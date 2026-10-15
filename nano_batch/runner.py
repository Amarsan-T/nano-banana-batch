from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .cancellation import (
    cancellation_blocking_operation,
    cancellation_checkpoint,
    cancellation_requested,
    interruptible_call,
)
from .downloads import (
    ValidatedImage,
    canonical_extension_for_image_format,
    commit_staged_file,
    deterministic_candidate_path,
    deterministic_staging_path,
    run_asset_directory,
    validate_image_file,
)
from .errors import (
    CANDIDATE_CORRELATION_UNPROVEN,
    CANDIDATE_UUID_OWNERSHIP_CONFLICT,
    FLOW_GENERIC_GENERATION_FAILED,
    FLOW_GENERATION_NOT_SUBMITTED,
    FLOW_POLICY_REJECTED,
    FLOW_PROJECT_IDENTITY_UNPROVEN,
    GENERATION_INTERRUPT_EVIDENCE_SCHEMA,
    GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT,
    GENERATION_SUBMISSION_NOT_SUBMITTED,
    GenericGenerationFailure,
    HighDemandGenerationFailure,
    GenerationPreflightError,
    PolicyGenerationRejected,
    TemporaryGenerationBlock,
    UnusualActivityGenerationFailure,
    WAITING_FOR_FLOW_LIMIT_RESET,
)
from .models import (
    Batch,
    BatchItem,
    BrowserDownloadResult,
    CandidateRecord,
    FlowProjectReference,
    GenerationResult,
    GenerationSettings,
    PendingDownloadCommit,
    PENDING_DOWNLOAD_COMMIT_STATE_VERSION,
    RunItemState,
    RunState,
)
from .state import RunStateStore


_AUTOMATIC_GENERIC_FAILURE_RETRY_LIMIT = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_run_state(
    batch: Batch,
    run_dir: Path,
    *,
    require_flow_project: bool = False,
) -> RunState:
    store = RunStateStore(run_dir / "run_state.json")
    if store.exists():
        existing = store.load()
        if existing.batch_id != batch.batch_id:
            raise ValueError("Existing run state belongs to a different semantic batch.")
        return existing

    now = utc_now()
    state = RunState(
        schema="nano-banana-batch-run-state",
        version=4 if require_flow_project else 3,
        batch_id=batch.batch_id,
        items=[RunItemState(order=i.order, visual_id=i.visual_id, prompt=i.prompt) for i in batch.items],
        created_at=now,
        updated_at=now,
        flow_project_required=require_flow_project,
    )
    store.save(state)
    return state


def dry_run(batch: Batch, max_items: int | None = None) -> None:
    items = list(batch.items)
    if max_items is not None:
        if max_items < 0:
            raise ValueError("--max-items cannot be negative.")
        items = items[:max_items]
    print(f"DRY RUN — {len(items)} of {len(batch.items)} item(s)")
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item.visual_id}")
        print(f"  order: {item.order}")
        print(f"  prompt: {item.prompt}")


def summarize_state(state: RunState) -> str:
    counts = state.counts()
    return (
        f"Batch: {state.batch_id}\n"
        f"Items: {len(state.items)}\n"
        f"Pending: {counts['pending']}\n"
        f"Running: {counts['running']}\n"
        f"Waiting for Flow limit reset: {counts[WAITING_FOR_FLOW_LIMIT_RESET]}\n"
        f"Completed: {counts['completed']}\n"
        f"Failed: {counts['failed']}"
    )


def output_basename(order: int, visual_id: str, variant: int, extension: str = "png") -> str:
    if order <= 0 or variant <= 0:
        raise ValueError("order and variant must be positive.")
    safe_visual_id = visual_id.strip()
    if not safe_visual_id or any(ch in safe_visual_id for ch in r'\/:*?"<>|'):
        raise ValueError("visual_id is not safe for a filename.")
    ext = extension.lower().lstrip(".")
    return f"{order:04d}__{safe_visual_id}__v{variant:02d}.{ext}"


class OnePromptAdapter(Protocol):
    def generate_one(
        self,
        *,
        item: BatchItem,
        settings: GenerationSettings,
        forbidden_candidate_ids: frozenset[str],
    ) -> GenerationResult:
        """Submit one prompt and return only confidently discovered candidates."""


class OneCandidateDownloadAdapter(Protocol):
    def download_candidate(
        self, *, candidate: CandidateRecord, destination: Path
    ) -> BrowserDownloadResult:
        """Use the attached browser to download one exact persisted candidate."""


class SequentialBatchAdapter(OnePromptAdapter, OneCandidateDownloadAdapter, Protocol):
    """The proven one-order generation and download capabilities used sequentially."""

    def create_flow_project(self) -> FlowProjectReference:
        """Create exactly one project and return its proven normalized identity."""

    def prepare_flow_project(
        self,
        *,
        project: FlowProjectReference,
        settings: GenerationSettings | None,
    ) -> None:
        """Reopen/verify one saved project and optionally verify generation readiness."""


@dataclass(frozen=True)
class CandidateDownloadPlan:
    candidate: CandidateRecord
    destination: Path
    destination_pattern: str
    existing_destination: Path | None
    action: str


@dataclass(frozen=True)
class DownloadPlan:
    state: RunState
    item: RunItemState
    candidates: tuple[CandidateDownloadPlan, ...]
    asset_dir: Path
    staging_dir: Path


@dataclass(frozen=True)
class SequentialOrderPlan:
    order: int
    visual_id: str
    generation_status: str
    generation_action: str
    download_status: str
    download_action: str
    candidate_actions: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class SequentialBatchPlan:
    orders: tuple[SequentialOrderPlan, ...]
    run_dir: Path
    state_exists: bool


@dataclass(frozen=True)
class SequentialOrderOutcome:
    order: int
    visual_id: str
    generation: str
    downloads: str


@dataclass(frozen=True)
class OrderCandidateRepairPlan:
    """Validated, read-only plan for clearing one corrupt candidate association."""

    state: RunState
    item: RunItemState
    candidate_ids: tuple[tuple[int, str], ...]
    conflicts: tuple[tuple[str, tuple[tuple[int, int], ...]], ...]
    earlier_orders: tuple[int, ...]
    later_orders: tuple[int, ...]
    canonical_assets: tuple[str, ...]


def resolve_item(batch: Batch, order: int) -> BatchItem:
    for item in batch.items:
        if item.order == order:
            return item
    raise ValueError(f"No batch item has order {order}.")


def preview_one(batch: Batch, order: int, settings: GenerationSettings) -> BatchItem:
    settings.validate()
    item = resolve_item(batch, order)
    _validate_declared_generation_settings(item, settings)
    return item


def _validate_declared_generation_settings(
    item: BatchItem,
    settings: GenerationSettings,
) -> None:
    # preferred_model_hint is retained as legacy Batch ID metadata only. The
    # current visible Flow image model is user-controlled and never enforced.
    expected_fields = {
        "mode": settings.mode,
        "aspect_ratio": settings.aspect_ratio,
        "variants": settings.variants,
    }
    for field_name, expected in expected_fields.items():
        declared = getattr(item, field_name)
        if declared is None:
            continue
        if isinstance(declared, str) and isinstance(expected, str):
            matches = (
                " ".join(declared.split()).casefold()
                == " ".join(expected.split()).casefold()
            )
        else:
            matches = declared == expected
        if not matches:
            raise ValueError(
                f"Batch order {item.order} declares {field_name}={declared!r}, "
                f"but runtime settings require {expected!r}."
            )


def _validate_sequential_batch_scope(
    batch: Batch,
    settings: GenerationSettings,
) -> None:
    settings.validate()
    if not batch.items:
        raise ValueError("run-batch requires a non-empty validated batch.")
    if settings.variants not in {1, 2}:
        raise ValueError(
            "Sequential execution currently supports exactly one or two candidates per order."
        )
    for item in batch.items:
        _validate_declared_generation_settings(item, settings)


def _matching_run_item(state: RunState, item: BatchItem) -> RunItemState:
    matches = [
        state_item
        for state_item in state.items
        if state_item.order == item.order and state_item.visual_id == item.visual_id
    ]
    if len(matches) != 1 or not matches[0].matches_source(item):
        raise ValueError(f"Run state identity does not match batch order {item.order}.")
    return matches[0]


def exhausted_policy_rejection(item: RunItemState) -> bool:
    """Only a clean, failed policy generation is eligible for prompt-edit recovery."""
    return (
        item.status == "failed"
        and item.generation_error_code == FLOW_POLICY_REJECTED
        and item.automatic_policy_retry_count == 1
        and not item.candidates and not item.output_files
        and item.result_details is None
        and item.download_status == "not_started"
        and item.download_attempt_count == 0
    )


def edit_policy_rejected_prompt(
    batch: Batch, run_dir: Path, order: int, replacement: str,
) -> None:
    """Atomically edit one exhausted policy Order, without authorizing a Start."""
    cancellation_checkpoint("policy_prompt_edit_persist")
    if not isinstance(replacement, str) or not replacement.strip():
        raise ValueError("Replacement prompt cannot be empty.")
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    state = store.load()
    if state.batch_id != batch.batch_id or len(state.items) != len(batch.items):
        raise ValueError("Run state belongs to a different semantic batch.")
    for item in batch.items:
        _matching_run_item(state, item)
    source = resolve_item(batch, order)
    target = _matching_run_item(state, source)
    if not exhausted_policy_rejection(target):
        raise ValueError("Prompt editing requires an exhausted policy rejection with no results.")
    if any((run_dir / "assets").glob(f"{order:04d}__*")):
        raise ValueError("Refusing to edit an Order with existing local asset artifacts.")
    target.source_prompt = source.prompt
    target.prompt = replacement
    state.version = max(state.version, 5)
    state.updated_at = utc_now()
    store.save(state)


def proven_pre_start_not_submitted(item: RunItemState) -> bool:
    """Recognize only durable evidence from control flow known to precede Start."""
    if (
        item.status != "failed"
        or item.candidates
        or item.output_files
        or item.result_details is not None
        or item.download_status != "not_started"
        or item.download_attempt_count != 0
    ):
        return False
    if item.generation_error_code == FLOW_GENERATION_NOT_SUBMITTED:
        return True
    # Compatibility for the exact old page-global preflight guard. That guard
    # executed in configure_generation(), before prompt entry and Start.
    return bool(
        item.generation_error_code is None
        and item.error
        and "Image-mode preflight requires exactly one visible generation control; found "
        in item.error
    )


def automatic_generic_failure_retry_available(item: RunItemState) -> bool:
    """Return whether this exact failed Order retains an automatic retry."""
    return bool(
        item.status == "failed"
        and item.generation_error_code == FLOW_GENERIC_GENERATION_FAILED
        and item.automatic_generic_failure_retry_count
        < _AUTOMATIC_GENERIC_FAILURE_RETRY_LIMIT
    )


def _generation_interrupt_evidence(exc: KeyboardInterrupt) -> dict[str, object] | None:
    """Accept only the adapter's bounded, prompt-free interrupt checkpoint."""
    if getattr(exc, "generation_interrupt_schema", None) != (
        GENERATION_INTERRUPT_EVIDENCE_SCHEMA
    ):
        return None
    phase = str(getattr(exc, "interrupt_phase", "unknown"))
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", phase):
        phase = "unknown"
    submission_state = getattr(exc, "submission_state", None)
    if submission_state not in {
        GENERATION_SUBMISSION_NOT_SUBMITTED,
        GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT,
    }:
        return None
    prompt_written = getattr(exc, "prompt_written", None)
    start_mouse_down_sent = getattr(exc, "start_mouse_down_sent", None)
    start_acknowledged = getattr(exc, "start_acknowledged", None)
    if not all(
        isinstance(value, bool)
        for value in (
            prompt_written,
            start_mouse_down_sent,
            start_acknowledged,
        )
    ):
        return None
    if submission_state == GENERATION_SUBMISSION_NOT_SUBMITTED:
        if start_mouse_down_sent or start_acknowledged:
            return None
    elif not start_mouse_down_sent:
        return None
    cancellation_received_phase = str(
        getattr(exc, "cancellation_received_phase", phase)
    )
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", cancellation_received_phase):
        cancellation_received_phase = "unknown"
    return {
        "interrupt_phase": phase,
        "cancellation_requested": True,
        "cancellation_received_phase": cancellation_received_phase,
        "prompt_written": prompt_written,
        "start_mouse_down_sent": start_mouse_down_sent,
        "start_acknowledged": start_acknowledged,
        "submission_state": submission_state,
    }


def _generation_interrupt_summary(evidence: dict[str, object]) -> str:
    return "; ".join(
        (
            f"interrupt_phase={evidence['interrupt_phase']}",
            "cancellation_requested=true",
            "cancellation_received_phase="
            f"{evidence['cancellation_received_phase']}",
            f"prompt_written={str(evidence['prompt_written']).lower()}",
            "start_mouse_down_sent="
            f"{str(evidence['start_mouse_down_sent']).lower()}",
            f"start_acknowledged={str(evidence['start_acknowledged']).lower()}",
            f"submission_state={evidence['submission_state']}",
        )
    )


def plan_sequential_batch(
    batch: Batch,
    run_dir: Path,
    settings: GenerationSettings,
    *,
    retry_failed_generation: bool = False,
) -> SequentialBatchPlan:
    """Build a read-only N-item resume plan using existing download validation."""
    _validate_sequential_batch_scope(batch, settings)
    run_dir = run_dir.resolve()
    store = RunStateStore(run_dir / "run_state.json")
    state = store.load() if store.exists() else None
    if state is not None and state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")

    orders: list[SequentialOrderPlan] = []
    for item in batch.items:
        if state is None:
            generation_status = "pending"
            generation_action = "generate"
            download_status = "not_started"
            download_action = "after_generation"
            candidate_actions = tuple(
                (variant, "download_after_generation")
                for variant in range(1, settings.variants + 1)
            )
        else:
            state_item = _matching_run_item(state, item)
            generation_status = state_item.status
            download_status = state_item.download_status
            if state_item.status == "completed":
                download_plan = plan_download_one(batch, run_dir, item.order)
                candidate_actions = tuple(
                    (candidate.candidate.variant, candidate.action)
                    for candidate in download_plan.candidates
                )
                generation_action = "skip_completed"
                download_action = (
                    "skip_valid"
                    if candidate_actions
                    and all(action == "skip_valid" for _, action in candidate_actions)
                    else "resume"
                )
            elif state_item.status == "pending":
                generation_action = "generate"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            elif state_item.status == WAITING_FOR_FLOW_LIMIT_RESET:
                generation_action = "resume_external_block"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            elif proven_pre_start_not_submitted(state_item):
                generation_action = "resume_pre_start"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            elif (
                state_item.status == "failed"
                and state_item.generation_error_code == FLOW_POLICY_REJECTED
                and state_item.automatic_policy_retry_count == 0
            ):
                generation_action = "auto_retry_policy"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            elif automatic_generic_failure_retry_available(state_item):
                generation_action = "auto_retry_generic_failure"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            elif retry_failed_generation:
                generation_action = "retry"
                download_action = "after_generation"
                candidate_actions = tuple(
                    (variant, "download_after_generation")
                    for variant in range(1, settings.variants + 1)
                )
            else:
                generation_action = "blocked_retry_required"
                download_action = "blocked"
                candidate_actions = ()

        orders.append(
            SequentialOrderPlan(
                order=item.order,
                visual_id=item.visual_id,
                generation_status=generation_status,
                generation_action=generation_action,
                download_status=download_status,
                download_action=download_action,
                candidate_actions=candidate_actions,
            )
        )
    return SequentialBatchPlan(
        orders=tuple(orders),
        run_dir=run_dir,
        state_exists=state is not None,
    )


def _download_state_item(batch: Batch, run_dir: Path, order: int) -> tuple[RunState, RunItemState]:
    requested = resolve_item(batch, order)
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        raise ValueError(f"No persisted run state exists at {store.path}.")
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")
    state_item = next(
        (
            item
            for item in state.items
            if item.order == requested.order and item.visual_id == requested.visual_id
        ),
        None,
    )
    if state_item is None or not state_item.matches_source(requested):
        raise ValueError("Run state item identity does not match the source batch.")
    if state_item.status != "completed":
        raise ValueError(
            f"{state_item.visual_id} generation is {state_item.status}; "
            "download-one requires an already-completed generation."
        )
    if not state_item.candidates:
        raise ValueError(f"{state_item.visual_id} has no persisted candidates to download.")
    return state, state_item


def _persisted_candidate_id(candidate: CandidateRecord) -> str | None:
    """Return the durable UUID-like identity recorded for one candidate."""
    if candidate.candidate_id:
        return candidate.candidate_id
    if candidate.asset_url:
        parts = candidate.asset_url.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-2] == "image" and parts[-1]:
            return parts[-1]
    return None


def _candidate_ownerships(state: RunState) -> dict[str, list[tuple[int, int]]]:
    ownerships: dict[str, list[tuple[int, int]]] = {}
    for item in state.items:
        for candidate in item.candidates:
            candidate_id = _persisted_candidate_id(candidate)
            if candidate_id:
                ownerships.setdefault(candidate_id, []).append(
                    (item.order, candidate.variant)
                )
    return ownerships


def _assert_unique_download_ownership(
    state: RunState,
    state_item: RunItemState,
    candidate: CandidateRecord,
) -> None:
    candidate_id = _persisted_candidate_id(candidate)
    expected_owner = (state_item.order, candidate.variant)
    owners = _candidate_ownerships(state).get(candidate_id or "", [])
    if not candidate_id or owners != [expected_owner]:
        owner_text = ", ".join(
            f"order {order} v{variant:02d}" for order, variant in owners
        ) or "none"
        raise ValueError(
            f"{CANDIDATE_UUID_OWNERSHIP_CONFLICT}: candidate for order "
            f"{state_item.order} v{candidate.variant:02d} does not have unique "
            f"persisted ownership; candidate_id={candidate_id or '(missing)'}; "
            f"owners={owner_text}."
        )


def _validate_recorded_download(candidate: CandidateRecord, destination: Path) -> None:
    if not candidate.local_path:
        raise ValueError("completed download metadata has no local path")
    if Path(candidate.local_path).resolve() != destination.resolve():
        raise ValueError("completed download metadata points outside its deterministic path")
    metadata = validate_image_file(destination, expected_filename=candidate.suggested_filename)
    required = {
        "sha256": candidate.sha256,
        "byte_size": candidate.byte_size,
        "width": candidate.width,
        "height": candidate.height,
        "mime_type": candidate.mime_type,
        "image_format": candidate.image_format,
    }
    actual = {
        "sha256": metadata.sha256,
        "byte_size": metadata.byte_size,
        "width": metadata.width,
        "height": metadata.height,
        "mime_type": metadata.mime_type,
        "image_format": metadata.image_format,
    }
    if any(value is None for value in required.values()) or required != actual:
        raise ValueError("completed download metadata does not match the validated local file")


def validate_pending_download_ownership(
    *,
    batch: Batch,
    state: RunState,
    order: int,
    variant: int,
    run_dir: Path,
) -> tuple[Path, Path]:
    """Prove pending logical ownership and paths without reading image bytes."""
    if type(order) is not int or order <= 0 or type(variant) is not int or variant <= 0:
        raise ValueError("Order and variant must be positive integers.")
    if state.batch_id != batch.batch_id:
        raise ValueError("Pending download belongs to a different semantic batch.")
    if (
        len([item for item in batch.items if item.order == order]) != 1
        or len([item for item in state.items if item.order == order]) != 1
    ):
        raise ValueError("Expected exactly one source and saved Order.")
    source_item = resolve_item(batch, order)
    state_item = _matching_run_item(state, source_item)

    matches = []

    for candidate in state_item.candidates:
        if candidate.variant == variant:
            matches.append(candidate)

    if len(matches) != 1:
        raise ValueError("Expected exactly one matching candidate")

    state_candidate = matches[0]
    if not isinstance(state_candidate.candidate_id, str) or not state_candidate.candidate_id:
        raise ValueError("Candidate UUID must be a non-empty string")
    _assert_unique_download_ownership(state, state_item, state_candidate)
    pending = state_candidate.pending_download_commit
    if not isinstance(pending, PendingDownloadCommit):
        raise ValueError("A typed pending download record is required.")
    pending.validate()
    if type(state.version) is not int or state.version < PENDING_DOWNLOAD_COMMIT_STATE_VERSION:
        raise ValueError("Pending download requires run-state version 6 or later.")
    if (
        state_item.status != "completed"
        or not isinstance(state_item.completed_at, str) or not state_item.completed_at
        or state_item.error is not None or state_item.generation_error_code is not None
        or state_item.download_status != "running" or state_candidate.download_status != "running"
        or state_item.download_error is not None or state_candidate.download_error is not None
        or state_item.download_completed_at is not None
    ):
        raise ValueError("Pending download has conflicting generation or download state.")
    if any(getattr(state_candidate, field) is not None for field in (
        "local_path", "local_relative_path", "sha256", "byte_size", "width", "height",
        "mime_type", "image_format", "downloaded_at",
    )):
        raise ValueError("Pending download conflicts with completed candidate metadata.")
    if state.flow_project_required is not True or not isinstance(state.flow_project, FlowProjectReference):
        raise ValueError("Automatic reconciliation requires saved project ownership.")
    state.flow_project.validate()
    identities = (
        (pending.batch_id, batch.batch_id), (pending.project_id, state.flow_project.project_id),
        (pending.order, state_item.order), (pending.visual_id, state_item.visual_id),
        (pending.variant, state_candidate.variant), (pending.candidate_id, state_candidate.candidate_id),
    )
    if any(recorded != expected for recorded, expected in identities):
        raise ValueError("Pending download ownership does not match authoritative state.")
    for recorded, expected in (
        (pending.order, state_item.order), (pending.order, source_item.order),
        (pending.variant, state_candidate.variant),
        (pending.generation_attempt_count, state_item.attempt_count),
        (pending.download_attempt_count, state_item.download_attempt_count),
    ):
        if type(expected) is not int or expected <= 0 or recorded != expected:
            raise ValueError("Pending download Order, variant or attempt counters conflict.")
    if state_candidate.asset_url is not None and not isinstance(state_candidate.asset_url, str):
        raise ValueError("Recorded asset identity must be a string or null.")
    url_id = _persisted_candidate_id(replace(state_candidate, candidate_id=None))
    if url_id is not None and url_id != state_candidate.candidate_id:
        raise ValueError("Persisted candidate UUID conflicts with its recorded asset identity.")
    canonical, staged = _pending_download_paths(pending, state_item, run_dir)
    _assert_pending_download_file_claims(state, state_candidate, run_dir, canonical, staged)
    return canonical, staged


def _assert_pending_download_file_claims(
    state: RunState, selected: CandidateRecord, run_dir: Path, canonical: Path,
    staged: Path | None, *, allow_selected_output: bool = False,
) -> None:
    """Reject competing saved file claims, independently of candidate UUID ownership."""
    root = run_dir.resolve()

    def resolve_claim(value, *, relative_only=False):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Saved file ownership paths must be non-empty strings.")
        path = Path(value)
        if ".." in path.parts or (
            (path.drive or path.root) and (relative_only or not path.is_absolute())
        ):
            raise ValueError("Saved file ownership path is ambiguous or contains traversal.")
        lexical = path if path.is_absolute() else root / path
        try:
            # Reject lexical escapes before resolving an unrelated external path.
            lexical.relative_to(root)
            resolved = lexical.resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("Saved file ownership path cannot be proven inside the run.") from exc
        return resolved

    def conflicts(path):
        return path in (canonical, staged) or (
            path.parent == canonical.parent and path.stem == canonical.stem
        )

    selected_output_seen = False
    for saved_item in state.items:
        if not isinstance(saved_item.output_files, list):
            raise ValueError("Saved Order output ownership must be a list of paths.")
        for path in saved_item.output_files:
            resolved = resolve_claim(path)
            if conflicts(resolved):
                # Ordinary skip/explicit overwrite may retain exactly one proven
                # registration of this candidate. Pending recovery permits none.
                if (
                    allow_selected_output and not selected_output_seen
                    and any(candidate is selected for candidate in saved_item.candidates)
                    and resolved == canonical and selected.local_path is not None
                    and resolve_claim(selected.local_path) == canonical
                    and (selected.local_relative_path is None or resolve_claim(
                        selected.local_relative_path, relative_only=True,
                    ) == canonical)
                ):
                    selected_output_seen = True
                    continue
                raise ValueError("Pending download file is already registered in Order output_files.")
        for other in saved_item.candidates:
            if other is selected:
                continue
            local_paths = []
            for field in ("local_path", "local_relative_path"):
                value = getattr(other, field)
                if value is not None:
                    local_paths.append(resolve_claim(value, relative_only=field == "local_relative_path"))
            if other.download_status == "completed" and not local_paths:
                raise ValueError("Completed candidate lacks file ownership path evidence.")
            if len(local_paths) == 2 and local_paths[0] != local_paths[1]:
                raise ValueError("Candidate local file ownership paths conflict.")
            if any(conflicts(path) for path in local_paths):
                raise ValueError("Pending download file is already claimed by another candidate.")
            if other.pending_download_commit is None:
                continue
            other_pending = other.pending_download_commit
            if not isinstance(other_pending, PendingDownloadCommit):
                raise ValueError("Saved pending download metadata is not typed.")
            other_pending.validate()
            if any(conflicts(path) for path in _pending_download_paths(other_pending, saved_item, root)):
                raise ValueError("Multiple pending candidates claim the same file path.")


class _DownloadFileOwnershipError(ValueError):
    """Stop without replacing state when a download boundary cannot prove ownership."""


def _assert_current_download_file_claims(
    state: RunState, item: RunItemState, candidate: CandidateRecord,
    run_dir: Path, canonical: Path, staged: Path | None, *, allow_selected_output: bool,
) -> None:
    """Recheck the saved snapshot and global claims before ordinary writes.

    Reject any changed snapshot under the existing single-writer contract. This
    is a bounded reread, not a lock or compare-and-swap filesystem transaction.
    """
    cancellation_checkpoint("download_file_ownership")
    try:
        saved = RunStateStore(run_dir / "run_state.json").load()
        if saved != state:
            raise ValueError("Run state changed during download; refusing stale ownership writes.")
        owners = [entry for entry in saved.items if entry.order == item.order]
        if len(owners) != 1:
            raise ValueError("Download requires exactly one saved Order.")
        matches = [entry for entry in owners[0].candidates if entry.variant == candidate.variant]
        if len(matches) != 1:
            raise ValueError("Download requires exactly one saved candidate variant.")
        _assert_unique_download_ownership(saved, owners[0], matches[0])
        _assert_pending_download_file_claims(
            saved, matches[0], run_dir, canonical, staged,
            allow_selected_output=allow_selected_output,
        )
    except (ValueError, TypeError, OSError) as exc:
        raise _DownloadFileOwnershipError(str(exc)) from exc
    cancellation_checkpoint("download_file_ownership")


def _pending_download_paths(
    pending: PendingDownloadCommit, item: RunItemState, run_dir: Path,
) -> tuple[Path, Path]:
    """Enforce exact lexical layouts and reject resolved filesystem aliases."""
    filename = output_basename(
        item.order, item.visual_id, pending.variant,
        canonical_extension_for_image_format(pending.image_format),
    )
    if pending.canonical_relative_path != f"assets/{filename}":
        raise ValueError("Pending canonical path is not the deterministic candidate path.")
    staging_pattern = rf"assets/\.staging-[a-z0-9_-]+/v{pending.variant:02d}\.download"
    if not re.fullmatch(staging_pattern, pending.staging_relative_path):
        raise ValueError("Pending staging path does not match its permitted layout.")
    try:
        root = run_dir.resolve()
        assets = root / "assets"
        canonical = deterministic_candidate_path(root, filename)
        staged_lexical = root / pending.staging_relative_path
        staged = staged_lexical.resolve()
        if (
            assets.resolve() != assets or canonical != assets / filename
            or staged_lexical.parent.resolve() != staged_lexical.parent or staged != staged_lexical
        ):
            raise ValueError("Pending download paths contain symlink/junction redirection.")
    except (OSError, RuntimeError) as exc:
        raise ValueError("Pending download paths cannot be safely resolved.") from exc
    try:
        canonical.relative_to(root)
        staged.relative_to(root)
    except ValueError as exc:
        raise ValueError("Pending download path escapes the trusted run root.") from exc
    if canonical == staged:
        raise ValueError("Pending canonical and staging paths overlap.")
    return canonical, staged


def _validate_pending_download_content(
    pending: PendingDownloadCommit, canonical: Path, staged: Path,
) -> ValidatedImage:
    """Compare available files to earlier persisted evidence, never a new digest alone."""
    available = [path for path in (canonical, staged) if path.exists()]
    if not available:
        raise ValueError("Pending download has no available canonical or staging file.")
    metadata = []
    for path in available:
        try:
            actual = interruptible_call(
                "download_reconciliation", "pending_image_validation",
                validate_image_file, path, expected_filename=canonical.name,
            )
            if any(getattr(actual, field) != getattr(pending, field) for field in (
                "sha256", "byte_size", "image_format", "mime_type", "width", "height",
            )):
                raise ValueError("File integrity does not match the persisted pending record.")
        except Exception as exc:
            if path == canonical:
                raise FileExistsError(
                    f"Refusing to overwrite untracked candidate asset: {canonical}. "
                    "Its bytes do not match the persisted pending download evidence."
                ) from exc
            raise ValueError("Pending staging file does not match persisted image evidence.") from exc
        metadata.append(actual)
    return metadata[0]


def plan_download_one(
    batch: Batch,
    run_dir: Path,
    order: int,
    *,
    overwrite: bool = False,
) -> DownloadPlan:
    """Build a read-only one-item plan and fail before any browser interaction."""
    state, state_item = _download_state_item(batch, run_dir, order)
    asset_dir = run_asset_directory(run_dir)
    staging_dir = asset_dir / (".staging-" + uuid.uuid4().hex)
    try:
        staging_dir.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError("Planned staging directory cannot be safely inspected.") from exc
    else:
        raise ValueError("Planned staging directory already exists; refusing reuse.")
    expected_variants = list(range(1, len(state_item.candidates) + 1))
    actual_variants = [candidate.variant for candidate in state_item.candidates]
    if actual_variants != expected_variants:
        raise ValueError(
            "Persisted candidate order is not contiguous and stable; refusing to reorder downloads."
        )

    plans = []
    seen_destinations = set()
    for candidate in state_item.candidates:
        planned_staged_path = deterministic_staging_path(run_dir, staging_dir, candidate.variant)
        suffix = Path(candidate.suggested_filename).suffix.lower().lstrip(".")
        if suffix not in {"png", "jpg", "jpeg", "webp"}:
            raise ValueError(
                f"Candidate v{candidate.variant:02d} has unsupported deterministic "
                f"image extension {suffix or '(none)'!r}."
            )
        expected_filename = output_basename(
            state_item.order,
            state_item.visual_id,
            candidate.variant,
            suffix,
        )
        if candidate.suggested_filename != expected_filename:
            raise ValueError(
                f"Candidate v{candidate.variant:02d} filename is not deterministic: "
                f"{candidate.suggested_filename!r}."
            )
        destination = deterministic_candidate_path(run_dir, candidate.suggested_filename)
        stem = output_basename(
            state_item.order,
            state_item.visual_id,
            candidate.variant,
            "png",
        ).rsplit(".", 1)[0]
        candidate_destinations = tuple(
            deterministic_candidate_path(run_dir, f"{stem}.{extension}")
            for extension in ("png", "jpg", "jpeg", "webp")
        )
        existing_destinations = tuple(path for path in candidate_destinations if path.exists())
        if len(existing_destinations) > 1:
            raise FileExistsError(
                f"Multiple canonical-extension candidates already exist for v{candidate.variant:02d}."
            )
        existing_destination = existing_destinations[0] if existing_destinations else None
        destination_pattern = str(destination.parent / f"{stem}.<actual-extension>")
        if stem in seen_destinations:
            raise ValueError("Multiple candidates resolve to the same destination path.")
        seen_destinations.add(stem)

        action = "download"
        if candidate.pending_download_commit is not None:
            if overwrite:
                raise ValueError("Reconcile pending downloads without --overwrite first.")
            destination, staged = validate_pending_download_ownership(
                batch=batch, state=state, order=order, variant=candidate.variant, run_dir=run_dir,
            )
            if existing_destination is not None and existing_destination != destination:
                raise FileExistsError("Pending download conflicts with an existing canonical extension.")
            _validate_pending_download_content(candidate.pending_download_commit, destination, staged)
            action = "recover_pending"
        elif candidate.download_status == "completed":
            if existing_destination is not None and existing_destination != destination:
                raise ValueError(
                    f"Candidate v{candidate.variant:02d} metadata extension does not match its canonical asset."
                )
            try:
                _validate_recorded_download(candidate, destination)
                action = "overwrite" if overwrite else "skip_valid"
            except Exception as exc:
                if not overwrite:
                    raise ValueError(
                        f"Candidate v{candidate.variant:02d} is marked downloaded but its "
                        f"local asset is invalid: {exc}. Pass --overwrite to replace it."
                    ) from exc
                action = "overwrite"
        elif existing_destination is not None:
            if not overwrite:
                raise FileExistsError(
                    f"Refusing to overwrite untracked candidate asset: {existing_destination}. "
                    "Pass --overwrite only after inspecting it."
                )
            action = "overwrite"
        if action != "skip_valid":
            _assert_unique_download_ownership(state, state_item, candidate)
        if candidate.pending_download_commit is None:
            _assert_pending_download_file_claims(
                state, candidate, run_dir, destination, planned_staged_path,
                allow_selected_output=action in {"skip_valid", "overwrite"},
            )
        plans.append(
            CandidateDownloadPlan(
                candidate=candidate,
                destination=destination,
                destination_pattern=destination_pattern,
                existing_destination=existing_destination,
                action=action,
            )
        )
    return DownloadPlan(
        state=state,
        item=state_item,
        candidates=tuple(plans),
        asset_dir=asset_dir,
        staging_dir=staging_dir,
    )


def _persist_download_completion(
    state: RunState, item: RunItemState, candidate: CandidateRecord, destination: Path,
    metadata: ValidatedImage, browser_result: BrowserDownloadResult, run_dir: Path,
) -> None:
    """Complete one candidate and clear its receipt in a single atomic replacement."""
    if candidate.pending_download_commit is not None and any(
        getattr(metadata, field) != getattr(candidate.pending_download_commit, field)
        for field in ("sha256", "byte_size", "width", "height", "mime_type", "image_format")
    ):
        raise ValueError("Completion metadata does not match the persisted pending evidence.")
    completed = copy.deepcopy(state)
    completed_item = next(entry for entry in completed.items if entry.order == item.order)
    completed_candidate = next(entry for entry in completed_item.candidates if entry.variant == candidate.variant)
    downloaded_at = utc_now()
    completed_candidate.download_status = "completed"
    completed_candidate.suggested_filename = destination.name
    completed_candidate.candidate_id = browser_result.candidate_id
    completed_candidate.browser_suggested_filename = browser_result.browser_suggested_filename
    completed_candidate.browser_download_guid = browser_result.browser_download_guid
    completed_candidate.download_mechanism = browser_result.mechanism
    completed_candidate.local_path = str(destination)
    completed_candidate.local_relative_path = str(destination.relative_to(run_dir.resolve()))
    for field in ("sha256", "byte_size", "width", "height", "mime_type", "image_format"):
        setattr(completed_candidate, field, getattr(metadata, field))
    completed_candidate.downloaded_at = downloaded_at
    completed_candidate.download_error = None
    completed_candidate.pending_download_commit = None
    completed_item.output_files = [
        entry.local_path for entry in completed_item.candidates
        if entry.download_status == "completed" and entry.local_path
    ]
    completed.updated_at = downloaded_at
    interruptible_call(
        "download_completion_persistence", "candidate_completion_save",
        RunStateStore(run_dir / "run_state.json").save, completed,
    )
    # Keep caller references intact, but only publish completion after durable save.
    candidate.__dict__.update(completed_candidate.__dict__)
    item.output_files = completed_item.output_files
    state.updated_at = completed.updated_at
    state.version = completed.version


def _reconcile_pending_downloads(batch: Batch, run_dir: Path, plan: DownloadPlan) -> DownloadPlan:
    """Recover saved receipts locally before starting any new download attempt."""
    root = run_dir.resolve()
    for candidate_plan in plan.candidates:
        if candidate_plan.action != "recover_pending":
            continue
        cancellation_checkpoint("download_reconciliation")
        state = RunStateStore(root / "run_state.json").load()
        item = _matching_run_item(state, resolve_item(batch, plan.item.order))
        candidate = next(entry for entry in item.candidates if entry.variant == candidate_plan.candidate.variant)
        pending = candidate.pending_download_commit

        def checked_paths(*, refresh_state=False):
            nonlocal state, item, candidate
            if root.resolve() != root:
                raise ValueError("Trusted run root changed during pending recovery.")
            if refresh_state:
                state = RunStateStore(root / "run_state.json").load()
            paths = validate_pending_download_ownership(
                batch=batch, state=state, order=plan.item.order,
                variant=candidate_plan.candidate.variant, run_dir=root,
            )
            if paths[0] != candidate_plan.destination:
                raise ValueError("Pending candidate destination changed during recovery.")
            if refresh_state:
                item = _matching_run_item(state, resolve_item(batch, plan.item.order))
                candidate = next(entry for entry in item.candidates if entry.variant == candidate_plan.candidate.variant)
                if candidate.pending_download_commit != pending:
                    raise ValueError("Pending download evidence changed during recovery.")
            return paths

        canonical, staged = checked_paths()
        _validate_pending_download_content(pending, canonical, staged)
        if not canonical.exists():
            # Repeat path/content checks immediately around the exclusive install.
            if checked_paths() != (canonical, staged):
                raise ValueError("Pending download paths changed before installation.")
            _validate_pending_download_content(pending, canonical, staged)
            if checked_paths(refresh_state=True) != (canonical, staged):
                raise ValueError("Pending download ownership or paths changed before installation.")
            interruptible_call(
                "download_atomic_commit", "pending_canonical_commit",
                commit_staged_file, staged, canonical, overwrite=False,
            )
        if checked_paths() != (canonical, staged):
            raise ValueError("Pending download paths changed after installation.")
        metadata = _validate_pending_download_content(pending, canonical, staged)
        if not canonical.is_file() or checked_paths(refresh_state=True) != (canonical, staged):
            raise ValueError("Pending canonical file or path changed before completion.")
        _persist_download_completion(
            state, item, candidate, canonical, metadata,
            BrowserDownloadResult(
                candidate_id=pending.candidate_id, staged_path=str(staged),
                browser_suggested_filename=pending.browser_suggested_filename,
                browser_download_guid=pending.browser_download_guid, mechanism=pending.download_mechanism,
            ), root,
        )
    return plan_download_one(batch, root, plan.item.order)


def _reconcile_download_finalization(
    plan: DownloadPlan,
    run_dir: Path,
    *,
    expected_variants: int | None,
) -> DownloadPlan:
    """Finalize only an interrupted Order whose canonical assets already validate."""
    item = plan.item
    if item.download_status == "completed" and item.download_completed_at:
        return plan
    if not plan.candidates or any(candidate.action != "skip_valid" for candidate in plan.candidates):
        return plan  # Genuine missing downloads keep the existing download-only path.

    cancellation_checkpoint("download_finalization_reconciliation")
    if (
        item.status != "completed" or not item.completed_at
        or item.download_status not in {"running", "not_started", "completed"}
        or item.download_error is not None
        or any(candidate.download_error is not None for candidate in item.candidates)
    ):
        raise ValueError(f"Order {item.order} has conflicting download finalization state.")

    if expected_variants is None:
        # download-one has no runtime generation settings. Use saved production
        # evidence, never the remaining candidate-list length as the expected count.
        try:
            details = json.loads(item.result_details or "{}")
        except (TypeError, ValueError):
            details = None
        if isinstance(details, dict):
            expected_variants = details.get("configured_variants")
            if details.get("requested_variants", expected_variants) != expected_variants:
                raise ValueError("Saved generation variant counts conflict.")
    if (
        type(expected_variants) is not int or expected_variants not in {1, 2}
        or [candidate.variant for candidate in item.candidates]
        != list(range(1, expected_variants + 1))
    ):
        raise ValueError(f"Order {item.order} lacks the expected candidate/variant count proof.")

    destinations = [candidate.destination for candidate in plan.candidates]
    if len(item.output_files) != len(destinations) or [
        Path(path).resolve() for path in item.output_files
    ] != destinations:
        raise ValueError(f"Order {item.order} output_files do not match its canonical assets.")
    for candidate_plan in plan.candidates:
        candidate = candidate_plan.candidate
        cancellation_checkpoint("download_finalization_reconciliation")
        if not candidate.candidate_id or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            candidate.candidate_id,
        ):
            raise ValueError("Download finalization requires a persisted candidate UUID.")
        _assert_unique_download_ownership(plan.state, item, candidate)
        url_id = _persisted_candidate_id(replace(candidate, candidate_id=None))
        if url_id is not None and url_id != candidate.candidate_id:
            raise ValueError("Persisted candidate UUID conflicts with its recorded asset identity.")
        if candidate.local_relative_path is not None and (
            Path(candidate.local_relative_path).is_absolute()
            or (run_dir / candidate.local_relative_path).resolve() != candidate_plan.destination
        ):
            raise ValueError("Completed download relative path conflicts with its canonical asset.")
        # skip_valid was granted only by _validate_recorded_download: full Pillow
        # decode, regular/nonempty file, deterministic path, and every saved digest,
        # byte-size, format, MIME and dimension must match. Do not rewrite candidates.

    repaired = copy.deepcopy(plan.state)
    repaired_item = next(entry for entry in repaired.items if entry.order == item.order)
    repaired_item.download_status = "completed"
    repaired_item.download_error = None
    repaired_item.download_completed_at = utc_now()
    repaired.updated_at = repaired_item.download_completed_at
    cancellation_checkpoint("download_finalization_reconciliation")
    RunStateStore(run_dir / "run_state.json").save(repaired)
    return replace(plan, state=repaired, item=repaired_item)


def _canonical_asset_inventory(run_dir: Path) -> tuple[str, ...]:
    asset_dir = run_dir.resolve() / "assets"
    if not asset_dir.exists():
        return ()
    return tuple(
        sorted(
            str(path.relative_to(run_dir.resolve()))
            for path in asset_dir.rglob("*")
            if path.is_file()
        )
    )


def _assert_repair_target_has_no_canonical_assets(
    run_dir: Path,
    item: RunItemState,
) -> None:
    if item.output_files:
        raise ValueError(
            f"Order {item.order} records canonical output files; repair refused."
        )
    canonical_metadata_fields = (
        "local_path",
        "local_relative_path",
        "sha256",
        "byte_size",
        "width",
        "height",
        "mime_type",
        "image_format",
        "downloaded_at",
    )
    for candidate in item.candidates:
        if candidate.download_status == "completed" or any(
            getattr(candidate, field_name) is not None
            for field_name in canonical_metadata_fields
        ):
            raise ValueError(
                f"Order {item.order} v{candidate.variant:02d} records a canonical asset; "
                "repair refused."
            )

    asset_dir = run_dir.resolve() / "assets"
    prefix = f"{item.order:04d}__{item.visual_id}__v"
    if asset_dir.exists():
        matching_files = [
            path
            for path in asset_dir.iterdir()
            if path.is_file()
            and path.name.startswith(prefix)
            and path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        if matching_files:
            names = ", ".join(sorted(path.name for path in matching_files))
            raise ValueError(
                f"Order {item.order} has canonical asset file(s) {names}; repair refused."
            )


def _assert_earlier_orders_valid_for_repair(
    batch: Batch,
    run_dir: Path,
    state: RunState,
    target_order: int,
) -> tuple[int, ...]:
    earlier = tuple(item for item in batch.items if item.order < target_order)
    earlier_ownerships: dict[str, list[tuple[int, int]]] = {}
    for batch_item in earlier:
        state_item = _matching_run_item(state, batch_item)
        if state_item.status != "completed" or state_item.download_status != "completed":
            raise ValueError(
                f"Earlier order {batch_item.order} is not fully completed; repair refused."
            )
        if not state_item.candidates:
            raise ValueError(
                f"Earlier order {batch_item.order} has no persisted candidates; repair refused."
            )
        for candidate in state_item.candidates:
            candidate_id = _persisted_candidate_id(candidate)
            if not candidate_id:
                raise ValueError(
                    f"Earlier order {batch_item.order} v{candidate.variant:02d} has no "
                    "durable candidate UUID; repair refused."
                )
            earlier_ownerships.setdefault(candidate_id, []).append(
                (state_item.order, candidate.variant)
            )
        download_plan = plan_download_one(batch, run_dir, batch_item.order)
        if not download_plan.candidates or any(
            candidate.action != "skip_valid" for candidate in download_plan.candidates
        ):
            raise ValueError(
                f"Earlier order {batch_item.order} does not have fully validated canonical "
                "assets; repair refused."
            )

    duplicate_earlier = {
        candidate_id: owners
        for candidate_id, owners in earlier_ownerships.items()
        if len(owners) != 1
    }
    if duplicate_earlier:
        raise ValueError(
            "Earlier completed orders have ambiguous candidate UUID ownership; repair refused."
        )
    return tuple(item.order for item in earlier)


def plan_repair_order_candidates(
    batch: Batch,
    run_dir: Path,
    order: int,
) -> OrderCandidateRepairPlan:
    """Validate an exact ownership-conflict repair without mutating state or assets."""
    run_dir = run_dir.resolve()
    requested = resolve_item(batch, order)
    store = RunStateStore(run_dir / "run_state.json")
    if not store.exists():
        raise ValueError(f"No persisted run state exists at {store.path}.")
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")
    if len(state.items) != len(batch.items):
        raise ValueError("Run state item count does not match the supplied batch.")
    for batch_item in batch.items:
        _matching_run_item(state, batch_item)

    item = _matching_run_item(state, requested)
    if item.status != "completed" or not item.candidates:
        raise ValueError(
            f"Order {order} does not contain a completed candidate result to repair."
        )
    _assert_repair_target_has_no_canonical_assets(run_dir, item)

    ownerships = _candidate_ownerships(state)
    candidate_ids: list[tuple[int, str]] = []
    conflicts: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for candidate in item.candidates:
        candidate_id = _persisted_candidate_id(candidate)
        if not candidate_id:
            continue
        candidate_ids.append((candidate.variant, candidate_id))
        other_owners = tuple(
            owner for owner in ownerships.get(candidate_id, ()) if owner[0] != order
        )
        if other_owners:
            conflicts.append((candidate_id, other_owners))
    if not conflicts:
        raise ValueError(
            f"Order {order} has no candidate UUID conflict with another order; repair refused."
        )

    earlier_orders = _assert_earlier_orders_valid_for_repair(
        batch,
        run_dir,
        state,
        order,
    )
    later_orders = tuple(item.order for item in batch.items if item.order > order)
    return OrderCandidateRepairPlan(
        state=state,
        item=item,
        candidate_ids=tuple(candidate_ids),
        conflicts=tuple(conflicts),
        earlier_orders=earlier_orders,
        later_orders=later_orders,
        canonical_assets=_canonical_asset_inventory(run_dir),
    )


def execute_repair_order_candidates(
    batch: Batch,
    run_dir: Path,
    order: int,
) -> tuple[RunState, RunItemState]:
    """Atomically invalidate one proven-corrupt candidate result for explicit retry."""
    run_dir = run_dir.resolve()
    plan = plan_repair_order_candidates(batch, run_dir, order)
    state = plan.state
    item = plan.item

    candidate_failures = [
        f"v{candidate.variant:02d}: {candidate.download_error}"
        for candidate in item.candidates
        if candidate.download_error
    ]
    audit_parts = [
        f"{CANDIDATE_UUID_OWNERSHIP_CONFLICT}: explicit transactional repair "
        "cleared untrusted candidate ownership; regeneration requires --retry."
    ]
    if item.error:
        audit_parts.append(f"Previous generation error: {item.error}")
    if candidate_failures:
        audit_parts.append(
            "Previous candidate download failure(s): " + "; ".join(candidate_failures)
        )

    item.status = "failed"
    item.error = " ".join(audit_parts)
    item.generation_error_code = CANDIDATE_UUID_OWNERSHIP_CONFLICT
    item.candidates = []
    item.output_files = []
    item.result_details = None
    state.updated_at = utc_now()
    RunStateStore(run_dir / "run_state.json").save(state)
    return state, item


@contextmanager
def _download_staging_directory(
    asset_dir: Path, *, planned_dir: Path | None = None,
    preserve: Callable[[], bool] | None = None,
):
    temporary = None
    if planned_dir is None:
        # Retain the existing helper contract for unplanned local callers.
        temporary = tempfile.TemporaryDirectory(prefix=".staging-", dir=asset_dir)
        staging_dir = Path(temporary.name)
        created = staging_dir.lstat()
        identity = (created.st_dev, created.st_ino)
        cleanup = lambda: _cleanup_download_staging(staging_dir, identity, legacy_cleanup=temporary.cleanup)
    else:
        # Production validates this name during read-only planning. Exclusive
        # creation cannot reuse an existing directory or a link at that name.
        if planned_dir.parent != asset_dir:
            raise _DownloadFileOwnershipError("Planned staging directory has an unexpected parent.")
        deterministic_staging_path(asset_dir.parent, planned_dir, 1)
        staging_dir = planned_dir
        staging_dir.mkdir(mode=0o700)
        created = staging_dir.lstat()
        identity = (created.st_dev, created.st_ino)
        cleanup = lambda: _cleanup_download_staging(staging_dir, identity)
    ownership_rejected = False
    try:
        yield str(staging_dir)
    except _DownloadFileOwnershipError:
        # A changed snapshot may now claim these bytes in another receipt.
        # Do not erase disputed evidence while rejecting stale ownership.
        ownership_rejected = True
        raise
    finally:
        # Explicit cleanup owns this lifecycle. A rejected/failed preflight must
        # not be bypassed later by TemporaryDirectory's GC finalizer.
        if temporary is not None:
            temporary._finalizer.detach()
        if ownership_rejected or cancellation_requested() or (preserve is not None and preserve()):
            pass
        else:
            if staging_dir.resolve(strict=True) != staging_dir:
                raise _DownloadFileOwnershipError("Staging directory redirected before cleanup.")
            interruptible_call("download_cleanup", "staging_directory_cleanup", cleanup)


def _staging_path_stat(path: Path, root: Path):
    """Inspect a staging entry without accepting traversal or filesystem redirects."""
    if not path.is_absolute() or ".." in path.parts:
        raise _DownloadFileOwnershipError("Staging entry has an ambiguous path.")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _DownloadFileOwnershipError("Staging entry escapes its owned directory.") from exc
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or path.resolve(strict=True) != path
    ):
        raise _DownloadFileOwnershipError("Staging entry contains symlink/junction redirection.")
    return info


def _make_staged_file_writable(path: Path, staging_dir: Path) -> None:
    """Clear Windows read-only only while the regular file is exclusively staged."""
    info = _staging_path_stat(path, staging_dir)
    if not stat.S_ISREG(info.st_mode):
        raise _DownloadFileOwnershipError("Expected a regular staging file.")
    if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0):
        if info.st_nlink != 1:
            raise _DownloadFileOwnershipError("Refusing to change attributes of a shared staging file.")
        path.chmod(info.st_mode | stat.S_IWRITE)


def _cleanup_download_staging(
    staging_dir: Path, identity: tuple[int, int], *, legacy_cleanup: Callable[[], None] | None = None,
) -> None:
    """Use standard rmtree with a confined, single-file read-only retry."""
    def check_root():
        cancellation_checkpoint("download_cleanup")
        info = _staging_path_stat(staging_dir, staging_dir)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
            raise _DownloadFileOwnershipError("Owned staging directory was replaced before cleanup.")

    def onerror(error):
        raise error

    check_root()
    # Preflight with the standard walker; reject links before it can descend.
    for directory, dirs, files in os.walk(staging_dir, followlinks=False, onerror=onerror):
        check_root()
        for name in dirs + files:
            info = _staging_path_stat(Path(directory) / name, staging_dir)
            if (
                not stat.S_ISDIR(info.st_mode) and info.st_nlink != 1
                and (legacy_cleanup is not None or
                     getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0))
            ):
                raise _DownloadFileOwnershipError("Staging permission repair cannot touch shared file ownership.")

    if legacy_cleanup is not None:
        check_root()
        legacy_cleanup()  # Retain the proven unplanned TemporaryDirectory hook.
        return

    def retry_readonly_unlink(function, name, error):
        check_root()
        path = Path(name)
        info = _staging_path_stat(path, staging_dir)
        if (
            not isinstance(error, PermissionError) or function is not os.unlink
            or not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0)
        ):
            raise error
        _make_staged_file_writable(path, staging_dir)
        function(name)  # Exactly one retry; genuine deletion/ACL failures propagate.

    check_root()
    shutil.rmtree(staging_dir, onexc=retry_readonly_unlink)


def execute_download_one(
    batch: Batch,
    run_dir: Path,
    order: int,
    adapter: OneCandidateDownloadAdapter,
    *,
    overwrite: bool = False,
) -> tuple[RunState, RunItemState]:
    """Download and persist candidates one at a time without changing generation status."""
    plan = plan_download_one(batch, run_dir, order, overwrite=overwrite)
    if any(candidate.action == "recover_pending" for candidate in plan.candidates):
        plan = _reconcile_pending_downloads(batch, run_dir, plan)
    state = plan.state
    state_item = plan.item
    store = RunStateStore(run_dir / "run_state.json")
    pending = [candidate for candidate in plan.candidates if candidate.action != "skip_valid"]
    if not pending:
        plan = _reconcile_download_finalization(
            plan, run_dir, expected_variants=resolve_item(batch, order).variants,
        )
        return plan.state, plan.item

    for candidate_plan in pending:
        deterministic_candidate_path(run_dir, candidate_plan.candidate.suggested_filename)
        staged_path = deterministic_staging_path(run_dir, plan.staging_dir, candidate_plan.candidate.variant)
        _assert_current_download_file_claims(
            state, state_item, candidate_plan.candidate, run_dir, candidate_plan.destination, staged_path,
            allow_selected_output=candidate_plan.action == "overwrite",
        )
    state.version = max(state.version, 3)
    state_item.download_status = "running"
    state_item.download_attempt_count += 1
    state_item.download_started_at = utc_now()
    state_item.download_completed_at = None
    state_item.download_error = None
    state.updated_at = state_item.download_started_at
    store.save(state)

    last_completed_download = None
    plan.asset_dir.mkdir(parents=True, exist_ok=True)
    with _download_staging_directory(
        plan.asset_dir, planned_dir=plan.staging_dir,
        preserve=lambda: any(candidate.pending_download_commit is not None for candidate in state_item.candidates),
    ) as temp_dir:
        staging_dir = Path(temp_dir).resolve()
        if staging_dir != plan.staging_dir:
            raise _DownloadFileOwnershipError("Allocated staging directory differs from the validated plan.")
        for candidate_plan in plan.candidates:
            candidate = candidate_plan.candidate
            if candidate_plan.action == "skip_valid":
                continue
            staged_path = deterministic_staging_path(run_dir, staging_dir, candidate.variant)
            _assert_current_download_file_claims(
                state, state_item, candidate, run_dir, candidate_plan.destination, staged_path,
                allow_selected_output=candidate_plan.action == "overwrite",
            )
            candidate.download_status = "running"
            candidate.download_error = None
            state.updated_at = utc_now()
            store.save(state)
            try:
                _assert_current_download_file_claims(
                    state, state_item, candidate, run_dir, candidate_plan.destination, staged_path,
                    allow_selected_output=candidate_plan.action == "overwrite",
                )
                browser_result = interruptible_call(
                    "download_candidate_pipeline",
                    "adapter_download_candidate",
                    adapter.download_candidate,
                    candidate=candidate,
                    destination=staged_path,
                )
                if Path(browser_result.staged_path).resolve() != staged_path:
                    raise RuntimeError("Browser adapter returned an unexpected staging path.")
                if _persisted_candidate_id(candidate) != browser_result.candidate_id:
                    raise RuntimeError("Downloaded candidate identity changed unexpectedly.")
                staged_metadata = interruptible_call(
                    "download_file_validation",
                    "pillow_staging_validation",
                    validate_image_file,
                    staged_path,
                )
                final_extension = canonical_extension_for_image_format(
                    staged_metadata.image_format
                )
                final_filename = output_basename(
                    state_item.order,
                    state_item.visual_id,
                    candidate.variant,
                    final_extension,
                )
                final_destination = deterministic_candidate_path(run_dir, final_filename)
                if (
                    candidate_plan.existing_destination is not None
                    and candidate_plan.existing_destination != final_destination
                ):
                    raise FileExistsError(
                        "Existing candidate asset uses a different extension than the validated download; "
                        "refusing to leave ambiguous canonical files."
                    )
                # Legacy generic adapters may lack a UUID. They retain their old
                # download path and never gain automatic orphan reconciliation.
                has_uuid = isinstance(browser_result.candidate_id, str) and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    browser_result.candidate_id,
                )
                _assert_current_download_file_claims(
                    state, state_item, candidate, run_dir, final_destination, staged_path,
                    allow_selected_output=candidate_plan.action == "overwrite",
                )
                # Normalize before a canonical hard link exists; cleanup must
                # never chmod a staged hard link and alter the canonical asset.
                _make_staged_file_writable(staged_path, staging_dir)
                if has_uuid or state.flow_project_required:
                    pending_record = PendingDownloadCommit(
                        record_version=1, batch_id=state.batch_id,
                        project_id=state.flow_project.project_id if state.flow_project is not None else None,
                        order=state_item.order, visual_id=state_item.visual_id, variant=candidate.variant,
                        candidate_id=browser_result.candidate_id,
                        generation_attempt_count=state_item.attempt_count,
                        download_attempt_count=state_item.download_attempt_count,
                        canonical_relative_path=final_destination.relative_to(run_dir.resolve()).as_posix(),
                        staging_relative_path=staged_path.relative_to(run_dir.resolve()).as_posix(),
                        sha256=staged_metadata.sha256, byte_size=staged_metadata.byte_size,
                        image_format=staged_metadata.image_format, mime_type=staged_metadata.mime_type,
                        width=staged_metadata.width, height=staged_metadata.height,
                        download_mechanism=browser_result.mechanism,
                        browser_suggested_filename=browser_result.browser_suggested_filename,
                        browser_download_guid=browser_result.browser_download_guid,
                    )
                    pending_record.validate()
                    candidate.pending_download_commit = pending_record
                    interruptible_call(
                        "download_pending_persistence", "pending_download_save", store.save, state,
                    )
                    if _pending_download_paths(pending_record, state_item, run_dir) != (final_destination, staged_path):
                        raise ValueError("Pending paths changed before canonical installation.")
                _assert_current_download_file_claims(
                    state, state_item, candidate, run_dir, final_destination, staged_path,
                    allow_selected_output=candidate_plan.action == "overwrite",
                )
                interruptible_call(
                    "download_atomic_commit",
                    "atomic_canonical_commit",
                    commit_staged_file,
                    staged_path,
                    final_destination,
                    overwrite=(
                        candidate_plan.action == "overwrite"
                        and candidate_plan.existing_destination == final_destination
                    ),
                )
                metadata = interruptible_call(
                    "download_file_validation",
                    "pillow_canonical_validation",
                    validate_image_file,
                    final_destination,
                    expected_filename=final_filename,
                )
                if candidate.pending_download_commit is not None:
                    if _pending_download_paths(candidate.pending_download_commit, state_item, run_dir) != (
                        final_destination, staged_path,
                    ):
                        raise ValueError("Pending paths changed after canonical installation.")
                    metadata = _validate_pending_download_content(
                        candidate.pending_download_commit, final_destination, staged_path,
                    )
                _assert_current_download_file_claims(
                    state, state_item, candidate, run_dir, final_destination, staged_path,
                    allow_selected_output=candidate_plan.action == "overwrite",
                )
                _persist_download_completion(
                    state, state_item, candidate, final_destination, metadata, browser_result, run_dir,
                )
                last_completed_download = (candidate, final_destination, staged_path)
            except Exception as exc:
                if candidate.pending_download_commit is not None or isinstance(exc, _DownloadFileOwnershipError):
                    # Preserve a durable receipt or changed saved snapshot;
                    # neither completion nor failure may overwrite that evidence.
                    raise
                _assert_current_download_file_claims(
                    state, state_item, candidate, run_dir, candidate_plan.destination, staged_path,
                    allow_selected_output=candidate_plan.action == "overwrite",
                )
                error = f"{type(exc).__name__}: {exc}"
                candidate.download_status = "failed"
                candidate.download_error = error
                state_item.download_status = "failed"
                state_item.download_error = error
                state_item.download_completed_at = utc_now()
                state.updated_at = state_item.download_completed_at
                store.save(state)
                raise

    if last_completed_download is not None:
        candidate, canonical, staged = last_completed_download
        _assert_current_download_file_claims(
            state, state_item, candidate, run_dir, canonical, staged, allow_selected_output=True,
        )
    state_item.download_status = "completed"
    state_item.download_error = None
    state_item.download_completed_at = utc_now()
    state.updated_at = state_item.download_completed_at
    store.save(state)
    return state, state_item


def _result_extension(asset_url: str | None) -> str:
    if asset_url:
        suffix = Path(asset_url).suffix.lower().lstrip(".")
        if suffix in {"png", "jpg", "jpeg", "webp"}:
            return suffix
    return "png"


def execute_one(
    batch: Batch,
    run_dir: Path,
    order: int,
    adapter: OnePromptAdapter,
    settings: GenerationSettings,
    *,
    retry: bool = False,
    policy_retry_event: Callable[[int, str], None] | None = None,
    allow_automatic_policy_retry: bool = False,
) -> tuple[RunState, RunItemState]:
    """Execute exactly one item while persisting every material state transition."""
    settings.validate()
    requested = resolve_item(batch, order)
    _validate_declared_generation_settings(requested, settings)
    state = initialize_run_state(batch, run_dir)
    store = RunStateStore(run_dir / "run_state.json")
    state_item = next((item for item in state.items if item.visual_id == requested.visual_id), None)
    if state_item is None or not state_item.matches_source(requested):
        raise ValueError("Run state item identity does not match the source batch.")
    requested = replace(requested, prompt=state_item.prompt)

    if state_item.status == "completed":
        raise ValueError(
            f"{state_item.visual_id} is already completed; existing results will not be overwritten."
        )
    resume_pre_start = proven_pre_start_not_submitted(state_item)
    if state_item.status in {"failed", "running"} and not retry and not resume_pre_start:
        qualifier = "interrupted/running" if state_item.status == "running" else "failed"
        raise ValueError(f"{state_item.visual_id} is {qualifier}; pass --retry to make another attempt.")

    if state_item.status == "running":
        state_item.status = "failed"
        state_item.error = "Previous execution ended while this item was running."
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        store.save(state)

    previous_attempt_count = (
        max(0, state_item.attempt_count - 1)
        if resume_pre_start
        else state_item.attempt_count
    )
    prior_state_item = copy.deepcopy(state_item)
    forbidden_candidate_ids = frozenset(_candidate_ownerships(state))
    state_item.status = "running"
    state_item.attempt_count = previous_attempt_count + 1
    state_item.started_at = utc_now()
    state_item.completed_at = None
    state_item.error = None
    state_item.generation_error_code = None
    state_item.output_files = []
    state_item.candidates = []
    state_item.result_details = None
    state.updated_at = state_item.started_at
    store.save(state)

    unusual_retry_started = False

    def authorize_policy_failure_retry(exc: PolicyGenerationRejected) -> bool:
        nonlocal previous_attempt_count, prior_state_item
        cancellation_checkpoint("policy_retry_preparation")
        state_item.status = "failed"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        store.save(state)
        if state_item.automatic_policy_retry_count != 0:
            return False
        state_item.automatic_policy_retry_count = 1
        prior_state_item = copy.deepcopy(state_item)
        previous_attempt_count = state_item.attempt_count
        state_item.status = "running"
        state_item.attempt_count = previous_attempt_count + 1
        state_item.started_at = utc_now()
        state_item.completed_at = None
        state_item.error = None
        state_item.generation_error_code = None
        state.updated_at = state_item.started_at
        store.save(state)
        if policy_retry_event is not None:
            policy_retry_event(requested.order, "automatic_retry_started")
        return True

    def authorize_unusual_activity_retry() -> None:
        nonlocal previous_attempt_count, prior_state_item, unusual_retry_started
        if unusual_retry_started:
            raise RuntimeError("Unusual-activity retry was already authorized.")
        if state.flow_project is None:
            raise RuntimeError(
                f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: unusual-activity recovery "
                "requires this run's exact persisted Flow project."
            )
        state.flow_project.validate()
        if state_item.status != "running" or state_item.attempt_count < 1:
            raise RuntimeError(
                "Unusual-activity retry cannot start from the current Order state."
            )
        prior_state_item = copy.deepcopy(state_item)
        previous_attempt_count = state_item.attempt_count
        state_item.attempt_count = previous_attempt_count + 1
        state_item.started_at = utc_now()
        state_item.completed_at = None
        state_item.error = None
        state_item.generation_error_code = None
        state.updated_at = state_item.started_at
        store.save(state)
        unusual_retry_started = True

    def authorize_generic_failure_retry(
        exc: GenericGenerationFailure,
    ) -> bool:
        nonlocal previous_attempt_count, prior_state_item
        if state.flow_project is None:
            raise RuntimeError(
                f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: generic-failure recovery "
                "requires this run's exact persisted Flow project."
            )
        state.flow_project.validate()
        if state_item.status != "running" or state_item.attempt_count < 1:
            raise RuntimeError(
                "Generic-failure retry cannot start from the current Order state."
            )

        state_item.status = "failed"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        store.save(state)
        if (
            state_item.automatic_generic_failure_retry_count
            >= _AUTOMATIC_GENERIC_FAILURE_RETRY_LIMIT
        ):
            return False

        state_item.automatic_generic_failure_retry_count += 1
        prior_state_item = copy.deepcopy(state_item)
        previous_attempt_count = state_item.attempt_count
        state_item.status = "running"
        state_item.attempt_count = previous_attempt_count + 1
        state_item.started_at = utc_now()
        state_item.completed_at = None
        state_item.error = None
        state_item.generation_error_code = None
        state.updated_at = state_item.started_at
        store.save(state)
        return True

    try:
        same_session_generation = getattr(
            adapter,
            "generate_one_with_unusual_activity_recovery",
            None,
        )
        if callable(same_session_generation) and state.flow_project is not None:
            result = same_session_generation(
                item=requested,
                settings=settings,
                forbidden_candidate_ids=forbidden_candidate_ids,
                project=state.flow_project,
                retry_started=authorize_unusual_activity_retry,
                generic_failure_retry_started=authorize_generic_failure_retry,
                policy_failure_retry_started=(
                    authorize_policy_failure_retry if allow_automatic_policy_retry else None
                ),
            )
        else:
            result = adapter.generate_one(
                item=requested,
                settings=settings,
                forbidden_candidate_ids=forbidden_candidate_ids,
            )
        with cancellation_blocking_operation(
            "candidate_correlation",
            "candidate_uuid_ownership_validation",
        ):
            if not result.candidates:
                raise RuntimeError(
                    "Flow did not expose any confidently identifiable generated candidates."
                )
            if len(result.candidates) != settings.variants:
                raise RuntimeError(
                    f"{CANDIDATE_CORRELATION_UNPROVEN}: expected exactly "
                    f"{settings.variants} current-attempt candidates, observed "
                    f"{len(result.candidates)}."
                )
            candidate_ids = [candidate.candidate_id for candidate in result.candidates]
            if (
                any(not candidate_id for candidate_id in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)
                or not set(candidate_ids).isdisjoint(forbidden_candidate_ids)
            ):
                raise RuntimeError(
                    f"{CANDIDATE_CORRELATION_UNPROVEN}: current-attempt candidates "
                    "must have distinct data-media-id values that are new to this run."
                )

        state_item.candidates = [
            CandidateRecord(
                variant=index,
                suggested_filename=output_basename(
                    requested.order,
                    requested.visual_id,
                    index,
                    _result_extension(candidate.asset_url),
                ),
                source_kind=candidate.source_kind,
                candidate_id=candidate.candidate_id,
                accessible_name=candidate.accessible_name,
                asset_url=candidate.asset_url,
                signal=candidate.signal,
            )
            for index, candidate in enumerate(result.candidates, start=1)
        ]
        state_item.output_files = list(result.output_files)
        state_item.result_details = result.details
        state_item.status = "completed"
        state_item.generation_error_code = None
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        store.save(state)
        return state, state_item
    except KeyboardInterrupt as exc:
        evidence = _generation_interrupt_evidence(exc)
        if evidence is not None and evidence["submission_state"] == (
            GENERATION_SUBMISSION_NOT_SUBMITTED
        ) and evidence["start_mouse_down_sent"] is False:
            restored = copy.deepcopy(prior_state_item)
            restored.status = "pending"
            restored.attempt_count = previous_attempt_count
            restored.error = (
                f"{FLOW_GENERATION_NOT_SUBMITTED}: generation interrupted by the "
                "user before Start activation; "
                f"{_generation_interrupt_summary(evidence)}"
            )
            restored.generation_error_code = FLOW_GENERATION_NOT_SUBMITTED
            restored.completed_at = None
            state.items[state.items.index(state_item)] = restored
            state_item = restored
            state.updated_at = utc_now()
            store.save(state)
        elif evidence is not None:
            state_item.status = "running"
            state_item.error = (
                "KeyboardInterrupt: generation submission may already have been "
                f"sent; {_generation_interrupt_summary(evidence)}"
            )
            state_item.generation_error_code = None
            state_item.completed_at = None
            state.updated_at = utc_now()
            store.save(state)
        exc.order = requested.order
        raise
    except GenerationPreflightError as exc:
        state_item.attempt_count = previous_attempt_count
        state_item.status = "pending"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = None
        state.updated_at = utc_now()
        exc.order = requested.order
        store.save(state)
        raise
    except TemporaryGenerationBlock as exc:
        if not exc.attempt_activated:
            # The durable authorization was saved, but no browser generation action
            # was consumed. Preserve every historical attempt and remove only this
            # newly authorized preflight invocation from the count.
            state_item.attempt_count = previous_attempt_count
        state_item.status = exc.state_status
        state_item.error = f"{exc.code}: {exc}"
        state_item.generation_error_code = exc.code
        state_item.completed_at = None
        state.updated_at = utc_now()
        exc.order = requested.order
        store.save(state)
        raise
    except PolicyGenerationRejected as exc:
        state_item.status = "failed"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        exc.order = requested.order
        store.save(state)
        raise
    except UnusualActivityGenerationFailure as exc:
        state_item.status = "failed"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        exc.order = requested.order
        store.save(state)
        raise
    except (GenericGenerationFailure, HighDemandGenerationFailure) as exc:
        state_item.status = "failed"
        state_item.error = str(exc)
        state_item.generation_error_code = exc.code
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        exc.order = requested.order
        store.save(state)
        raise
    except Exception as exc:
        state_item.status = "failed"
        state_item.error = f"{type(exc).__name__}: {exc}"
        state_item.generation_error_code = None
        state_item.completed_at = utc_now()
        state.updated_at = state_item.completed_at
        store.save(state)
        raise


def prepare_persisted_flow_project(
    batch: Batch,
    run_dir: Path,
    adapter: SequentialBatchAdapter,
    settings: GenerationSettings | None,
) -> FlowProjectReference | None:
    """Prepare a managed run's saved project; legacy runs retain old behavior."""
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    if not store.exists():
        return None
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")
    if not state.flow_project_required:
        return None
    if state.flow_project is None:
        raise RuntimeError(
            f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: this run was reserved for a dedicated "
            "Flow project, but no new project identity was durably proven. Start a "
            "different new batch rather than risking a second creation click."
        )
    state.flow_project.validate()
    adapter.prepare_flow_project(project=state.flow_project, settings=settings)
    return state.flow_project


def _authorize_automatic_policy_retry(
    batch: Batch,
    run_dir: Path,
    item: BatchItem,
) -> None:
    """Durably consume this order's sole automatic policy retry before Start."""
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")
    state_item = _matching_run_item(state, item)
    if not (
        state_item.status == "failed"
        and state_item.generation_error_code == FLOW_POLICY_REJECTED
        and state_item.automatic_policy_retry_count == 0
    ):
        raise RuntimeError(
            "Automatic policy retry authorization is unavailable or already consumed."
        )
    state_item.automatic_policy_retry_count = 1
    state.updated_at = utc_now()
    store.save(state)


def _authorize_automatic_generic_failure_retry(
    batch: Batch,
    run_dir: Path,
    item: BatchItem,
) -> None:
    """Durably consume one of this Order's two exact generic-failure retries."""
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    state = store.load()
    if state.batch_id != batch.batch_id:
        raise ValueError("Run state belongs to a different semantic batch.")
    state_item = _matching_run_item(state, item)
    if not automatic_generic_failure_retry_available(state_item):
        raise RuntimeError(
            "Automatic generic-failure retry authorization is unavailable "
            "or exhausted."
        )
    state_item.automatic_generic_failure_retry_count += 1
    state.updated_at = utc_now()
    store.save(state)


def _execute_generation_with_policy_retry(
    batch: Batch,
    run_dir: Path,
    item: BatchItem,
    adapter: SequentialBatchAdapter,
    settings: GenerationSettings,
    *,
    retry: bool,
    resume_policy_retry: bool,
    success_outcome: str,
    policy_retry_event: Callable[[int, str], None] | None,
) -> str:
    """Run generation with at most one durably authorized policy-only retry."""

    def report(event: str) -> None:
        if policy_retry_event is not None:
            policy_retry_event(item.order, event)

    def run_retry() -> str:
        cancellation_checkpoint("policy_retry_dispatch")
        try:
            execute_one(
                batch,
                run_dir,
                item.order,
                adapter,
                settings,
                retry=True,
                policy_retry_event=policy_retry_event,
                allow_automatic_policy_retry=True,
            )
        except PolicyGenerationRejected:
            report("automatic_retry_exhausted")
            raise
        report("automatic_retry_succeeded")
        return "retried_policy_automatic"

    if resume_policy_retry:
        _authorize_automatic_policy_retry(batch, run_dir, item)
        report("automatic_retry_started")
        return run_retry()

    try:
        store = RunStateStore(run_dir / "run_state.json")
        before_policy_count = (
            _matching_run_item(store.load(), item).automatic_policy_retry_count
            if store.exists() else 0
        )
        execute_one(
            batch,
            run_dir,
            item.order,
            adapter,
            settings,
            retry=retry,
            policy_retry_event=policy_retry_event,
            allow_automatic_policy_retry=True,
        )
        after = _matching_run_item(RunStateStore(run_dir / "run_state.json").load(), item)
        if after.automatic_policy_retry_count > before_policy_count:
            report("automatic_retry_succeeded")
            return "retried_policy_automatic"
        return success_outcome
    except PolicyGenerationRejected:
        cancellation_checkpoint("policy_retry_preparation")
        store = RunStateStore(run_dir.resolve() / "run_state.json")
        failed_item = _matching_run_item(store.load(), item)
        if failed_item.automatic_policy_retry_count != 0:
            report("automatic_retry_exhausted")
            raise
        _authorize_automatic_policy_retry(batch, run_dir, item)
        report("automatic_retry_started")
        return run_retry()


def _execute_generation_with_automatic_retries(
    batch: Batch,
    run_dir: Path,
    item: BatchItem,
    adapter: SequentialBatchAdapter,
    settings: GenerationSettings,
    *,
    retry: bool,
    resume_policy_retry: bool,
    resume_generic_retry: bool,
    success_outcome: str,
    policy_retry_event: Callable[[int, str], None] | None,
) -> str:
    """Compose existing policy retry with two durable exact generic retries."""
    authorize_generic = resume_generic_retry
    generic_retry_used = False
    next_retry = retry or resume_generic_retry
    next_resume_policy_retry = resume_policy_retry
    while True:
        cancellation_checkpoint("generic_retry_loop")
        if authorize_generic:
            cancellation_checkpoint("generic_retry_authorization")
            _authorize_automatic_generic_failure_retry(batch, run_dir, item)
            generic_retry_used = True
            authorize_generic = False
        try:
            outcome = _execute_generation_with_policy_retry(
                batch,
                run_dir,
                item,
                adapter,
                settings,
                retry=next_retry,
                resume_policy_retry=next_resume_policy_retry,
                success_outcome=success_outcome,
                policy_retry_event=policy_retry_event,
            )
            return "retried_generic_failure_automatic" if generic_retry_used else outcome
        except GenericGenerationFailure:
            cancellation_checkpoint("generic_retry_failure_handling")
            store = RunStateStore(run_dir.resolve() / "run_state.json")
            failed_item = _matching_run_item(store.load(), item)
            if not automatic_generic_failure_retry_available(failed_item):
                raise
            authorize_generic = True
            next_retry = True
            next_resume_policy_retry = False


def _prepare_sequential_flow_project(
    batch: Batch,
    run_dir: Path,
    adapter: SequentialBatchAdapter,
    settings: GenerationSettings,
) -> FlowProjectReference:
    """Create once for a genuinely new run, then persist and prepare that project."""
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    state_existed = store.exists()
    state = initialize_run_state(
        batch,
        run_dir.resolve(),
        require_flow_project=not state_existed,
    )
    if not state.flow_project_required:
        raise RuntimeError("Legacy runs are not managed by the new-project path.")
    if state.flow_project is not None:
        state.flow_project.validate()
        adapter.prepare_flow_project(project=state.flow_project, settings=settings)
        return state.flow_project
    if state_existed:
        raise RuntimeError(
            f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: this run was reserved for a dedicated "
            "Flow project, but no new project identity was durably proven. Start a "
            "different new batch rather than risking a second creation click."
        )

    project = adapter.create_flow_project()
    project.validate()
    current = store.load()
    if current.batch_id != batch.batch_id or not current.flow_project_required:
        raise RuntimeError(
            f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: run ownership changed before the "
            "new Flow project could be persisted."
        )
    if current.flow_project is not None:
        raise RuntimeError(
            f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: the run already contains a Flow "
            "project identity and it cannot be replaced."
        )
    current.flow_project = project
    current.version = max(current.version, 4)
    current.updated_at = utc_now()
    store.save(current)

    # The durable project binding exists before the production readiness gate and
    # therefore before any possible Order 1 prompt submission.
    adapter.prepare_flow_project(project=project, settings=settings)
    return project


def execute_sequential_batch(
    batch: Batch,
    run_dir: Path,
    adapter: SequentialBatchAdapter,
    settings: GenerationSettings,
    *,
    retry_failed_generation: bool = False,
    order_completed=None,
    policy_retry_event: Callable[[int, str], None] | None = None,
    manage_flow_project: bool = False,
) -> tuple[RunState, tuple[SequentialOrderOutcome, ...]]:
    """Process validated batch order sequentially via the proven one-order paths."""
    plan = plan_sequential_batch(
        batch,
        run_dir,
        settings,
        retry_failed_generation=retry_failed_generation,
    )
    blocked = [
        order
        for order in plan.orders
        if order.generation_action == "blocked_retry_required"
    ]
    if blocked:
        blocked_orders = ", ".join(str(order.order) for order in blocked)
        raise ValueError(
            f"Generation retry required for order(s) {blocked_orders}; "
            "rerun run-batch with --retry."
        )

    if manage_flow_project:
        store = RunStateStore(run_dir.resolve() / "run_state.json")
        if store.exists() and not store.load().flow_project_required:
            # Historical states have no project ownership metadata and must keep
            # their configured-project behavior without guessing or mutation here.
            pass
        else:
            _prepare_sequential_flow_project(batch, run_dir, adapter, settings)

    outcomes: list[SequentialOrderOutcome] = []
    store = RunStateStore(run_dir.resolve() / "run_state.json")
    for item in batch.items:
        cancellation_checkpoint("sequential_order_dispatch")
        if store.exists():
            current = store.load()
            state_item = _matching_run_item(current, item)
        else:
            state_item = RunItemState(
                order=item.order,
                visual_id=item.visual_id,
                prompt=item.prompt,
            )

        if state_item.status == "completed":
            generation_outcome = "skipped_completed"
        else:
            cancellation_checkpoint("generation_dispatch")
            was_retry = state_item.status in {"failed", "running"}
            was_pre_start_resume = proven_pre_start_not_submitted(state_item)
            was_external_block = state_item.status == WAITING_FOR_FLOW_LIMIT_RESET
            resume_policy_retry = (
                state_item.status == "failed"
                and state_item.generation_error_code == FLOW_POLICY_REJECTED
                and state_item.automatic_policy_retry_count == 0
            )
            resume_generic_retry = automatic_generic_failure_retry_available(
                state_item
            )
            generation_outcome = _execute_generation_with_automatic_retries(
                batch,
                run_dir,
                item,
                adapter,
                settings,
                retry=was_retry and retry_failed_generation,
                resume_policy_retry=resume_policy_retry,
                resume_generic_retry=resume_generic_retry,
                success_outcome=(
                    "resumed_pre_start"
                    if was_pre_start_resume
                    else "retried"
                    if was_retry
                    else (
                        "resumed_external_block"
                        if was_external_block
                        else "generated"
                    )
                ),
                policy_retry_event=policy_retry_event,
            )

        download_plan = plan_download_one(batch, run_dir, item.order)
        pending_downloads = [
            candidate
            for candidate in download_plan.candidates
            if candidate.action != "skip_valid"
        ]
        previously_completed = any(
            candidate.action == "skip_valid" for candidate in download_plan.candidates
        )
        if not pending_downloads:
            download_plan = _reconcile_download_finalization(
                download_plan, run_dir, expected_variants=settings.variants,
            )
            download_outcome = "skipped_valid"
        else:
            cancellation_checkpoint("download_dispatch")
            execute_download_one(batch, run_dir, item.order, adapter)
            download_outcome = "resumed" if previously_completed or any(
                candidate.action == "recover_pending" for candidate in pending_downloads
            ) else "downloaded"

        completed_state = store.load()
        completed_item = _matching_run_item(completed_state, item)
        if not (
            completed_item.status == "completed"
            and completed_item.download_status == "completed"
            and completed_item.candidates
            and all(
                candidate.download_status == "completed"
                for candidate in completed_item.candidates
            )
        ):
            raise RuntimeError(
                f"Order {item.order} did not reach complete generation and download state."
            )
        outcome = SequentialOrderOutcome(
            order=item.order,
            visual_id=item.visual_id,
            generation=generation_outcome,
            downloads=download_outcome,
        )
        outcomes.append(outcome)
        if order_completed is not None:
            order_completed(outcome)

    final_state = store.load()
    return final_state, tuple(outcomes)
