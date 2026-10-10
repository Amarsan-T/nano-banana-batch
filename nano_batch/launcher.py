from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Callable

from .batch import load_prompts_file
from .cancellation import format_cancellation_diagnostics, mark_recovery_menu_reached
from .downloads import CANONICAL_IMAGE_EXTENSIONS
from .errors import FLOW_POLICY_REJECTED
from .models import Batch, RunState
from .runner import (
    automatic_generic_failure_retry_available,
    edit_policy_rejected_prompt,
    exhausted_policy_rejection,
    proven_pre_start_not_submitted,
)
from .state import RunStateStore


class LauncherGenerationRetryRequired(RuntimeError):
    """Signal that the delegated production path requires explicit retry consent."""

    def __init__(self, orders: tuple[int, ...]) -> None:
        self.orders = orders
        order_label = ", ".join(str(order) for order in orders)
        super().__init__(f"Generation retry required for order(s) {order_label}.")


@dataclass(frozen=True)
class MatchingRun:
    run_dir: Path
    state: RunState
    completed_orders: int
    total_orders: int
    modified_ns: int

    @property
    def is_complete(self) -> bool:
        return self.total_orders > 0 and self.completed_orders == self.total_orders

    @property
    def summary(self) -> str:
        return f"{self.completed_orders}/{self.total_orders} completed"


def load_launcher_batch(prompts_path: Path) -> Batch:
    """Use the exact prompt-wrapper parser and semantic identity implementation."""
    return load_prompts_file(prompts_path)


def create_unique_run_dir(runs_dir: Path, *, now: datetime | None = None) -> Path:
    """Atomically reserve a new human-readable run directory without reuse."""
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"batch_{timestamp}"
    runs_dir.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        name = stem if suffix == 1 else f"{stem}_{suffix:02d}"
        candidate = runs_dir / name
        try:
            candidate.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return candidate.resolve()


def validate_run_folder_name(name: str) -> None:
    """Require one Windows-safe component without silently rewriting user input."""
    if not name.strip() or name in {".", ".."}:
        raise ValueError("Enter a non-empty folder name, not '.' or '..'.")
    if any(char in '<>:"/\\|?*' or ord(char) < 32 or 127 <= ord(char) <= 159 for char in name):
        raise ValueError("Paths, Windows-forbidden characters, and control characters are not allowed.")
    path = PureWindowsPath(name)
    if path.drive or path.root or path.parts != (name,):
        raise ValueError("The name must be one directory directly beneath runs/.")
    if name.endswith((".", " ")):
        raise ValueError("A folder name cannot end in a period or space.")
    device = name.split(".", 1)[0].strip(" ").upper()
    if re.fullmatch(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]", device):
        raise ValueError("Windows reserved device names, including forms with extensions, are not allowed.")
    try:
        length = len(name.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("The folder name contains invalid Unicode.") from exc
    if length > 255:
        raise ValueError("The folder name exceeds 255 Windows filename units.")


def create_named_run_dir(runs_dir: Path, name: str) -> Path:
    """Reserve one exact custom name; never reuse or suffix an existing entry."""
    validate_run_folder_name(name)
    root = runs_dir.resolve()
    candidate = root / name
    if candidate.resolve().parent != root:
        raise ValueError("The folder must remain directly beneath runs/.")
    root.mkdir(parents=True, exist_ok=True)
    candidate.mkdir()  # Atomic no-reuse check, including a concurrent creator.
    return candidate


def _choose_new_run_dir(runs_dir: Path, *, input_fn, output, now_fn) -> Path | None:
    output("Enter a batch folder name, or CANCEL to return to the main menu.")
    while True:
        name = input_fn("Batch folder name (blank = automatic): ")
        if name.strip().casefold() == "cancel":
            return None
        if name == "":
            return create_unique_run_dir(runs_dir, now=now_fn())
        try:
            return create_named_run_dir(runs_dir, name)
        except FileExistsError:
            output(f"A batch folder with that name already exists: {runs_dir / name}")
            output("Choose a different name, or CANCEL.")
        except ValueError as exc:
            output(f"Invalid batch folder name: {exc}")


def _completed_order_count(state: RunState) -> int:
    return sum(
        1
        for item in state.items
        if item.status == "completed" and item.download_status == "completed"
    )


def find_matching_runs(runs_dir: Path, batch: Batch) -> tuple[list[MatchingRun], list[MatchingRun]]:
    """Return compatible incomplete and completed immediate child runs, newest first."""
    matches: list[MatchingRun] = []
    if runs_dir.is_dir():
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            state_path = run_dir / "run_state.json"
            if not state_path.is_file():
                continue
            try:
                state = RunStateStore(state_path).load()
                state.counts()
            except (OSError, KeyError, TypeError, ValueError):
                continue
            if state.batch_id != batch.batch_id:
                continue
            total = len(state.items)
            matches.append(
                MatchingRun(
                    run_dir=run_dir.resolve(),
                    state=state,
                    completed_orders=_completed_order_count(state),
                    total_orders=total,
                    modified_ns=state_path.stat().st_mtime_ns,
                )
            )
    matches.sort(key=lambda match: (match.modified_ns, match.run_dir.name), reverse=True)
    incomplete = [match for match in matches if not match.is_complete]
    completed = [match for match in matches if match.is_complete]
    return incomplete, completed


def find_latest_assets_dir(runs_dir: Path) -> Path | None:
    """Find the newest immediate child run containing a canonical image."""
    canonical_suffixes = {
        f".{extension.lower()}" for extension in CANONICAL_IMAGE_EXTENSIONS.values()
    }
    if not runs_dir.is_dir():
        return None
    candidates = sorted(
        (path for path in runs_dir.iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for run_dir in candidates:
        assets_dir = run_dir / "assets"
        if not assets_dir.is_dir():
            continue
        if any(
            path.is_file() and path.suffix.lower() in canonical_suffixes
            for path in assets_dir.iterdir()
        ):
            return assets_dir.resolve()
    return None


def open_prompts_in_notepad(prompts_path: Path) -> None:
    subprocess.run(["notepad.exe", str(prompts_path.resolve())], check=False)


def open_assets_in_explorer(assets_dir: Path) -> None:
    subprocess.Popen(["explorer.exe", str(assets_dir.resolve())], shell=False)


def _read_current_batch(
    prompts_path: Path,
    *,
    output: Callable[[str], None],
) -> Batch | None:
    try:
        return load_launcher_batch(prompts_path)
    except FileNotFoundError:
        output("prompts.txt is missing. Use 'Open prompts.txt' to create it.")
    except (OSError, UnicodeError, ValueError) as exc:
        output(f"Cannot use prompts.txt: {exc}")
    return None


def _choose_incomplete_run(
    matches: list[MatchingRun],
    *,
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
) -> MatchingRun | None:
    if not matches:
        output("No incomplete run matches the current prompts.")
        return None
    if len(matches) == 1:
        match = matches[0]
        output(f"Matching run: {match.run_dir.name} ({match.summary})")
        return match

    output("Matching incomplete runs (newest first):")
    for index, match in enumerate(matches, start=1):
        output(f"  {index}. {match.run_dir.name} ({match.summary})")
    output("  0. Cancel")
    while True:
        choice = input_fn("Choose run: ").strip()
        if choice == "0":
            return None
        try:
            index = int(choice)
        except ValueError:
            index = -1
        if 1 <= index <= len(matches):
            return matches[index - 1]
        output("Please enter one of the listed numbers.")


def _report_batch_result(result: int, *, output: Callable[[str], None]) -> None:
    if result == 0:
        output("Batch completed successfully.")
    else:
        output(
            "Batch ended with an error. You can use Resume current prompts "
            "after resolving the issue."
        )


def retry_required_generation_orders(state: RunState) -> tuple[int, ...]:
    """Return generations the existing planner blocks without explicit retry."""
    return tuple(
        item.order
        for item in state.items
        if item.status in {"failed", "running"}
        and not proven_pre_start_not_submitted(item)
        and not (
            item.status == "failed"
            and item.generation_error_code == FLOW_POLICY_REJECTED
            and item.automatic_policy_retry_count == 0
        )
        and not automatic_generic_failure_retry_available(item)
    )


def _persisted_retry_required_generation_orders(run_dir: Path) -> tuple[int, ...]:
    """Read the durable failed/interrupted generations that require retry consent."""
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        return ()
    try:
        state = store.load()
        state.counts()
    except (OSError, KeyError, TypeError, ValueError):
        return ()
    return retry_required_generation_orders(state)


def _choose_retry_required_action(
    retry_orders: tuple[int, ...],
    *,
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    order_label = ", ".join(str(order) for order in retry_orders)
    noun = "Order" if len(retry_orders) == 1 else "Orders"
    output(f"Generation retry required for {noun} {order_label}.")
    output("")
    output("1. Retry failed generation")
    output("2. Return to main menu")
    output("3. Exit")
    while True:
        choice = input_fn("Choose: ").strip()
        action = {"1": "retry", "2": "main", "3": "exit"}.get(choice)
        if action is not None:
            return action
        output("Please choose 1, 2, or 3.")


def _policy_recovery_diagnostic(order: int, event: str, *, output) -> None:
    # Fixed event names and scalar identity only, never prompt/error/page content.
    output(f"POLICY RECOVERY: order={order} event={event} automatic_policy_retry_count=1")


def _read_replacement_prompt(item, *, input_fn, output) -> str | None:
    _policy_recovery_diagnostic(item.order, "prompt_edit_started", output=output)
    output(f"Current prompt for Order {item.order}:")
    output(item.prompt)
    output("Enter replacement prompt. Finish with END on its own line; CANCEL cancels.")
    lines = []
    while True:
        try:
            line = input_fn("")
        except EOFError:
            line = "CANCEL"
        if line == "CANCEL":
            break
        if line == "END":
            replacement = "\n".join(lines)
            if replacement.strip():
                return replacement
            break
        lines.append(line)
    _policy_recovery_diagnostic(item.order, "prompt_edit_cancelled", output=output)
    return None


def choose_policy_recovery_action(
    batch: Batch, run_dir: Path, order: int, *, input_fn=input, output=print,
) -> str:
    """Policy-only CLI UI; edits state atomically but never submits or authorizes Start."""
    store = RunStateStore(run_dir / "run_state.json")
    menu_reported = False
    while True:
        state = store.load()
        source = next((item for item in batch.items if item.order == order), None)
        targets = [item for item in state.items if item.order == order]
        if (state.batch_id != batch.batch_id or len(targets) != 1 or source is None
                or not targets[0].matches_source(source)
                or not exhausted_policy_rejection(targets[0])):
            raise ValueError("Policy recovery state is no longer eligible; no retry was started.")
        item = targets[0]
        if not menu_reported:
            _policy_recovery_diagnostic(order, "policy_recovery_menu_reached", output=output)
            menu_reported = True
        output(f"Policy rejection persists for Order {order}.")
        output("1. Retry failed generation")
        output("2. Edit this Order's prompt and retry")
        output("3. Return to main menu")
        output("4. Exit")
        try:
            choice = input_fn("Choose: ").strip()
        except EOFError:
            return "main"
        if choice in {"1", "3", "4"}:
            return {"1": "retry", "3": "main", "4": "exit"}[choice]
        if choice == "2":
            replacement = _read_replacement_prompt(item, input_fn=input_fn, output=output)
            if replacement is None:
                continue
            edit_policy_rejected_prompt(batch, run_dir, order, replacement)
            _policy_recovery_diagnostic(order, "edited_prompt_applied", output=output)
            return "edit_retry"
        output("Please choose 1, 2, 3, or 4.")


def _choose_generation_recovery_action(
    prompts_path: Path, run_dir: Path, retry_orders: tuple[int, ...], *, input_fn, output,
) -> str:
    # Keep Ctrl+C's existing menu separate. Only ordinary exhausted policy failures
    # reach the new editor, including explicit Resume of the same persisted run.
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        return _choose_retry_required_action(retry_orders, input_fn=input_fn, output=output)
    state = store.load()
    target = next((item for item in state.items if item.order in retry_orders), None)
    if len(retry_orders) == 1 and target is not None and exhausted_policy_rejection(target):
        return choose_policy_recovery_action(
            load_launcher_batch(prompts_path), run_dir, target.order,
            input_fn=input_fn, output=output,
        )
    return _choose_retry_required_action(retry_orders, input_fn=input_fn, output=output)


def _choose_interruption_recovery(
    run_dir: Path,
    *,
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    """Offer only state-authorized actions after one launcher batch interrupt."""
    retry_orders = _persisted_retry_required_generation_orders(run_dir)
    output("")
    output("Batch interrupted by user.")
    output("")
    if retry_orders:
        return _choose_retry_required_action(
            retry_orders,
            input_fn=input_fn,
            output=output,
        )
    output("1. Resume current run")
    output("2. Return to main menu")
    output("3. Exit")
    valid = {"1": "resume", "2": "main", "3": "exit"}
    while True:
        choice = input_fn("Choose: ").strip()
        action = valid.get(choice)
        if action is not None:
            return action
        output(f"Please choose {', '.join(valid)}.")


def _run_batch_interactively(
    prompts_path: Path,
    run_dir: Path,
    execute_prompts_file: Callable[[Path, Path, bool], int],
    *,
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
    initial_retry_required_orders: tuple[int, ...] = (),
) -> int | None:
    retry = False
    if initial_retry_required_orders:
        action = _choose_generation_recovery_action(
            prompts_path, run_dir, initial_retry_required_orders,
            input_fn=input_fn,
            output=output,
        )
        if action == "main":
            return None
        if action == "exit":
            return 0
        if action == "edit_retry":
            order = initial_retry_required_orders[0]
            _policy_recovery_diagnostic(order, "edited_prompt_retry_started", output=output)
        retry = True
    while True:
        try:
            result = int(execute_prompts_file(prompts_path, run_dir, retry))
        except KeyboardInterrupt as exc:
            mark_recovery_menu_reached(exc)
            diagnostics = format_cancellation_diagnostics(exc)
            if diagnostics:
                output("")
                for line in diagnostics.splitlines():
                    output(line)
            action = _choose_interruption_recovery(
                run_dir,
                input_fn=input_fn,
                output=output,
            )
            if action == "resume":
                retry = False
                continue
            if action == "retry":
                retry = True
                continue
            if action == "main":
                return None
            return 130
        except LauncherGenerationRetryRequired as exc:
            action = _choose_generation_recovery_action(
                prompts_path, run_dir, exc.orders,
                input_fn=input_fn,
                output=output,
            )
            if action == "main":
                return None
            if action == "exit":
                return 0
            if action == "edit_retry":
                _policy_recovery_diagnostic(exc.orders[0], "edited_prompt_retry_started", output=output)
            retry = True
            continue

        if result == 130:
            return result
        _report_batch_result(result, output=output)
        return None


def run_launcher(
    project_dir: Path,
    execute_prompts_file: Callable[[Path, Path, bool], int],
    *,
    launch_chrome: Callable[[], int] | None = None,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    now_fn: Callable[[], datetime] = datetime.now,
) -> int:
    """Run the interactive Windows convenience menu without starting work implicitly."""
    project_dir = project_dir.resolve()
    prompts_path = project_dir / "prompts.txt"
    runs_dir = project_dir / "runs"

    try:
        while True:
            try:
                current_batch = load_launcher_batch(prompts_path)
                prompt_status = str(len(current_batch.items))
            except FileNotFoundError:
                current_batch = None
                prompt_status = "unavailable (prompts.txt is missing)"
            except (OSError, UnicodeError, ValueError) as exc:
                current_batch = None
                prompt_status = f"0 ({exc})"

            output("")
            output("Nano Banana Batch")
            output(f"Prompts file: {prompts_path.resolve()}")
            output(f"Accepted prompts: {prompt_status}")
            if current_batch is not None:
                _incomplete, completed = find_matching_runs(runs_dir, current_batch)
                if completed:
                    output(f"Latest completed matching run: {completed[0].run_dir.name}")
            output("")
            output("1. Launch Chrome")
            output("2. Start new batch")
            output("3. Resume current prompts")
            output("4. Open prompts.txt")
            output("5. Open latest images")
            output("6. Exit")
            output("")
            choice = input_fn("Choose: ").strip()

            if choice == "1":
                if launch_chrome is None:
                    output("Dedicated Chrome launch is unavailable.")
                    continue
                try:
                    result = int(launch_chrome())
                except Exception as exc:
                    output(f"Could not launch dedicated Chrome: {exc}")
                    continue
                if result == 0:
                    output("Dedicated Chrome is ready.")
                else:
                    output(f"Could not launch dedicated Chrome (exit code {result}).")
                continue

            if choice == "2":
                batch = _read_current_batch(prompts_path, output=output)
                if batch is None:
                    continue
                run_dir = _choose_new_run_dir(
                    runs_dir, input_fn=input_fn, output=output, now_fn=now_fn,
                )
                if run_dir is None:
                    continue
                output(f"Starting new batch in: {run_dir}")
                result = _run_batch_interactively(
                    prompts_path.resolve(),
                    run_dir,
                    execute_prompts_file,
                    input_fn=input_fn,
                    output=output,
                )
                if result is not None:
                    return result
                continue

            if choice == "3":
                batch = _read_current_batch(prompts_path, output=output)
                if batch is None:
                    continue
                incomplete, _completed = find_matching_runs(runs_dir, batch)
                selected = _choose_incomplete_run(
                    incomplete,
                    input_fn=input_fn,
                    output=output,
                )
                if selected is None:
                    continue
                output(f"Resuming: {selected.run_dir} ({selected.summary})")
                retry_orders = retry_required_generation_orders(selected.state)
                result = _run_batch_interactively(
                    prompts_path.resolve(),
                    selected.run_dir,
                    execute_prompts_file,
                    input_fn=input_fn,
                    output=output,
                    initial_retry_required_orders=retry_orders,
                )
                if result is not None:
                    return result
                continue

            if choice == "4":
                try:
                    open_prompts_in_notepad(prompts_path)
                except OSError as exc:
                    output(f"Could not open Notepad: {exc}")
                continue

            if choice == "5":
                assets_dir = find_latest_assets_dir(runs_dir)
                if assets_dir is None:
                    output("No generated images were found.")
                    continue
                try:
                    open_assets_in_explorer(assets_dir)
                    output(f"Opened: {assets_dir}")
                except OSError as exc:
                    output(f"Could not open File Explorer: {exc}")
                continue

            if choice == "6":
                return 0

            output("Please choose 1, 2, 3, 4, 5, or 6.")
    except KeyboardInterrupt:
        output("\nLauncher cancelled.")
        return 130
