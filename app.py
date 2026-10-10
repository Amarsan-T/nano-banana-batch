from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from nano_batch.batch import (
    PROMPT_WRAPPER_VARIANTS,
    load_batch,
    load_prompts_file,
    single_prompt_batch,
)
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.errors import PolicyGenerationRejected, TemporaryGenerationBlock, WAITING_FOR_FLOW_LIMIT_RESET
from nano_batch.models import Batch, GenerationSettings
from nano_batch.launcher import (
    LauncherGenerationRetryRequired,
    choose_policy_recovery_action,
    retry_required_generation_orders,
    run_launcher,
)
from nano_batch.power import windows_execution_state_guard
from nano_batch.runner import (
    dry_run,
    execute_download_one,
    execute_one,
    execute_repair_order_candidates,
    execute_sequential_batch,
    exhausted_policy_rejection,
    initialize_run_state,
    plan_download_one,
    plan_repair_order_candidates,
    plan_sequential_batch,
    prepare_persisted_flow_project,
    preview_one,
    summarize_state,
)
from nano_batch.state import RunStateStore
from nano_batch.adapters.flow import FLOW_HOME_URL, FlowSession


def _default_run_dir(batch_id: str) -> Path:
    return Path("runs") / batch_id.split(":", 1)[-1][:16]


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a JSON object.")
    return raw


def _flow_session_from_args(args: argparse.Namespace, config: dict) -> FlowSession:
    flow_url = (
        getattr(args, "url", None)
        or config.get("flow_url")
        or "https://flow.google.com"
    )
    profile_dir = Path(
        getattr(args, "profile_dir", None)
        or config.get("browser_profile_dir", ".flow_chrome_profile")
    )
    debug_address = getattr(args, "debug_address", None) or config.get(
        "debug_address", "127.0.0.1"
    )
    debug_port = getattr(args, "debug_port", None) or config.get("debug_port", 9222)
    diagnostics_path = Path(
        getattr(args, "screenshot", None)
        or config.get("diagnostics_path", "diagnostics/flow_inspect.png")
    )
    prestart_timing_debug = config.get("prestart_timing_debug", False)
    if not isinstance(prestart_timing_debug, bool):
        raise ValueError("prestart_timing_debug must be true or false.")
    return FlowSession(
        flow_url=flow_url,
        profile_dir=profile_dir,
        debug_address=str(debug_address),
        debug_port=int(debug_port),
        diagnostics_path=diagnostics_path,
        prestart_timing_debug=prestart_timing_debug,
    )


def _generation_settings(
    config: dict,
    timeout_override: int | None = None,
    *,
    variants_override: int | None = None,
) -> GenerationSettings:
    raw = config.get("generation") or {}
    if not isinstance(raw, dict):
        raise ValueError("generation configuration must be a JSON object.")
    settings = GenerationSettings(
        mode=str(raw.get("mode", "image")),
        aspect_ratio=str(raw.get("aspect_ratio", "16:9")),
        variants=int(
            variants_override
            if variants_override is not None
            else raw.get("variants", 2)
        ),
        preferred_model_hint=raw.get("preferred_model_hint"),
        result_timeout_seconds=int(
            timeout_override
            if timeout_override is not None
            else raw.get("result_timeout_seconds", 300)
        ),
    )
    settings.validate()
    return settings


def cmd_inspect(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    print(f"Batch ID: {batch.batch_id}")
    print(f"Source schema: {batch.source_schema}")
    print(f"Items: {len(batch.items)}")
    if batch.source_batch_revision:
        print(f"Source batch revision: {batch.source_batch_revision}")
    if batch.source_visual_manifest_revision:
        print(f"Source visual manifest revision: {batch.source_visual_manifest_revision}")
    print()
    for item in batch.items:
        preview = item.prompt[:140] + ("..." if len(item.prompt) > 140 else "")
        print(f"{item.order:04d}  {item.visual_id}")
        print(f"      {preview}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)

    if args.dry_run:
        dry_run(batch, max_items=args.max_items)
        print("\nDRY RUN ONLY — no browser interaction and no generation.")
        return 0

    state = initialize_run_state(batch, run_dir)
    print(f"Initialized/reused run state: {run_dir / 'run_state.json'}")
    print(summarize_state(state))
    print("\nThis command only initializes state; it does not submit prompts.")
    print("Use `python app.py generate-one <batch> --order N --preview` for the one-item path.")
    return 0


def _print_sequential_batch_plan(plan) -> None:
    generation_labels = {
        "skip_completed": "completed -> skip",
        "generate": "pending -> generate",
        "resume_pre_start": "pre-Start/not-submitted -> resume safely",
        "resume_external_block": "Flow limit wait -> resume when available",
        "auto_retry_policy": "Flow policy rejection -> automatic retry 1/1",
        "retry": "failed/interrupted -> retry",
        "blocked_retry_required": "failed/interrupted -> blocked (requires --retry)",
    }
    download_labels = {
        "skip_valid": "complete and valid -> skip",
        "resume": "incomplete -> resume",
        "after_generation": "after generation -> download expected candidate(s)",
        "blocked": "blocked until generation retry is authorized",
    }
    for order in plan.orders:
        print(f"Order {order.order} — {order.visual_id}")
        print(
            "  generation: "
            + generation_labels.get(
                order.generation_action,
                f"{order.generation_status} -> {order.generation_action}",
            )
        )
        print(
            "  downloads: "
            + download_labels.get(
                order.download_action,
                f"{order.download_status} -> {order.download_action}",
            )
        )
        if order.candidate_actions:
            actions = ", ".join(
                f"v{variant:02d}={action}"
                for variant, action in order.candidate_actions
            )
            print(f"  candidates: {actions}")
        print("  download quality: 1K Original size")


def _report_policy_retry_event(order: int, event: str) -> None:
    if event == "automatic_retry_started":
        print(f"Order {order}: Flow policy rejection.")
        print("Automatic retry 1/1...")
        print(f"POLICY RECOVERY: order={order} event=policy_rejection_detected automatic_policy_retry_count=1")
        print(f"POLICY RECOVERY: order={order} event=automatic_policy_retry_attempted automatic_policy_retry_count=1")
    elif event == "automatic_retry_succeeded":
        print(f"Order {order}: retry succeeded.")
    elif event == "automatic_retry_exhausted":
        print(f"Order {order}: Flow policy rejection repeated.")
        print("Automatic policy retry exhausted.")
        print("Manual retry required.")
        print(f"POLICY RECOVERY: order={order} event=policy_rejection_detected automatic_policy_retry_count=1")


def _recorded_flow_model(result_details: str | None) -> str | None:
    """Read only a short, known-safe model label from adapter result metadata."""
    if not result_details:
        return None
    try:
        details = json.loads(result_details)
    except (TypeError, ValueError):
        return None
    model = details.get("model") if isinstance(details, dict) else None
    if not isinstance(model, str):
        return None
    model = " ".join(model.split())
    if len(model) > 80 or not re.fullmatch(
        r"(?:unknown"
        r"|Nano Banana(?: (?:\d+(?:\.\d+)?(?: (?:Lite|Pro|Flash))?"
        r"|Lite|Pro|Flash))?"
        r"|Imagen(?: \d+(?:\.\d+)?)?"
        r"|Gemini(?: \d+(?:\.\d+)?)?)",
        model,
        re.I,
    ):
        return None
    return model


def _run_sequential_command(
    batch: Batch,
    args: argparse.Namespace,
    *,
    intro_lines: tuple[str, ...] = (),
    prompt_wrapper: bool = False,
) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    config = _load_config(args.config)
    settings = _generation_settings(
        config,
        args.timeout,
        variants_override=PROMPT_WRAPPER_VARIANTS if prompt_wrapper else None,
    )
    plan = plan_sequential_batch(
        batch,
        run_dir,
        settings,
        retry_failed_generation=args.retry,
    )

    for line in intro_lines:
        print(line)
    if prompt_wrapper:
        print(f"Run directory: {plan.run_dir}")
        print(f"Expected Flow variants: x{settings.variants} (set manually)")
    print(f"Batch ID: {batch.batch_id}")
    order_chain = " -> ".join(str(order.order) for order in plan.orders)
    print(f"Sequential batch scope: {len(plan.orders)} order(s) ({order_chain})")
    print(f"Run state: {plan.run_dir / 'run_state.json'}")
    print()
    _print_sequential_batch_plan(plan)

    if args.preview:
        print(
            "\nPREVIEW ONLY — no browser interaction, prompt submission, generation, "
            "download, state mutation, or credit use."
        )
        return 0

    retry_failed_generation = args.retry
    blocked_orders = [entry.order for entry in plan.orders
                      if getattr(entry, "generation_action", None) == "blocked_retry_required"]
    if len(blocked_orders) == 1 and not getattr(args, "_launcher", False):
        state = RunStateStore(run_dir / "run_state.json").load()
        target = next(item for item in state.items if item.order == blocked_orders[0])
        if exhausted_policy_rejection(target):
            action = choose_policy_recovery_action(batch, run_dir, target.order, input_fn=input)
            if action in {"main", "exit"}:
                return 0 if action == "exit" else 2
            if action == "edit_retry":
                print(f"POLICY RECOVERY: order={target.order} event=edited_prompt_retry_started automatic_policy_retry_count=1")
            retry_failed_generation = True

    adapter = _flow_session_from_args(args, config)

    reported_models: set[str] = set()

    def report_completed(outcome) -> None:
        print(
            f"Order {outcome.order}: COMPLETE "
            f"(generation={outcome.generation}, downloads={outcome.downloads})"
        )
        completed_item = None
        try:
            completed_state = RunStateStore(
                run_dir.resolve() / "run_state.json"
            ).load()
            completed_item = next(
                item for item in completed_state.items if item.order == outcome.order
            )
            model = _recorded_flow_model(completed_item.result_details)
        except (OSError, KeyError, StopIteration, TypeError, ValueError):
            model = None
        if model is not None and model.casefold() not in reported_models:
            reported_models.add(model.casefold())
            print(f"Current Flow model: {model}")
        if config.get("prestart_timing_debug", False) and outcome.generation != "skipped_completed":
            try:
                details = json.loads(getattr(completed_item, "result_details", None) or "{}")
                timing = details.get("post_start_ack_timing")
                if isinstance(timing, dict):
                    print(FlowSession._format_order_latency_summary(outcome.order, timing))
                    print(FlowSession._format_ack_poll_breakdown(timing))
                    print(FlowSession._format_observer_read_breakdown(timing))
                    print(FlowSession._format_event_loop_vs_cdp(timing))
                    print(FlowSession._format_generation_network_timing(timing))
            except (AttributeError, TypeError, ValueError):
                pass  # Optional persisted telemetry must not fail a completed Order.

    while True:
        try:
            with authoritative_cancellation_scope():
                with windows_execution_state_guard(enabled=isinstance(adapter, FlowSession)):
                    final_state, outcomes = execute_sequential_batch(
                        batch,
                        run_dir,
                        adapter,
                        settings,
                        retry_failed_generation=retry_failed_generation,
                        order_completed=report_completed,
                        policy_retry_event=_report_policy_retry_event,
                        manage_flow_project=isinstance(adapter, FlowSession),
                    )
            break
        except PolicyGenerationRejected as exc:
            if getattr(args, "_launcher", False):
                raise  # The launcher owns main-menu/Exit routing.
            state = RunStateStore(run_dir / "run_state.json").load()
            target = next((item for item in state.items if item.order == exc.order), None)
            if target is None or not exhausted_policy_rejection(target):
                raise
            print(f"ERROR: {exc.code} for Order {target.order}", file=sys.stderr)
            action = choose_policy_recovery_action(
                batch, run_dir, target.order, input_fn=input,
            )
            if action in {"main", "exit"}:
                return 0 if action == "exit" else 2
            if action == "edit_retry":
                print(f"POLICY RECOVERY: order={target.order} event=edited_prompt_retry_started automatic_policy_retry_count=1")
            retry_failed_generation = True
        except TemporaryGenerationBlock as exc:
            store = RunStateStore(run_dir.resolve() / "run_state.json")
            paused_state = store.load()
            completed = sum(
                1
                for item in paused_state.items
                if item.status == "completed" and item.download_status == "completed"
            )
            order = exc.order if exc.order is not None else "unknown"
            print(f"\nBatch paused: {exc.code}")
            print(f"Order: {order}")
            print(f"Completed: {completed}/{len(paused_state.items)}")
            print("No later orders were started.")
            print("No automatic Flow retry was attempted.")
            print("Run the same command again after Flow usage becomes available.")
            return 3
    print(f"\nSequential run completed: {len(outcomes)} orders")
    print(summarize_state(final_state))
    if prompt_wrapper:
        print("Canonical assets:")
        for item in final_state.items:
            for candidate in sorted(item.candidates, key=lambda value: value.variant):
                print(
                    f"  Order {item.order} {item.visual_id} "
                    f"v{candidate.variant:02d}: {candidate.local_path}"
                )
    return 0


def cmd_run_batch(args: argparse.Namespace) -> int:
    return _run_sequential_command(load_batch(Path(args.batch)), args)


def _configure_unicode_cli_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def cmd_run_prompt(args: argparse.Namespace) -> int:
    _configure_unicode_cli_output()
    batch = single_prompt_batch(args.prompt)
    return _run_sequential_command(
        batch,
        args,
        intro_lines=("Order: 1", f"Prompt: {batch.items[0].prompt}"),
        prompt_wrapper=True,
    )


def cmd_run_prompts_file(args: argparse.Namespace) -> int:
    _configure_unicode_cli_output()
    prompts_path = Path(args.prompts_file)
    batch = load_prompts_file(prompts_path)
    return _run_sequential_command(
        batch,
        args,
        intro_lines=(
            f"Prompts file: {prompts_path.resolve()}",
            f"Accepted prompts/orders: {len(batch.items)}",
        ),
        prompt_wrapper=True,
    )


def cmd_launcher(args: argparse.Namespace) -> int:
    _configure_unicode_cli_output()
    project_dir = Path(__file__).resolve().parent

    def retry_snapshot(
        prompts_path: Path,
        run_dir: Path,
    ) -> tuple[tuple[int, ...], bytes | None]:
        try:
            batch = load_prompts_file(prompts_path)
            state_path = run_dir.resolve() / "run_state.json"
            store = RunStateStore(state_path)
            if not store.exists():
                return (), None
            state = store.load()
            if state.batch_id != batch.batch_id or len(state.items) != len(batch.items):
                return (), state_path.read_bytes()
            if any(
                not state_item.matches_source(batch_item)
                for state_item, batch_item in zip(state.items, batch.items)
            ):
                return (), state_path.read_bytes()
            orders = retry_required_generation_orders(state)
            return orders, state_path.read_bytes()
        except (OSError, KeyError, TypeError, UnicodeError, ValueError):
            return (), None

    def execute_prompts_file(
        prompts_path: Path,
        run_dir: Path,
        retry: bool,
    ) -> int:
        _before_orders, before_revision = retry_snapshot(prompts_path, run_dir)
        try:
            return cmd_run_prompts_file(
                argparse.Namespace(
                    prompts_file=str(prompts_path),
                    run_dir=str(run_dir),
                    preview=False,
                    retry=retry,
                    timeout=None,
                    config=args.config,
                    url=args.url,
                    profile_dir=args.profile_dir,
                    debug_address=args.debug_address,
                    debug_port=args.debug_port,
                    _launcher=True,
                )
            )
        except Exception as exc:
            after_orders, after_revision = retry_snapshot(prompts_path, run_dir)
            blocked_message = (
                f"Generation retry required for order(s) "
                f"{', '.join(str(order) for order in after_orders)}; "
                "rerun run-batch with --retry."
            )
            retry_state_changed = (
                after_revision is not None and after_revision != before_revision
            )
            blocked_by_existing_semantics = (
                not retry
                and bool(after_orders)
                and isinstance(exc, ValueError)
                and str(exc) == blocked_message
            )
            if after_orders and (
                retry_state_changed or blocked_by_existing_semantics
            ):
                raise LauncherGenerationRetryRequired(after_orders) from exc
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    def launch_chrome() -> int:
        launch_args = argparse.Namespace(**vars(args))
        launch_args.url = FLOW_HOME_URL
        return cmd_open_flow_debug(launch_args)

    return run_launcher(
        project_dir,
        execute_prompts_file,
        launch_chrome=launch_chrome,
    )


def cmd_status(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        print("No persisted run state exists for this batch.")
        print(f"Expected: {store.path}")
        return 1
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different batch.")
    print(summarize_state(state))
    return 0


def _format_order_scope(orders: tuple[int, ...]) -> str:
    if not orders:
        return "none"
    if orders == tuple(range(orders[0], orders[-1] + 1)):
        return str(orders[0]) if len(orders) == 1 else f"{orders[0]}-{orders[-1]}"
    return ", ".join(str(order) for order in orders)


def _print_order_candidate_repair_plan(plan) -> None:
    item = plan.item
    print(f"Order {item.order} — {item.visual_id}")
    print("generation:")
    print(f"  {item.status} -> failed/retry-required")
    print("candidate UUIDs:")
    for variant, candidate_id in plan.candidate_ids:
        print(f"  v{variant:02d} {candidate_id} -> cleared")
    print("ownership conflicts:")
    for candidate_id, owners in plan.conflicts:
        owner_text = ", ".join(
            f"order {order} v{variant:02d}" for order, variant in owners
        )
        print(f"  {candidate_id}: also owned by {owner_text}")
    print("candidate/output list:")
    print("  candidates, output_files, and result_details -> cleared")
    print("downloads:")
    print("  untrusted candidate associations -> cleared")
    print("  item-level download status, attempts, error, and timestamps -> preserved")
    print("generation attempts:")
    print(f"  remains {item.attempt_count}")
    print("generation timestamps:")
    print("  started_at and completed_at -> preserved")
    print(f"Orders {_format_order_scope(plan.earlier_orders)} before target:")
    print("  unchanged")
    print(f"Orders {_format_order_scope(plan.later_orders)} after target:")
    print("  unchanged")
    print("Canonical assets:")
    print(f"  unchanged ({len(plan.canonical_assets)} file(s))")


def cmd_repair_order_candidates(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_repair_order_candidates(batch, run_dir, args.order)
    print(f"Batch ID: {batch.batch_id}")
    print(f"Run state: {run_dir.resolve() / 'run_state.json'}")
    _print_order_candidate_repair_plan(plan)
    if args.preview:
        print("\nPREVIEW ONLY — zero state or asset mutation.")
        return 0

    execute_repair_order_candidates(batch, run_dir, args.order)
    print("\nREPAIR APPLIED atomically. No canonical assets were changed.")
    print(
        f"Order {args.order} now requires explicit run-batch --retry regeneration."
    )
    return 0


def cmd_open_flow(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    _flow_session_from_args(args, config).open_for_manual_reconnaissance()
    return 0


def cmd_open_flow_debug(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    _flow_session_from_args(args, config).open_for_debugging()
    return 0


def cmd_inspect_flow(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    settings = _generation_settings(config)
    with _flow_session_from_args(args, config) as session:
        inspection = session.print_environment_report()
        mode_detection = session.detect_image_mode(settings, inspection=inspection)
    print("\nMode detection:")
    print(
        json.dumps(
            {"mode_detection": mode_detection.to_dict()},
            ensure_ascii=True,
            indent=2,
        )
    )
    print("\nREAD-ONLY INSPECTION — no prompt was submitted and no credits were spent.")
    return 0


def cmd_inspect_flow_project_ui(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    session.open_read_only_project_reconnaissance()
    try:
        report = session.inspect_project_ui_controls(limit=args.limit)
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    print(
        "\nREAD-ONLY PROJECT UI INSPECTION — no click, hover, keyboard input, "
        "navigation, screenshot, or Flow mutation was performed."
    )
    return 0


def cmd_inspect_current_flow_project(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    session.open_read_only_project_reconnaissance()
    try:
        report = session.inspect_current_flow_project(dialog_limit=args.limit)
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    print(
        "\nREAD-ONLY CURRENT PROJECT INSPECTION — no click, hover, keyboard input, "
        "navigation, screenshot, or Flow mutation was performed."
    )
    return 0


def cmd_inspect_flow_download_layout(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    session.open_read_only_project_reconnaissance()
    try:
        report = session.inspect_flow_download_layout(candidate_limit=args.limit)
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    print(
        "\nREAD-ONLY DOWNLOAD LAYOUT INSPECTION — no click, hover, keyboard input, "
        "navigation, window manipulation, screenshot, or Flow mutation was performed."
    )
    return 0


def cmd_inspect_flow_model_menu(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    session.open_read_only_project_reconnaissance()
    try:
        report = session.inspect_flow_model_menu(control_limit=args.limit)
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    print(
        "\nREAD-ONLY MODEL MENU INSPECTION — no click, hover, keyboard input, "
        "navigation, model change, screenshot, focus, window manipulation, or "
        "Flow mutation was performed."
    )
    return 0


def _print_existing_preview_state(batch, item, run_dir: Path) -> None:
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        print("Persisted item state: none (preview will not create it)")
        return
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different batch.")
    state_item = next(
        (
            candidate
            for candidate in state.items
            if candidate.order == item.order and candidate.visual_id == item.visual_id
        ),
        None,
    )
    if state_item is None or not state_item.matches_source(item):
        raise ValueError("Run state item identity does not match the source batch.")
    print(f"Persisted item status: {state_item.status}")
    print(f"Previous attempts: {state_item.attempt_count}")
    if state_item.error:
        print(f"Last error: {state_item.error}")
    if state_item.status == WAITING_FOR_FLOW_LIMIT_RESET:
        print("This external wait state resumes without --retry once Flow usage is available.")
    if state_item.status in {"failed", "running"}:
        print("Future real execution requires --retry; preview does not change this state.")


def cmd_generate_one(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    config = _load_config(args.config)
    settings = _generation_settings(config, args.timeout)
    item = preview_one(batch, args.order, settings)
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)

    print(f"Batch ID: {batch.batch_id}")
    print(f"Order: {item.order}")
    print(f"Visual ID: {item.visual_id}")
    print(f"Prompt: {item.prompt}")
    print(f"Mode: {settings.mode}")
    print(f"Aspect ratio: {settings.aspect_ratio}")
    print(f"Requested variants: {settings.variants}")
    print("Flow model: user-controlled (the currently selected image model is used)")
    print(f"Run state: {run_dir / 'run_state.json'}")

    if args.preview:
        _print_existing_preview_state(batch, item, run_dir)
        if args.download:
            print("Post-generation download requested; preview will not generate or download.")
        print("\nPREVIEW ONLY — no browser interaction, state mutation, generation, or credit use.")
        return 0

    adapter = _flow_session_from_args(args, config)
    if isinstance(adapter, FlowSession):
        prepare_persisted_flow_project(batch, run_dir, adapter, settings)
    _, state_item = execute_one(
        batch,
        run_dir,
        args.order,
        adapter,
        settings,
        retry=args.retry,
    )
    print(f"\nCompleted: {state_item.visual_id}")
    print(f"Attempt count: {state_item.attempt_count}")
    print(f"Candidates discovered: {len(state_item.candidates)}")
    for candidate in state_item.candidates:
        print(f"  v{candidate.variant:02d}: {candidate.suggested_filename}")
        print(f"       signal: {candidate.signal or candidate.source_kind}")
    if not state_item.output_files and not args.download:
        print("No files were downloaded; candidate metadata was persisted without guessing a download path.")
    if args.download:
        _, state_item = execute_download_one(
            batch,
            run_dir,
            args.order,
            adapter,
        )
        print(f"Downloaded candidates: {len(state_item.output_files)}")
        for output_file in state_item.output_files:
            print(f"  {output_file}")
    return 0


def cmd_download_one(args: argparse.Namespace) -> int:
    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_download_one(
        batch,
        run_dir,
        args.order,
        overwrite=args.overwrite,
    )

    print(f"Batch ID: {batch.batch_id}")
    print(f"Order: {plan.item.order}")
    print(f"Visual ID: {plan.item.visual_id}")
    print(f"Generation status: {plan.item.status}")
    print(f"Download status: {plan.item.download_status}")
    print(f"Download attempts: {plan.item.download_attempt_count}")
    print(f"Asset directory: {plan.asset_dir}")
    for candidate_plan in plan.candidates:
        candidate = candidate_plan.candidate
        print(f"Candidate v{candidate.variant:02d}:")
        print(f"  persisted planned name: {candidate.suggested_filename}")
        print(f"  persisted identity: {candidate.candidate_id or candidate.asset_url}")
        print("  UI: More options")
        print("      Download submenu")
        print("      1K Original size")
        print(f"  action: {candidate_plan.action}")
        destination = (
            candidate_plan.destination
            if candidate_plan.action == "skip_valid"
            else candidate_plan.destination_pattern
        )
        print(f"  destination: {destination}")

    if args.preview:
        print(
            "\nPREVIEW ONLY — no browser interaction, clicks, downloads, generation, "
            "state mutation, or credit use."
        )
        return 0

    config = _load_config(args.config)
    adapter = _flow_session_from_args(args, config)
    if isinstance(adapter, FlowSession):
        prepare_persisted_flow_project(batch, run_dir, adapter, None)
    _, state_item = execute_download_one(
        batch,
        run_dir,
        args.order,
        adapter,
        overwrite=args.overwrite,
    )
    print(f"\nDownload status: {state_item.download_status}")
    print(f"Validated local candidates: {len(state_item.output_files)}")
    for candidate in state_item.candidates:
        if candidate.download_status != "completed":
            continue
        print(f"  v{candidate.variant:02d}: {candidate.local_path}")
        print(
            f"       {candidate.image_format} {candidate.width}x{candidate.height}, "
            f"{candidate.byte_size} bytes, sha256={candidate.sha256}"
        )
    return 0


def cmd_trace_download_trigger(args: argparse.Namespace) -> int:
    if args.variant != 1:
        raise ValueError("This bounded diagnostic is restricted to candidate v01.")
    trace_label = getattr(args, "trace_label", None)
    if trace_label and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", trace_label):
        raise ValueError("--trace-label must be 1-40 ASCII letters, numbers, hyphens, or underscores.")
    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_download_one(batch, run_dir, args.order, overwrite=False)
    matching = [
        candidate_plan
        for candidate_plan in plan.candidates
        if candidate_plan.candidate.variant == args.variant
    ]
    if len(matching) != 1:
        raise ValueError(
            f"Expected exactly one persisted candidate variant {args.variant}, found {len(matching)}."
        )
    candidate = matching[0].candidate
    if candidate.download_status == "completed":
        raise ValueError("Refusing to trace Download for an already-completed candidate.")
    if not args.confirm_download_activation:
        raise ValueError(
            "This diagnostic performs one real Flow Download activation. "
            "Review the command and add --confirm-download-activation to proceed."
        )
    if not 1 <= args.timeout <= 30:
        raise ValueError("--timeout must be between 1 and 30 seconds.")

    diagnostics_dir = (run_dir / "diagnostics").resolve()
    stem = (
        f"download_trigger_order_{args.order:04d}_v{args.variant:02d}_"
        f"{args.activation}"
    )
    if trace_label:
        stem = f"{stem}_{trace_label}"
    report_path = diagnostics_dir / f"{stem}.json"
    staging_dir = diagnostics_dir / f"{stem}_staging"
    if report_path.exists() or staging_dir.exists():
        raise FileExistsError(
            "A diagnostic report or staging directory already exists for this candidate; "
            "preserve and review it before running another trace."
        )

    state_path = run_dir / "run_state.json"
    state_before = state_path.read_bytes()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir()
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    report = session.trace_download_trigger(
        candidate=candidate,
        staging_dir=staging_dir,
        activation=args.activation,
        timeout_seconds=args.timeout,
    )
    state_after = state_path.read_bytes()
    if state_after != state_before:
        raise RuntimeError("Run state changed during the diagnostic trace; report was not written.")
    report.update(
        {
            "batch_id": batch.batch_id,
            "order": args.order,
            "variant": args.variant,
            "activation": args.activation,
            "trace_label": trace_label,
            "run_state_unchanged": True,
            "staging_directory": str(staging_dir),
        }
    )
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        handle.write("\n")

    print(f"Diagnostic status: {report['status']}")
    print(f"Outcome: {report['outcome']}")
    print(f"Safe events recorded: {len(report['events'])}")
    print(f"Staging files observed: {len(report['staging_files'])}")
    print(f"Run state unchanged: {report['run_state_unchanged']}")
    print(f"Diagnostic report: {report_path}")
    print(f"Trace staging: {staging_dir}")
    print("No prompt was submitted and generation state was not changed.")
    return 0


def cmd_trace_download_manual(args: argparse.Namespace) -> int:
    """Observe one user-performed Download interaction without dispatching UI input."""
    if args.variant != 1:
        raise ValueError("This bounded diagnostic is restricted to candidate v01.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", args.trace_label):
        raise ValueError("--trace-label must be 1-40 ASCII letters, numbers, hyphens, or underscores.")
    if not 1 <= args.timeout <= 120:
        raise ValueError("--timeout must be between 1 and 120 seconds.")

    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_download_one(batch, run_dir, args.order, overwrite=False)
    matching = [
        candidate_plan
        for candidate_plan in plan.candidates
        if candidate_plan.candidate.variant == args.variant
    ]
    if len(matching) != 1:
        raise ValueError(
            f"Expected exactly one persisted candidate variant {args.variant}, found {len(matching)}."
        )
    candidate = matching[0].candidate
    if candidate.download_status == "completed":
        raise ValueError("Refusing to trace Download for an already-completed candidate.")
    if not args.confirm_manual_trace:
        raise ValueError(
            "This command only arms passive observers, but the user may perform one real "
            "Download manually. Review the command and add --confirm-manual-trace to proceed."
        )

    diagnostics_dir = (run_dir / "diagnostics").resolve()
    stem = (
        f"download_manual_order_{args.order:04d}_v{args.variant:02d}_"
        f"{args.trace_label}"
    )
    report_path = diagnostics_dir / f"{stem}.json"
    staging_dir = diagnostics_dir / f"{stem}_staging"
    if report_path.exists() or staging_dir.exists():
        raise FileExistsError(
            "A diagnostic report or staging directory already exists for this label; "
            "preserve it and choose a fresh --trace-label."
        )

    state_path = run_dir / "run_state.json"
    state_before = state_path.read_bytes()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir()
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)

    def announce_ready() -> None:
        print("\nREADY FOR MANUAL DOWNLOAD TEST", flush=True)
        print("In the already-open Chrome Flow project, physically:", flush=True)
        print("  1. Hover candidate v01 with your real mouse.", flush=True)
        print("  2. Click that candidate's More options button once.", flush=True)
        print("  3. Click the visible Download menu item once with your real mouse.", flush=True)
        print(
            f"The passive observer will wait for {args.timeout} seconds; do not use this terminal meanwhile.\n",
            flush=True,
        )

    report = session.trace_download_manual(
        candidate=candidate,
        staging_dir=staging_dir,
        timeout_seconds=args.timeout,
        ready_callback=announce_ready,
    )
    state_after = state_path.read_bytes()
    if state_after != state_before:
        raise RuntimeError("Run state changed during the passive trace; report was not written.")
    report.update(
        {
            "batch_id": batch.batch_id,
            "order": args.order,
            "variant": args.variant,
            "trace_label": args.trace_label,
            "run_state_unchanged": True,
            "staging_directory": str(staging_dir),
        }
    )
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        handle.write("\n")

    print(f"Diagnostic status: {report['status']}")
    print(f"Outcome: {report['outcome']}")
    print(f"Safe events recorded: {len(report['events'])}")
    print(f"Staging files observed: {len(report['staging_files'])}")
    print(f"Run state unchanged: {report['run_state_unchanged']}")
    print(f"Diagnostic report: {report_path}")
    print(f"Diagnostic staging: {staging_dir}")
    print("No UI action was automated, no prompt was submitted, and no canonical asset was written.")
    return 0


def cmd_inspect_download_submenu(args: argparse.Namespace) -> int:
    """Inspect a manually exposed Download submenu without dispatching UI input."""
    if args.variant != 1:
        raise ValueError("This bounded diagnostic is restricted to candidate v01.")
    if not 1 <= args.timeout <= 120:
        raise ValueError("--timeout must be between 1 and 120 seconds.")

    batch = load_batch(Path(args.batch))
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_download_one(batch, run_dir, args.order, overwrite=False)
    matching = [
        candidate_plan
        for candidate_plan in plan.candidates
        if candidate_plan.candidate.variant == args.variant
    ]
    if len(matching) != 1:
        raise ValueError(
            f"Expected exactly one persisted candidate variant {args.variant}, found {len(matching)}."
        )
    candidate = matching[0].candidate
    if candidate.download_status == "completed":
        raise ValueError("Refusing to inspect Download for an already-completed candidate.")

    diagnostics_dir = (run_dir / "diagnostics").resolve()
    report_path = diagnostics_dir / (
        f"download_submenu_order_{args.order:04d}_v{args.variant:02d}.json"
    )
    if report_path.exists():
        raise FileExistsError(
            "The passive submenu report already exists; preserve it before another inspection."
        )

    state_path = run_dir / "run_state.json"
    state_before = state_path.read_bytes()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)

    def announce_ready() -> None:
        print("\nREADY FOR MANUAL SUBMENU INSPECTION", flush=True)
        print("In the already-open Chrome Flow project, physically:", flush=True)
        print("  1. Hover candidate v01 with your real mouse.", flush=True)
        print("  2. Open that candidate's More options menu.", flush=True)
        print("  3. Hover the first-level Download > item.", flush=True)
        print(
            "  4. If hovering does not expose it, click only that first-level Download > item once.",
            flush=True,
        )
        print("  5. Do NOT select anything from the submenu.", flush=True)
        print(
            f"Keep the submenu visible while the read-only probe waits up to {args.timeout} seconds.\n",
            flush=True,
        )

    report = session.inspect_download_submenu(
        candidate=candidate,
        timeout_seconds=args.timeout,
        ready_callback=announce_ready,
    )
    state_after = state_path.read_bytes()
    if state_after != state_before:
        raise RuntimeError("Run state changed during submenu inspection; report was not written.")
    report.update(
        {
            "batch_id": batch.batch_id,
            "order": args.order,
            "variant": args.variant,
            "run_state_unchanged": True,
        }
    )
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        handle.write("\n")

    print(f"Inspection status: {report['status']}")
    print(f"Outcome: {report['outcome']}")
    print(f"Visible menus captured: {report['snapshot']['visible_menu_count']}")
    print(f"Submenu detected: {report['snapshot']['submenu_detected']}")
    print(f"Run state unchanged: {report['run_state_unchanged']}")
    print(f"Diagnostic report: {report_path}")
    print("No UI action was automated and no submenu item was activated.")
    return 0


def cmd_probe_download_ui(args: argparse.Namespace) -> int:
    """Measure candidate menu readiness and stop before final 1K activation."""
    if args.variant != 1:
        raise ValueError("This bounded diagnostic is restricted to candidate v01.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", args.label):
        raise ValueError("--label must be 1-40 ASCII letters, numbers, hyphens, or underscores.")
    if not 1 <= args.samples <= 10:
        raise ValueError("--samples must be between 1 and 10.")
    if not args.confirm_ui_probe:
        raise ValueError(
            "This diagnostic performs candidate hover and menu UI actions but never "
            "activates 1K. Review the command and add --confirm-ui-probe to proceed."
        )
    if not 0 <= args.start_delay <= 30:
        raise ValueError("--start-delay must be between 0 and 30 seconds.")

    batch = (
        load_prompts_file(Path(args.batch))
        if getattr(args, "prompts_file", False)
        else load_batch(Path(args.batch))
    )
    run_dir = Path(args.run_dir) if args.run_dir else _default_run_dir(batch.batch_id)
    plan = plan_download_one(batch, run_dir, args.order, overwrite=False)
    matching = [
        candidate_plan
        for candidate_plan in plan.candidates
        if candidate_plan.candidate.variant == args.variant
    ]
    if len(matching) != 1:
        raise ValueError(
            f"Expected exactly one persisted candidate variant {args.variant}, "
            f"found {len(matching)}."
        )
    candidate = matching[0].candidate
    if candidate.candidate_id != args.candidate_id:
        raise ValueError(
            "The explicitly requested candidate UUID does not match the uniquely "
            "persisted candidate for this order/variant."
        )
    if candidate.download_status == "completed":
        raise ValueError("Refusing to probe an already-completed candidate.")

    diagnostics_dir = (run_dir / "diagnostics").resolve()
    report_path = diagnostics_dir / (
        f"download_ui_probe_order_{args.order:04d}_v{args.variant:02d}_{args.label}.json"
    )
    if report_path.exists():
        raise FileExistsError(
            "A Download UI probe report already exists for this label; preserve it "
            "and choose a fresh label."
        )

    state_path = run_dir / "run_state.json"
    state_before = state_path.read_bytes()
    asset_dir = run_dir / "assets"

    def asset_inventory() -> tuple[tuple[str, int, int], ...]:
        if not asset_dir.exists():
            return ()
        return tuple(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(asset_dir.iterdir(), key=lambda value: value.name)
            if path.is_file()
        )

    assets_before = asset_inventory()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    config = _load_config(args.config)
    session = _flow_session_from_args(args, config)
    print(
        f"Download UI probe starts in {args.start_delay} second(s). "
        "Arrange the requested window condition now; do not touch Flow afterward.",
        flush=True,
    )
    if args.start_delay:
        time.sleep(args.start_delay)
    report = session.probe_download_ui(
        candidate=candidate,
        label=args.label,
        samples=args.samples,
    )
    if state_path.read_bytes() != state_before:
        raise RuntimeError("Run state changed during the Download UI probe; report was not written.")
    if asset_inventory() != assets_before:
        raise RuntimeError("Canonical assets changed during the Download UI probe; report was not written.")
    report.update(
        {
            "batch_id": batch.batch_id,
            "order": args.order,
            "variant": args.variant,
            "run_state_unchanged": True,
            "canonical_assets_unchanged": True,
            "start_delay_seconds": args.start_delay,
        }
    )
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        handle.write("\n")

    print(
        "condition sample toolbar_ms more_options_ms download_parent_ms "
        "submenu_ms 1k_ready_ms reveal_attempts submenu_polls parent_activations "
        "cleanup_neutral remounts result"
    )
    for sample in report["samples"]:
        remounts = sum(
            bool(sample.get(key))
            for key in (
                "candidate_tile_remounted",
                "more_options_remounted",
                "download_parent_remounted",
            )
        )
        print(
            f"{sample['condition']} {sample['sample']} "
            f"{sample.get('toolbar_ms')} {sample.get('more_options_ms')} "
            f"{sample.get('download_parent_ms')} {sample.get('submenu_ms')} "
            f"{sample.get('one_k_ready_ms')} {sample.get('reveal_attempts')} "
            f"{sample.get('submenu_polls')} {sample.get('parent_activation_count')} "
            f"{sample.get('cleanup_neutral_state')} {remounts} {sample['status']}"
        )
        if sample["status"] == "failure":
            failure_detail = {
                "activation_dispatch_returned_normally": sample.get(
                    "activation_dispatch_returned_normally"
                ),
                "classification": sample.get("final_safe_failure_classification"),
                "final_parent": sample.get("final_parent"),
                "post_activation_poll_count": sample.get(
                    "post_activation_poll_count"
                ),
                "pre_activation_poll_count": sample.get(
                    "pre_activation_poll_count"
                ),
                "remaining_visible_menu_count": sample.get(
                    "remaining_visible_menu_count"
                ),
                "remaining_visible_submenu_count": sample.get(
                    "remaining_visible_submenu_count"
                ),
            }
            print(
                "  failure_detail="
                + json.dumps(failure_detail, ensure_ascii=True, sort_keys=True)
            )
    summary = report["summary"]
    print(
        f"Summary {summary['condition']}: success={summary['success_count']}/"
        f"{summary['sample_count']} ({summary['success_rate']:.0%}); "
        f"median_more_options_ms={summary['median_more_options_ms']}; "
        f"median_submenu_ms={summary['median_submenu_ms']}; "
        f"median_total_1k_ready_ms={summary['median_total_1k_ready_ms']}; "
        f"reveal_retries={summary['total_reveal_retries']}; "
        "remounts="
        f"{summary['candidate_tile_remounts'] + summary['more_options_remounts'] + summary['download_parent_remounts']}"
    )
    print(f"Final document.hasFocus(): {report['final_document_has_focus']}")
    print(f"Final document.visibilityState: {report['final_document_visibility_state']}")
    print(f"CDP focus emulation active: {report['focus_emulation_active']}")
    print(f"Run state unchanged: {report['run_state_unchanged']}")
    print(f"Canonical assets unchanged: {report['canonical_assets_unchanged']}")
    print(f"Diagnostic report: {report_path}")
    print("Stopped at exact 1K readiness; 1K was not activated and no download was armed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nano-banana-batch",
        description="Conservative one-prompt Google Flow / Nano Banana automation experiment.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="Validate and inspect a batch file.")
    p.add_argument("batch")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("run", help="Initialize or dry-run a batch.")
    p.add_argument("batch")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-items", type=int)
    p.add_argument("--run-dir")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "run-batch",
        help="Sequentially process every item in a validated batch.",
    )
    p.add_argument("batch")
    p.add_argument("--preview", action="store_true")
    p.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry a failed or interrupted generation; downloads resume automatically.",
    )
    p.add_argument("--timeout", type=int, help="Override per-order generation timeout in seconds.")
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_run_batch)

    p = sub.add_parser(
        "run-prompt",
        help="Process one directly supplied prompt through the sequential batch path.",
    )
    p.add_argument("prompt")
    p.add_argument("--preview", action="store_true")
    p.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry a failed or interrupted generation; downloads resume automatically.",
    )
    p.add_argument("--timeout", type=int, help="Override generation timeout in seconds.")
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_run_prompt)

    p = sub.add_parser(
        "run-prompts-file",
        help="Process one UTF-8 prompt per line through the sequential batch path.",
    )
    p.add_argument("prompts_file")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--preview", action="store_true")
    p.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry a failed or interrupted generation; downloads resume automatically.",
    )
    p.add_argument("--timeout", type=int, help="Override per-order generation timeout in seconds.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_run_prompts_file)

    p = sub.add_parser(
        "launcher",
        help="Open the interactive Windows prompt-batch launcher.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_launcher)

    p = sub.add_parser("status", help="Show persisted state for a batch.")
    p.add_argument("batch")
    p.add_argument("--run-dir")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "repair-order-candidates",
        help="Transactionally clear one proven ownership-conflicted candidate result.",
    )
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--run-dir")
    p.add_argument(
        "--preview",
        action="store_true",
        help="Validate and report the exact repair without changing state or assets.",
    )
    p.set_defaults(func=cmd_repair_order_candidates)

    p = sub.add_parser("open-flow", help="Open normal Chrome for manual Flow authentication.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.set_defaults(func=cmd_open_flow)

    p = sub.add_parser(
        "open-flow-debug",
        help="Open normal Chrome with loopback-only debugging for Playwright attachment.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_open_flow_debug)

    p = sub.add_parser("inspect-flow", help="Read visible Flow UI metadata without submitting a prompt.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.add_argument("--screenshot", help="Diagnostic screenshot path.")
    p.set_defaults(func=cmd_inspect_flow)

    p = sub.add_parser(
        "inspect-flow-project-ui",
        help="Report bounded project-creation/navigation UI evidence without interaction.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum candidate controls to report (1-50).",
    )
    p.set_defaults(func=cmd_inspect_flow_project_ui)

    p = sub.add_parser(
        "inspect-current-flow-project",
        help="Report current Flow project identity/editor readiness without interaction.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum visible project-dialog controls to report (1-25).",
    )
    p.set_defaults(func=cmd_inspect_current_flow_project)

    p = sub.add_parser(
        "inspect-flow-download-layout",
        help="Report bounded window and candidate-toolbar layout evidence without interaction.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum visible candidate tiles to report (1-10).",
    )
    p.set_defaults(func=cmd_inspect_flow_download_layout)

    p = sub.add_parser(
        "inspect-flow-model-menu",
        help="Inspect an already-open Flow model menu without interaction.",
    )
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maximum model controls/options to report (1-30).",
    )
    p.set_defaults(func=cmd_inspect_flow_model_menu)

    p = sub.add_parser("generate-one", help="Preview or execute exactly one Flow image prompt.")
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--retry", action="store_true", help="Retry a failed or interrupted item.")
    p.add_argument(
        "--download",
        action="store_true",
        help="After a successful new generation, download its persisted candidates.",
    )
    p.add_argument("--timeout", type=int, help="Override result timeout in seconds.")
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_generate_one)

    p = sub.add_parser(
        "download-one",
        help="Download candidates for one already-completed persisted generation.",
    )
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--preview", action="store_true")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace deterministic candidate files, including valid downloads.",
    )
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_download_one)

    p = sub.add_parser(
        "trace-download-trigger",
        help="Trace one existing candidate's Flow Download action without changing run state.",
    )
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--variant", type=int, required=True)
    p.add_argument(
        "--activation",
        required=True,
        choices=("locator", "mouse", "keyboard"),
        help="Explicit conservative activation strategy for this one trace.",
    )
    p.add_argument(
        "--trace-label",
        help="Optional safe suffix for a distinct immutable diagnostic run.",
    )
    p.add_argument("--timeout", type=int, default=10)
    p.add_argument(
        "--confirm-download-activation",
        "--confirm-download-click",
        dest="confirm_download_activation",
        action="store_true",
        help="Explicitly authorize exactly one real Download activation for the selected candidate.",
    )
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_trace_download_trigger)

    p = sub.add_parser(
        "trace-download-manual",
        help="Passively observe one physical user Download action without automating the menu.",
    )
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--variant", type=int, required=True)
    p.add_argument(
        "--trace-label",
        required=True,
        help="Safe suffix for a fresh immutable passive diagnostic run.",
    )
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument(
        "--confirm-manual-trace",
        action="store_true",
        help="Arm passive observers and wait for the user's one physical Download action.",
    )
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_trace_download_manual)

    p = sub.add_parser(
        "inspect-download-submenu",
        help="Read a user-exposed Download submenu without activating any UI control.",
    )
    p.add_argument("batch")
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--variant", type=int, required=True)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_inspect_download_submenu)

    p = sub.add_parser(
        "probe-download-ui",
        help="Measure candidate-to-1K UI readiness without activating 1K or downloading.",
    )
    p.add_argument("batch")
    p.add_argument(
        "--prompts-file",
        action="store_true",
        help="Interpret the input as the run-prompts-file UTF-8 prompt list.",
    )
    p.add_argument("--order", type=int, required=True)
    p.add_argument("--variant", type=int, required=True)
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--samples", type=int, default=3)
    p.add_argument(
        "--start-delay",
        type=int,
        default=8,
        help="Seconds allowed to establish the foreground/covered condition before probing.",
    )
    p.add_argument(
        "--confirm-ui-probe",
        action="store_true",
        help="Authorize harmless candidate hover/menu actions that stop before 1K activation.",
    )
    p.add_argument("--run-dir")
    p.add_argument("--config", default="config.json")
    p.add_argument("--url")
    p.add_argument("--profile-dir")
    p.add_argument("--debug-address")
    p.add_argument("--debug-port", type=int)
    p.set_defaults(func=cmd_probe_download_ui)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
