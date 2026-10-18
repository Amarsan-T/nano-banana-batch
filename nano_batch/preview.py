"""Read-only prompt-file previews. No browser adapter or execution entry points."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Empty, Full, Queue

from .batch import PROMPT_WRAPPER_VARIANTS, load_prompts_file
from .errors import WAITING_FOR_FLOW_LIMIT_RESET
from .launcher import validate_run_folder_name
from .models import GenerationSettings
from .runner import SequentialBatchPlan, plan_sequential_batch


class PreviewState(str, Enum):
    PENDING = "Pending"
    COMPLETED = "Completed"
    DOWNLOAD_ONLY = "Download-only Resume"
    FINALIZE = "Finalization pending"
    BLOCKED = "Blocked / explicit Retry required"
    WAITING = "Waiting for Flow limit reset"
    RETRY = "Automatic retry eligible"


_GENERATION_LABELS = {
    "generate": "Generate (planned)",
    "skip_completed": "Skip saved generation",
    "resume_external_block": "Resume after limit reset",
    "resume_pre_start": "Resume; Start was not submitted",
    "auto_retry_policy": "Existing automatic policy retry",
    "auto_retry_generic_failure": "Existing automatic failure retry",
    "blocked_retry_required": "Blocked; no automatic submission",
}
_DOWNLOAD_LABELS = {
    "after_generation": "After generation",
    "skip_valid": "Keep validated assets",
    "resume": "Resume / reconcile saved downloads",
    "blocked": "Blocked",
}
_CANDIDATE_LABELS = {
    "download_after_generation": "after generation",
    "download": "download",
    "skip_valid": "keep validated asset",
    "recover_pending": "reconcile interrupted download",
}
ERROR_MESSAGES = {
    "prompt_path": "Choose an existing prompt text file.",
    "prompt_encoding": "The prompt file must be UTF-8. Save a UTF-8 copy and select it.",
    "prompt_content": "No valid prompts were found. Use one prompt per line; blank and # comment lines are ignored.",
    "run_path": "Enter a run folder path without quotes. For a new run, its parent folder must already exist.",
    "run_exists": "That folder already exists. Choose Existing run to preview it, or enter a fresh folder name.",
    "run_missing": "Choose an existing run folder containing run_state.json.",
    "plan_invalid": "Preview blocked: saved state, ownership, or assets could not be validated. Confirm the matching prompt file and run; use existing read-only CLI diagnostics for further review. Nothing was repaired.",
    "unavailable": "Preview could not read the selected inputs. Check local permissions and try again.",
    "changed": "Inputs or saved state changed. Preview again; the previous plan is no longer current.",
    "unexpected": "Preview is unavailable. No execution or repair was attempted.",
}


class PreviewError(Exception):
    def __init__(self, code: str):
        self.code = code if code in ERROR_MESSAGES else "unexpected"
        super().__init__(ERROR_MESSAGES[self.code])


@dataclass(frozen=True)
class PreviewRow:
    order: int
    state: PreviewState
    generation: str
    downloads: str
    candidates: str


@dataclass(frozen=True)
class PreviewView:
    rows: tuple[PreviewRow, ...]
    counts: tuple[tuple[PreviewState, int], ...] = field(init=False)

    def __post_init__(self) -> None:
        counts = {state: 0 for state in PreviewState}
        for row in self.rows:
            counts[row.state] += 1
        object.__setattr__(self, "counts", tuple(counts.items()))

    def count(self, state: PreviewState) -> int:
        return dict(self.counts)[state]


@dataclass(frozen=True)
class PreviewRequest:
    prompt_file: str
    run_dir: str
    existing: bool = False


@dataclass(frozen=True)
class PreviewResult:
    view: PreviewView
    fingerprint: tuple


def present_plan(plan: SequentialBatchPlan) -> PreviewView:
    """Project existing planner decisions, never prompts, visual IDs or file paths."""
    rows = []
    for entry in plan.orders:
        if type(entry.order) is not int or entry.order <= 0:
            raise PreviewError("plan_invalid")
        try:
            generation = _GENERATION_LABELS[entry.generation_action]
            downloads = _DOWNLOAD_LABELS[entry.download_action]
            candidates = []
            for variant, action in entry.candidate_actions:
                if type(variant) is not int or variant not in (1, 2):
                    raise PreviewError("plan_invalid")
                candidates.append(f"v{variant:02d}: {_CANDIDATE_LABELS[action]}")
        except (KeyError, TypeError):
            raise PreviewError("plan_invalid") from None
        state = (
            PreviewState.BLOCKED if entry.generation_action == "blocked_retry_required" else
            PreviewState.WAITING if entry.generation_status == WAITING_FOR_FLOW_LIMIT_RESET else
            PreviewState.COMPLETED if entry.generation_action == "skip_completed"
            and entry.download_action == "skip_valid" and entry.download_status == "completed" else
            PreviewState.FINALIZE if entry.generation_action == "skip_completed"
            and entry.download_action == "skip_valid" else
            PreviewState.DOWNLOAD_ONLY if entry.generation_action == "skip_completed" else
            PreviewState.RETRY if entry.generation_action in {"auto_retry_policy", "auto_retry_generic_failure"} else
            PreviewState.PENDING
        )
        rows.append(PreviewRow(entry.order, state, generation, downloads, "; ".join(candidates)))
    return PreviewView(tuple(rows))


def _paths(request: PreviewRequest) -> tuple[Path, Path]:
    if not request.prompt_file.strip() or "\0" in request.prompt_file:
        raise PreviewError("prompt_path")
    prompt = Path(request.prompt_file)
    if not prompt.is_file():
        raise PreviewError("prompt_path")
    if not request.run_dir.strip() or "\0" in request.run_dir:
        raise PreviewError("run_path")
    run = Path(request.run_dir)
    if request.existing:
        if not run.is_dir() or not (run / "run_state.json").is_file():
            raise PreviewError("run_missing")
    else:
        if run.exists() or run.is_symlink() or getattr(run, "is_junction", lambda: False)():
            raise PreviewError("run_exists")
        try:
            validate_run_folder_name(run.name)
        except ValueError:
            raise PreviewError("run_path") from None
        if not run.parent.is_dir():
            raise PreviewError("run_path")
    return prompt, run


def _file_fingerprint(path: Path) -> tuple:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, digest.digest()


def preview_fingerprint(request: PreviewRequest) -> tuple:
    """Read on the preview thread; never trust mtime/size alone for invalidation."""
    prompt, run = _paths(request)
    parent = run.resolve().parent.stat()
    run_info = run.stat() if request.existing else None
    identity = hashlib.sha256((str(prompt.resolve()) + "\0" + str(run.resolve())).encode("utf-8")).digest()
    return (
        _file_fingerprint(prompt),
        _file_fingerprint(run / "run_state.json") if request.existing else None,
        identity, parent.st_dev, parent.st_ino,
        (run_info.st_dev, run_info.st_ino) if run_info is not None else None,
    )


def evaluate_preview(request: PreviewRequest) -> PreviewResult:
    """Read exactly the production prompt-wrapper input and validated planner."""
    try:
        before = preview_fingerprint(request)
        try:
            batch = load_prompts_file(Path(request.prompt_file))
        except UnicodeError:
            raise PreviewError("prompt_encoding") from None
        except ValueError:
            raise PreviewError("prompt_content") from None
        try:
            plan = plan_sequential_batch(
                batch, Path(request.run_dir), GenerationSettings(variants=PROMPT_WRAPPER_VARIANTS),
            )
            view = present_plan(plan)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            raise PreviewError("plan_invalid") from None
        after = preview_fingerprint(request)
        if before != after:
            raise PreviewError("changed")
        return PreviewResult(view, after)
    except PreviewError:
        raise
    except (OSError, RuntimeError):
        raise PreviewError("unavailable") from None
    except Exception:
        raise PreviewError("unexpected") from None


@dataclass(frozen=True)
class PreviewMessage:
    revision: int
    kind: str
    view: PreviewView | None = None
    code: str | None = None


class PreviewController:
    """One read-only thread, one pending request and one sanitized result slot.

    The GUI owns revisions and drains messages on its main thread. The worker
    never receives a widget/callback. Monitoring detects input/state changes; it
    is not a filesystem lock or any authorization for future execution.
    """

    def __init__(self, *, evaluator=evaluate_preview, fingerprint=preview_fingerprint,
                 check_interval: float = 1.0):
        self._evaluate = evaluator
        self._fingerprint = fingerprint
        self._interval = check_interval
        self._requests: Queue = Queue(maxsize=1)
        self._results: Queue = Queue(maxsize=1)
        self._lock = threading.Lock()
        self._revision = 0
        self._closed = False
        self._thread = threading.Thread(target=self._work, name="read-only-preview", daemon=True)
        self._thread.start()

    @staticmethod
    def _latest(queue: Queue, value) -> None:
        try:
            queue.get_nowait()
        except Empty:
            pass
        try:
            queue.put_nowait(value)
        except Full:
            pass

    def request(self, request: PreviewRequest | None) -> int:
        with self._lock:
            self._revision += 1
            self._latest(self._results, None)
            if not self._closed:
                self._latest(self._requests, (self._revision, request))
            return self._revision

    def invalidate(self) -> int:
        return self.request(None)

    def drain(self) -> PreviewMessage | None:
        with self._lock:
            try:
                message = self._results.get_nowait()
            except Empty:
                return None
            return message if message is not None and message.revision == self._revision else None

    def _publish(self, message: PreviewMessage) -> None:
        with self._lock:
            if not self._closed and message.revision == self._revision:
                self._latest(self._results, message)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._revision += 1
            self._latest(self._requests, (self._revision, None))

    def _work(self) -> None:
        current = None
        while True:
            try:
                revision, request = self._requests.get(timeout=self._interval)
            except Empty:
                with self._lock:
                    if self._closed:
                        return
                if current is not None:
                    revision, request, token = current
                    try:
                        unchanged = self._fingerprint(request) == token
                    except Exception:
                        unchanged = False
                    if not unchanged:
                        self._publish(PreviewMessage(revision, "stale", code="changed"))
                        current = None
                continue
            with self._lock:
                if self._closed:
                    return
            current = None
            if request is None:
                continue
            try:
                result = self._evaluate(request)
                self._publish(PreviewMessage(revision, "ready", view=result.view))
                current = (revision, request, result.fingerprint)
            except PreviewError as exc:
                self._publish(PreviewMessage(revision, "error", code=exc.code))
            except Exception:
                self._publish(PreviewMessage(revision, "error", code="unexpected"))
