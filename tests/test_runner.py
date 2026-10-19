import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.errors import (
    CANDIDATE_UUID_OWNERSHIP_CONFLICT,
    FLOW_GENERATION_NOT_SUBMITTED,
    FLOW_USAGE_LIMIT_REACHED,
    GENERATION_INTERRUPT_EVIDENCE_SCHEMA,
    GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT,
    GENERATION_SUBMISSION_NOT_SUBMITTED,
    GenerationPreflightError,
    WAITING_FOR_FLOW_LIMIT_RESET,
    TemporaryGenerationBlock,
)
from nano_batch.models import (
    Batch,
    BatchItem,
    BrowserDownloadResult,
    CandidateRecord,
    DiscoveredCandidate,
    GenerationResult,
    GenerationSettings,
)
from nano_batch.runner import (
    execute_download_one,
    execute_one,
    execute_repair_order_candidates,
    execute_sequential_batch,
    initialize_run_state,
    output_basename,
    plan_download_one,
    plan_repair_order_candidates,
    plan_sequential_batch,
)
from nano_batch.state import RunStateStore, StatePersistenceError


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result = result or GenerationResult(
            candidates=(
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id="fresh-candidate-v01",
                    asset_url="https://example.test/a.png",
                ),
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id="fresh-candidate-v02",
                    asset_url="https://example.test/b.png",
                ),
            )
        )
        self.error = error
        self.calls = []
        self.forbidden_candidate_ids = []

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        self.calls.append((item, settings))
        self.forbidden_candidate_ids.append(frozenset(forbidden_candidate_ids))
        if self.error is not None:
            raise self.error
        return self.result


def generation_interrupt(
    *,
    phase: str,
    prompt_written: bool,
    start_mouse_down_sent: bool,
    start_acknowledged: bool = False,
) -> KeyboardInterrupt:
    exc = KeyboardInterrupt()
    exc.generation_interrupt_schema = GENERATION_INTERRUPT_EVIDENCE_SCHEMA
    exc.interrupt_phase = phase
    exc.prompt_written = prompt_written
    exc.start_mouse_down_sent = start_mouse_down_sent
    exc.start_acknowledged = start_acknowledged
    exc.submission_state = (
        GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT
        if start_mouse_down_sent
        else GENERATION_SUBMISSION_NOT_SUBMITTED
    )
    return exc


class FakeDownloadAdapter:
    def __init__(self, *, fail_variants=(), invalid_variants=(), image_format="PNG"):
        self.fail_variants = set(fail_variants)
        self.invalid_variants = set(invalid_variants)
        self.image_format = image_format
        self.calls = []

    def download_candidate(self, *, candidate, destination):
        self.calls.append(candidate.variant)
        if candidate.variant in self.fail_variants:
            raise RuntimeError(f"synthetic download failure v{candidate.variant:02d}")
        if candidate.variant in self.invalid_variants:
            destination.write_bytes(b"not an image")
        else:
            Image.new(
                "RGB",
                (candidate.variant + 5, candidate.variant + 3),
                (20 * candidate.variant, 30, 40),
            ).save(destination, format=self.image_format)
        candidate_id = candidate.asset_url.rsplit("/", 1)[-1]
        return BrowserDownloadResult(
            candidate_id=candidate_id,
            staged_path=str(destination),
            browser_suggested_filename=(
                f"flow-{candidate.variant}.jpeg"
                if self.image_format == "JPEG"
                else f"flow-{candidate.variant}.png"
            ),
            browser_download_guid=f"guid-{candidate.variant}",
            mechanism="cdp_download_events",
        )


class SequentialFakeAdapter:
    def __init__(
        self,
        *,
        fail_generation_orders=(),
        fail_downloads=(),
        flow_limit_orders=(),
        flow_limit_attempt_activated=True,
    ):
        self.fail_generation_orders = set(fail_generation_orders)
        self.fail_downloads = set(fail_downloads)
        self.flow_limit_orders = set(flow_limit_orders)
        self.flow_limit_attempt_activated = flow_limit_attempt_activated
        self.events = []
        self.forbidden_candidate_ids_by_order = {}

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        self.events.append(("generate", item.order))
        self.forbidden_candidate_ids_by_order[item.order] = frozenset(
            forbidden_candidate_ids
        )
        if item.order in self.flow_limit_orders:
            raise TemporaryGenerationBlock(
                "visible Flow usage limit",
                code=FLOW_USAGE_LIMIT_REACHED,
                state_status=WAITING_FOR_FLOW_LIMIT_RESET,
                attempt_activated=self.flow_limit_attempt_activated,
            )
        if item.order in self.fail_generation_orders:
            raise RuntimeError(f"synthetic generation failure order {item.order}")
        return GenerationResult(
            candidates=tuple(
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id=f"order-{item.order}-v{variant:02d}",
                    asset_url=(
                        "https://flow-content.google/image/"
                        f"order-{item.order}-v{variant:02d}"
                    ),
                )
                for variant in (1, 2)
            ),
            details=json.dumps({"configured_variants": settings.variants, "requested_variants": settings.variants}),
        )

    def download_candidate(self, *, candidate, destination):
        order = int(candidate.suggested_filename[:4])
        key = (order, candidate.variant)
        self.events.append(("download", order, candidate.variant))
        if key in self.fail_downloads:
            raise RuntimeError(
                f"synthetic download failure order {order} v{candidate.variant:02d}"
            )
        Image.new(
            "RGB",
            (candidate.variant + 5, order + 4),
            (20 * candidate.variant, 30 * order, 40),
        ).save(destination, format="JPEG")
        return BrowserDownloadResult(
            candidate_id=str(candidate.candidate_id),
            staged_path=str(destination),
            browser_suggested_filename=f"order-{order}-v{candidate.variant:02d}.jpeg",
            browser_download_guid=f"guid-{order}-{candidate.variant}",
            mechanism="cdp_download_events",
        )


class RunnerTests(unittest.TestCase):
    def sample_batch(self):
        return Batch(
            batch_id="sha256:abc",
            source_schema="test",
            items=(
                BatchItem(order=1, visual_id="scene_0001_visual_001", prompt="a"),
                BatchItem(order=2, visual_id="scene_0002_visual_001", prompt="b"),
                BatchItem(order=3, visual_id="scene_0003_visual_001", prompt="c"),
            ),
        )

    def generic_batch(self, item_count):
        return Batch(
            batch_id=f"sha256:generic-{item_count}",
            source_schema="test",
            items=tuple(
                BatchItem(
                    order=order,
                    visual_id=f"scene_{order:04d}_visual_001",
                    prompt=f"prompt {order}",
                )
                for order in range(1, item_count + 1)
            ),
        )

    def completed_download_run(self, run_dir):
        state = initialize_run_state(self.sample_batch(), run_dir)
        item = state.items[0]
        item.status = "completed"
        item.attempt_count = 2
        item.completed_at = "2026-01-01T00:00:00+00:00"
        item.result_details = '{"configured_variants":2,"requested_variants":2}'
        item.candidates = [
            CandidateRecord(
                variant=1,
                suggested_filename="0001__scene_0001_visual_001__v01.png",
                source_kind="rendered-image",
                asset_url="https://flow-content.google/image/candidate-one",
            ),
            CandidateRecord(
                variant=2,
                suggested_filename="0001__scene_0001_visual_001__v02.png",
                source_kind="rendered-image",
                asset_url="https://flow-content.google/image/candidate-two",
            ),
        ]
        RunStateStore(run_dir / "run_state.json").save(state)
        return state

    def accepted_order_one_run(self, run_dir):
        self.completed_download_run(run_dir)
        execute_download_one(
            self.sample_batch(),
            run_dir,
            1,
            FakeDownloadAdapter(image_format="JPEG"),
        )
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].download_attempt_count = 8
        RunStateStore(run_dir / "run_state.json").save(state)
        return state

    def ownership_conflict_repair_run(self, run_dir, item_count=6):
        batch = self.generic_batch(item_count)
        adapter = SequentialFakeAdapter()
        settings = GenerationSettings()
        for order in (1, 2, 3):
            execute_one(batch, run_dir, order, adapter, settings)
            execute_download_one(batch, run_dir, order, adapter)

        store = RunStateStore(run_dir / "run_state.json")
        state = store.load()
        target = state.items[3]
        target.status = "completed"
        target.attempt_count = 1
        target.started_at = "2026-01-04T01:02:03+00:00"
        target.completed_at = "2026-01-04T01:03:04+00:00"
        target.error = "historical generation diagnostic"
        target.generation_error_code = None
        target.result_details = "untrusted generation correlation"
        target.download_status = "failed"
        target.download_attempt_count = 2
        target.download_started_at = "2026-01-04T01:04:05+00:00"
        target.download_completed_at = "2026-01-04T01:05:06+00:00"
        target.download_error = "historical item download failure"
        target.output_files = []
        target.candidates = [
            CandidateRecord(
                variant=1,
                suggested_filename="0004__scene_0004_visual_001__v01.png",
                source_kind="rendered-image",
                candidate_id="order-3-v02",
                asset_url="https://flow-content.google/image/order-3-v02",
                download_status="failed",
                download_error="historical candidate download failure",
            ),
            CandidateRecord(
                variant=2,
                suggested_filename="0004__scene_0004_visual_001__v02.png",
                source_kind="rendered-image",
                candidate_id="order-2-v02",
                asset_url="https://flow-content.google/image/order-2-v02",
            ),
        ]
        store.save(state)
        return batch

    def test_initialize_is_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            first = initialize_run_state(self.sample_batch(), run_dir)
            first.items[0].status = "completed"
            RunStateStore(run_dir / "run_state.json").save(first)
            second = initialize_run_state(self.sample_batch(), run_dir)
            self.assertEqual(second.items[0].status, "completed")

    def test_output_filename_preserves_order_identity_and_variant(self):
        self.assertEqual(output_basename(7, "scene_0007_visual_001", 2, "png"), "0007__scene_0007_visual_001__v02.png")

    def test_unsafe_visual_id_rejected(self):
        with self.assertRaises(ValueError):
            output_basename(1, "../bad", 1, "png")

    def test_positive_variant_required(self):
        with self.assertRaises(ValueError):
            output_basename(1, "good", 0, "png")

    def test_success_updates_only_selected_item_and_preserves_identity(self):
        result = GenerationResult(
            candidates=(
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id="fresh-candidate-v01",
                    asset_url="https://example.test/a.webp",
                ),
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id="fresh-candidate-v02",
                    accessible_name="candidate two",
                ),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            adapter = FakeAdapter(result=result)
            state, item = execute_one(
                self.sample_batch(), Path(tmp), 2, adapter, GenerationSettings()
            )
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.visual_id, "scene_0002_visual_001")
            self.assertEqual(item.attempt_count, 1)
            self.assertEqual(state.items[0].status, "pending")
            self.assertEqual(item.candidates[0].suggested_filename, "0002__scene_0002_visual_001__v01.webp")
            self.assertEqual(item.candidates[1].suggested_filename, "0002__scene_0002_visual_001__v02.png")

    def test_generation_receives_all_persisted_candidate_uuids_as_forbidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = self.completed_download_run(run_dir)
            before_order_one = state.items[0]
            adapter = FakeAdapter()
            execute_one(
                self.sample_batch(),
                run_dir,
                2,
                adapter,
                GenerationSettings(),
            )
            self.assertEqual(
                adapter.forbidden_candidate_ids,
                [frozenset({"candidate-one", "candidate-two"})],
            )
            persisted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(persisted.items[0], before_order_one)

    def test_historical_candidate_in_generation_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = self.completed_download_run(run_dir)
            before_order_one = state.items[0]
            result = GenerationResult(
                candidates=(
                    DiscoveredCandidate(
                        source_kind="rendered-image",
                        candidate_id="fresh-order-two-v01",
                    ),
                    DiscoveredCandidate(
                        source_kind="rendered-image",
                        candidate_id="candidate-two",
                    ),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "CANDIDATE_CORRELATION_UNPROVEN"):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    2,
                    FakeAdapter(result=result),
                    GenerationSettings(),
                )
            persisted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(persisted.items[0], before_order_one)
            self.assertEqual(persisted.items[1].status, "failed")
            self.assertEqual(persisted.items[1].candidates, [])

    def test_duplicate_uuid_ownership_blocks_download_before_adapter_or_state_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = initialize_run_state(self.sample_batch(), run_dir)
            duplicate_id = "duplicate-candidate-uuid"
            for item, order in ((state.items[0], 1), (state.items[1], 2)):
                item.status = "completed"
                item.candidates = [
                    CandidateRecord(
                        variant=1,
                        suggested_filename=(
                            f"{order:04d}__scene_{order:04d}_visual_001__v01.png"
                        ),
                        source_kind="rendered-image",
                        candidate_id=duplicate_id,
                        download_status="not_started",
                    )
                ]
            store = RunStateStore(run_dir / "run_state.json")
            store.save(state)
            before = store.path.read_bytes()
            adapter = FakeDownloadAdapter()

            with self.assertRaisesRegex(ValueError, "CANDIDATE_UUID_OWNERSHIP_CONFLICT"):
                execute_download_one(self.sample_batch(), run_dir, 2, adapter)

            self.assertEqual(adapter.calls, [])
            self.assertEqual(store.path.read_bytes(), before)
            after = store.load()
            self.assertEqual(after.items[0], state.items[0])
            self.assertEqual(after.items[2], state.items[2])

    def test_failure_is_persisted_without_shifting_later_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=RuntimeError("synthetic failure")),
                    GenerationSettings(),
                )
            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(state.items[0].visual_id, "scene_0001_visual_001")
            self.assertEqual(state.items[0].status, "failed")
            self.assertEqual(state.items[1].visual_id, "scene_0002_visual_001")
            self.assertEqual(state.items[1].status, "pending")

    def test_failed_item_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(RuntimeError):
                execute_one(
                    self.sample_batch(), run_dir, 1, FakeAdapter(error=RuntimeError("fail")), GenerationSettings()
                )
            adapter = FakeAdapter()
            with self.assertRaisesRegex(ValueError, "--retry"):
                execute_one(self.sample_batch(), run_dir, 1, adapter, GenerationSettings())
            self.assertEqual(adapter.calls, [])

    def test_proven_pre_start_failure_is_pending_and_resumes_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            error = GenerationPreflightError(
                "active composer Start ownership is ambiguous",
                code=FLOW_GENERATION_NOT_SUBMITTED,
            )
            with self.assertRaises(GenerationPreflightError):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=error),
                    GenerationSettings(),
                )

            persisted = RunStateStore(run_dir / "run_state.json").load()
            item = persisted.items[0]
            self.assertEqual(item.status, "pending")
            self.assertEqual(item.attempt_count, 0)
            self.assertEqual(item.automatic_policy_retry_count, 0)
            self.assertEqual(
                item.generation_error_code, FLOW_GENERATION_NOT_SUBMITTED
            )
            self.assertIsNone(item.completed_at)
            self.assertEqual(item.candidates, [])
            self.assertEqual(item.download_status, "not_started")
            self.assertEqual(
                plan_sequential_batch(
                    self.sample_batch(), run_dir, GenerationSettings()
                ).orders[0].generation_action,
                "generate",
            )

            _, resumed = execute_one(
                self.sample_batch(),
                run_dir,
                1,
                FakeAdapter(),
                GenerationSettings(),
            )
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(resumed.attempt_count, 1)
            self.assertEqual(resumed.automatic_policy_retry_count, 0)

    def test_proven_pre_start_interrupt_rolls_back_and_resumes_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            error = generation_interrupt(
                phase="post_input_start_actionability",
                prompt_written=True,
                start_mouse_down_sent=False,
            )
            with self.assertRaises(KeyboardInterrupt):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=error),
                    GenerationSettings(),
                )

            persisted = RunStateStore(run_dir / "run_state.json").load()
            item = persisted.items[0]
            self.assertEqual(item.status, "pending")
            self.assertEqual(item.attempt_count, 0)
            self.assertEqual(item.automatic_policy_retry_count, 0)
            self.assertEqual(
                item.generation_error_code, FLOW_GENERATION_NOT_SUBMITTED
            )
            self.assertEqual(item.candidates, [])
            self.assertEqual(item.output_files, [])
            self.assertEqual(item.download_status, "not_started")
            self.assertEqual(item.download_attempt_count, 0)
            self.assertIn(
                "submission_state=not_submitted", item.error or ""
            )
            self.assertEqual(
                plan_sequential_batch(
                    self.sample_batch(), run_dir, GenerationSettings()
                ).orders[0].generation_action,
                "generate",
            )

            _, resumed = execute_one(
                self.sample_batch(),
                run_dir,
                1,
                FakeAdapter(),
                GenerationSettings(),
            )
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(resumed.attempt_count, 1)

    def test_interrupt_after_mouse_down_retains_ambiguous_running_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            error = generation_interrupt(
                phase="start_acknowledgement_wait",
                prompt_written=True,
                start_mouse_down_sent=True,
            )
            with self.assertRaises(KeyboardInterrupt):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=error),
                    GenerationSettings(),
                )

            persisted = RunStateStore(run_dir / "run_state.json").load()
            item = persisted.items[0]
            self.assertEqual(item.status, "running")
            self.assertEqual(item.attempt_count, 1)
            self.assertIsNone(item.generation_error_code)
            self.assertIn(
                "submission_state=ambiguous_or_sent", item.error or ""
            )
            self.assertEqual(
                plan_sequential_batch(
                    self.sample_batch(), run_dir, GenerationSettings()
                ).orders[0].generation_action,
                "blocked_retry_required",
            )
            with self.assertRaisesRegex(ValueError, "--retry"):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(),
                    GenerationSettings(),
                )

    def test_pre_start_interrupt_preserves_retry_counters_and_prior_candidate_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            batch = self.sample_batch()
            state = initialize_run_state(batch, run_dir)
            item = state.items[0]
            item.status = "failed"
            item.attempt_count = 1
            item.automatic_policy_retry_count = 1
            item.automatic_generic_failure_retry_count = 1
            item.error = "prior authorized retry"
            item.candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="preserved.jpeg",
                    source_kind="rendered-image",
                    candidate_id="preserved-candidate-id",
                )
            ]
            item.output_files = ["preserved.jpeg"]
            RunStateStore(run_dir / "run_state.json").save(state)
            error = generation_interrupt(
                phase="pre_start_activation",
                prompt_written=True,
                start_mouse_down_sent=False,
            )

            with self.assertRaises(KeyboardInterrupt):
                execute_one(
                    batch,
                    run_dir,
                    1,
                    FakeAdapter(error=error),
                    GenerationSettings(),
                    retry=True,
                )

            preserved = RunStateStore(run_dir / "run_state.json").load().items[0]
            self.assertEqual(preserved.status, "pending")
            self.assertEqual(preserved.attempt_count, 1)
            self.assertEqual(preserved.automatic_policy_retry_count, 1)
            self.assertEqual(
                preserved.automatic_generic_failure_retry_count,
                1,
            )
            self.assertEqual(preserved.output_files, ["preserved.jpeg"])
            self.assertEqual(len(preserved.candidates), 1)
            self.assertEqual(
                preserved.candidates[0].candidate_id,
                "preserved-candidate-id",
            )
            self.assertEqual(preserved.download_status, "not_started")
            self.assertEqual(preserved.download_attempt_count, 0)

    def test_incomplete_not_submitted_interrupt_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            error = KeyboardInterrupt()
            error.generation_interrupt_schema = GENERATION_INTERRUPT_EVIDENCE_SCHEMA
            error.interrupt_phase = "composer_resolution"
            error.submission_state = GENERATION_SUBMISSION_NOT_SUBMITTED

            with self.assertRaises(KeyboardInterrupt):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=error),
                    GenerationSettings(),
                )

            preserved = RunStateStore(run_dir / "run_state.json").load().items[0]
            self.assertEqual(preserved.status, "running")
            self.assertEqual(preserved.attempt_count, 1)
            self.assertIsNone(preserved.generation_error_code)

    def test_exact_legacy_global_count_failure_resumes_without_extra_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            batch = self.sample_batch()
            execute_one(batch, run_dir, 1, FakeAdapter(), GenerationSettings())
            store = RunStateStore(run_dir / "run_state.json")
            state = store.load()
            previous_order = state.items[0]
            legacy = state.items[1]
            legacy.status = "failed"
            legacy.attempt_count = 1
            legacy.error = (
                "FlowAutomationError: Image-mode preflight requires exactly one "
                "visible generation control; found 2."
            )
            legacy.generation_error_code = None
            legacy.result_details = None
            legacy.candidates = []
            legacy.output_files = []
            legacy.download_status = "not_started"
            legacy.download_attempt_count = 0
            store.save(state)

            plan = plan_sequential_batch(batch, run_dir, GenerationSettings())
            self.assertEqual(plan.orders[1].generation_action, "resume_pre_start")
            _, resumed = execute_one(
                batch, run_dir, 2, SequentialFakeAdapter(), GenerationSettings()
            )

            final = store.load()
            self.assertEqual(final.items[0], previous_order)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(resumed.attempt_count, 1)

    def test_legacy_pre_start_recognition_fails_closed_on_ambiguous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            batch = self.sample_batch()
            state = initialize_run_state(batch, run_dir)
            item = state.items[0]
            item.status = "failed"
            item.attempt_count = 1
            item.error = (
                "FlowAutomationError: Image-mode preflight requires exactly one "
                "visible generation control; found 2."
            )
            item.result_details = "post-start observation exists"
            RunStateStore(run_dir / "run_state.json").save(state)

            plan = plan_sequential_batch(batch, run_dir, GenerationSettings())
            self.assertEqual(
                plan.orders[0].generation_action, "blocked_retry_required"
            )
            with self.assertRaisesRegex(ValueError, "--retry"):
                execute_one(
                    batch, run_dir, 1, FakeAdapter(), GenerationSettings()
                )

    def test_retry_increments_attempt_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(RuntimeError):
                execute_one(
                    self.sample_batch(), run_dir, 1, FakeAdapter(error=RuntimeError("fail")), GenerationSettings()
                )
            _, item = execute_one(
                self.sample_batch(), run_dir, 1, FakeAdapter(), GenerationSettings(), retry=True
            )
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.attempt_count, 2)

    def test_interrupted_running_item_is_recoverable_with_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(KeyboardInterrupt):
                execute_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeAdapter(error=KeyboardInterrupt()),
                    GenerationSettings(),
                )
            interrupted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(interrupted.items[0].status, "running")
            self.assertEqual(interrupted.items[0].attempt_count, 1)
            _, recovered = execute_one(
                self.sample_batch(), run_dir, 1, FakeAdapter(), GenerationSettings(), retry=True
            )
            self.assertEqual(recovered.status, "completed")
            self.assertEqual(recovered.attempt_count, 2)

    def test_completed_item_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            execute_one(self.sample_batch(), run_dir, 1, FakeAdapter(), GenerationSettings())
            adapter = FakeAdapter()
            with self.assertRaisesRegex(ValueError, "already completed"):
                execute_one(
                    self.sample_batch(), run_dir, 1, adapter, GenerationSettings(), retry=True
                )
            self.assertEqual(adapter.calls, [])

    def test_empty_candidate_result_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            adapter = FakeAdapter(result=GenerationResult(candidates=()))
            with self.assertRaisesRegex(RuntimeError, "confidently identifiable"):
                execute_one(self.sample_batch(), run_dir, 1, adapter, GenerationSettings())
            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(state.items[0].status, "failed")

    def test_completed_item_download_persists_validated_metadata_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            adapter = FakeDownloadAdapter()
            state, item = execute_download_one(
                self.sample_batch(),
                run_dir,
                1,
                adapter,
            )
            self.assertEqual(adapter.calls, [1, 2])
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.attempt_count, 2)
            self.assertEqual(item.download_status, "completed")
            self.assertEqual(item.download_attempt_count, 1)
            self.assertEqual([candidate.variant for candidate in item.candidates], [1, 2])
            self.assertEqual([entry.order for entry in state.items], [1, 2, 3])
            self.assertEqual([entry.status for entry in state.items], ["completed", "pending", "pending"])
            for index, candidate in enumerate(item.candidates, start=1):
                self.assertEqual(candidate.download_status, "completed")
                self.assertEqual(candidate.candidate_id, f"candidate-{'one' if index == 1 else 'two'}")
                self.assertEqual(candidate.image_format, "PNG")
                self.assertEqual(candidate.mime_type, "image/png")
                self.assertEqual(candidate.browser_download_guid, f"guid-{index}")
                self.assertEqual(candidate.download_mechanism, "cdp_download_events")
                self.assertGreater(candidate.width, 0)
                self.assertGreater(candidate.height, 0)
                self.assertGreater(candidate.byte_size, 0)
                self.assertEqual(len(candidate.sha256), 64)
                self.assertEqual(
                    candidate.local_relative_path,
                    f"assets\\0001__scene_0001_visual_001__v0{index}.png",
                )
                self.assertTrue(Path(candidate.local_path).is_file())
            persisted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(persisted.items[1].visual_id, "scene_0002_visual_001")
            self.assertEqual(persisted.items[2].visual_id, "scene_0003_visual_001")

    def test_one_signal_during_download_stops_before_next_order_and_preserves_candidate(self):
        class InterruptingDownloadAdapter(SequentialFakeAdapter):
            def download_candidate(inner_self, *, candidate, destination):
                order = int(candidate.suggested_filename[:4])
                inner_self.events.append(("download", order, candidate.variant))
                state.request()
                raise RuntimeError("translated download interruption")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = InterruptingDownloadAdapter()
            with authoritative_cancellation_scope() as state, self.assertRaises(
                KeyboardInterrupt
            ) as raised:
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=2),
                )

            persisted = RunStateStore(run_dir / "run_state.json").load()

        self.assertEqual(raised.exception.cancellation_diagnostics["interrupt_signal_count"], 1)
        self.assertEqual(adapter.events, [("generate", 1), ("download", 1, 1)])
        first = persisted.items[0]
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(len(first.candidates), 2)
        self.assertEqual(first.candidates[0].candidate_id, "order-1-v01")
        self.assertEqual(first.candidates[0].download_status, "running")
        self.assertEqual(first.download_status, "running")
        self.assertEqual(persisted.items[1].status, "pending")
        self.assertNotIn(("generate", 2), adapter.events)

    def test_one_signal_during_pillow_validation_preserves_download_resume_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            initial = self.completed_download_run(run_dir)
            initial.items[0].candidates[0].candidate_id = "candidate-one"
            RunStateStore(run_dir / "run_state.json").save(initial)
            adapter = FakeDownloadAdapter()

            def translated_validation_interrupt(_path, **_kwargs):
                state.request()
                raise RuntimeError("Pillow translated Ctrl+C")

            with authoritative_cancellation_scope() as state, patch(
                "nano_batch.runner.validate_image_file",
                side_effect=translated_validation_interrupt,
            ), self.assertRaises(KeyboardInterrupt) as raised:
                execute_download_one(self.sample_batch(), run_dir, 1, adapter)

            persisted = RunStateStore(run_dir / "run_state.json").load().items[0]

        facts = raised.exception.cancellation_diagnostics
        self.assertEqual(facts["interrupt_signal_count"], 1)
        self.assertEqual(facts["interrupt_phase"], "download_file_validation")
        self.assertEqual(facts["blocking_operation"], "pillow_staging_validation")
        self.assertEqual(persisted.status, "completed")
        self.assertEqual(persisted.attempt_count, 2)
        self.assertEqual(persisted.download_status, "running")
        self.assertEqual(persisted.candidates[0].download_status, "running")
        self.assertEqual(persisted.candidates[0].candidate_id, "candidate-one")

    def test_one_signal_during_atomic_commit_preserves_download_resume_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            initial = self.completed_download_run(run_dir)
            initial.items[0].candidates[0].candidate_id = "candidate-one"
            RunStateStore(run_dir / "run_state.json").save(initial)
            adapter = FakeDownloadAdapter()

            def translated_commit_interrupt(*_args, **_kwargs):
                state.request()
                raise RuntimeError("filesystem translated Ctrl+C")

            with authoritative_cancellation_scope() as state, patch(
                "nano_batch.runner.commit_staged_file",
                side_effect=translated_commit_interrupt,
            ), self.assertRaises(KeyboardInterrupt) as raised:
                execute_download_one(self.sample_batch(), run_dir, 1, adapter)

            persisted = RunStateStore(run_dir / "run_state.json").load().items[0]
            canonical = run_dir / "assets" / "0001__scene_0001_visual_001__v01.png"

        facts = raised.exception.cancellation_diagnostics
        self.assertEqual(facts["interrupt_signal_count"], 1)
        self.assertEqual(facts["interrupt_phase"], "download_atomic_commit")
        self.assertEqual(facts["blocking_operation"], "atomic_canonical_commit")
        self.assertFalse(canonical.exists())
        self.assertEqual(persisted.status, "completed")
        self.assertEqual(persisted.download_status, "running")
        self.assertEqual(persisted.candidates[0].download_status, "running")
        self.assertEqual(persisted.candidates[0].candidate_id, "candidate-one")

    def test_download_validation_failure_keeps_generation_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            with self.assertRaisesRegex(Exception, "decodable"):
                execute_download_one(
                    self.sample_batch(),
                    run_dir,
                    1,
                    FakeDownloadAdapter(invalid_variants={1}),
                )
            item = RunStateStore(run_dir / "run_state.json").load().items[0]
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.download_status, "failed")
            self.assertEqual(item.candidates[0].download_status, "failed")
            self.assertIn("decodable", item.candidates[0].download_error)
            self.assertEqual(item.candidates[1].download_status, "not_started")

    def test_jpeg_bytes_choose_jpeg_canonical_names_instead_of_png_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            _, item = execute_download_one(
                self.sample_batch(),
                run_dir,
                1,
                FakeDownloadAdapter(image_format="JPEG"),
            )

            self.assertEqual(item.status, "completed")
            self.assertEqual(item.attempt_count, 2)
            self.assertEqual(item.download_status, "completed")
            self.assertEqual(
                [candidate.suggested_filename for candidate in item.candidates],
                [
                    "0001__scene_0001_visual_001__v01.jpeg",
                    "0001__scene_0001_visual_001__v02.jpeg",
                ],
            )
            for candidate in item.candidates:
                self.assertEqual(candidate.image_format, "JPEG")
                self.assertEqual(candidate.mime_type, "image/jpeg")
                self.assertTrue(Path(candidate.local_path).is_file())
                self.assertEqual(Path(candidate.local_path).suffix, ".jpeg")
            self.assertEqual(list((run_dir / "assets").glob("*.png")), [])

    def test_partial_success_is_persisted_and_retry_resumes_only_failed_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            first = FakeDownloadAdapter(fail_variants={2})
            with self.assertRaisesRegex(RuntimeError, "synthetic download failure"):
                execute_download_one(self.sample_batch(), run_dir, 1, first)
            partial = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(first.calls, [1, 2])
            self.assertEqual(partial.items[0].status, "completed")
            self.assertEqual(partial.items[0].download_status, "failed")
            self.assertEqual(partial.items[0].candidates[0].download_status, "completed")
            self.assertEqual(partial.items[0].candidates[1].download_status, "failed")
            first_path = Path(partial.items[0].candidates[0].local_path)
            first_bytes = first_path.read_bytes()

            retry = FakeDownloadAdapter()
            state, completed = execute_download_one(self.sample_batch(), run_dir, 1, retry)
            self.assertEqual(retry.calls, [2])
            self.assertEqual(first_path.read_bytes(), first_bytes)
            self.assertEqual(completed.download_status, "completed")
            self.assertEqual(completed.download_attempt_count, 2)
            self.assertEqual(len(completed.output_files), 2)
            self.assertEqual([entry.order for entry in state.items], [1, 2, 3])
            self.assertEqual(state.items[1].status, "pending")
            self.assertEqual(state.items[2].status, "pending")

    def test_three_order_runner_skips_accepted_order_one_and_runs_strictly_sequentially(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            stored = RunStateStore(run_dir / "run_state.json").load()
            historical_details = json.loads(stored.items[0].result_details)
            historical_details["note"] = "historical generation note"
            stored.items[0].result_details = json.dumps(historical_details)
            RunStateStore(run_dir / "run_state.json").save(stored)
            order_one_paths = [
                Path(candidate.local_path) for candidate in stored.items[0].candidates
            ]
            order_one_bytes = [path.read_bytes() for path in order_one_paths]

            preview = plan_sequential_batch(
                self.sample_batch(),
                run_dir,
                GenerationSettings(variants=2),
            )
            self.assertEqual(preview.orders[0].generation_action, "skip_completed")
            self.assertEqual(preview.orders[0].download_action, "skip_valid")
            self.assertEqual(preview.orders[1].generation_action, "generate")
            self.assertEqual(preview.orders[2].generation_action, "generate")

            adapter = SequentialFakeAdapter()
            final, outcomes = execute_sequential_batch(
                self.sample_batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=2),
            )

            self.assertEqual(
                adapter.events,
                [
                    ("generate", 2),
                    ("download", 2, 1),
                    ("download", 2, 2),
                    ("generate", 3),
                    ("download", 3, 1),
                    ("download", 3, 2),
                ],
            )
            self.assertEqual(
                [(outcome.order, outcome.generation, outcome.downloads) for outcome in outcomes],
                [
                    (1, "skipped_completed", "skipped_valid"),
                    (2, "generated", "downloaded"),
                    (3, "generated", "downloaded"),
                ],
            )
            self.assertEqual([item.status for item in final.items], ["completed"] * 3)
            self.assertEqual(
                [item.download_status for item in final.items],
                ["completed"] * 3,
            )
            self.assertEqual([item.attempt_count for item in final.items], [2, 1, 1])
            self.assertEqual(
                [item.download_attempt_count for item in final.items],
                [8, 1, 1],
            )
            self.assertEqual(final.items[0].result_details, stored.items[0].result_details)
            self.assertIsNone(final.items[0].error)
            self.assertEqual(
                [path.read_bytes() for path in order_one_paths],
                order_one_bytes,
            )
            self.assertEqual(len(list((run_dir / "assets").glob("*.jpeg"))), 6)

    def test_one_item_generic_batch_uses_proven_sequential_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(1)
            adapter = SequentialFakeAdapter()

            final, outcomes = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=2),
            )

            self.assertEqual(
                adapter.events,
                [
                    ("generate", 1),
                    ("download", 1, 1),
                    ("download", 1, 2),
                ],
            )
            self.assertEqual([outcome.order for outcome in outcomes], [1])
            self.assertEqual(final.items[0].status, "completed")
            self.assertEqual(final.items[0].download_status, "completed")

    def test_ten_item_batch_runs_in_validated_order_without_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(10)
            adapter = SequentialFakeAdapter()
            callbacks = []

            final, outcomes = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=2),
                order_completed=lambda outcome: callbacks.append(outcome.order),
            )

            expected_events = []
            for order in range(1, 11):
                expected_events.extend(
                    [
                        ("generate", order),
                        ("download", order, 1),
                        ("download", order, 2),
                    ]
                )
            self.assertEqual(adapter.events, expected_events)
            self.assertEqual([outcome.order for outcome in outcomes], list(range(1, 11)))
            self.assertEqual(callbacks, list(range(1, 11)))
            self.assertEqual([item.order for item in final.items], list(range(1, 11)))
            self.assertTrue(all(item.status == "completed" for item in final.items))
            self.assertTrue(
                all(item.download_status == "completed" for item in final.items)
            )

    def test_five_item_failure_resume_preserves_history_and_partial_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(5)
            first = SequentialFakeAdapter(fail_downloads={(3, 2)})

            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic download failure order 3 v02",
            ):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    first,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(
                first.events,
                [
                    ("generate", 1),
                    ("download", 1, 1),
                    ("download", 1, 2),
                    ("generate", 2),
                    ("download", 2, 1),
                    ("download", 2, 2),
                    ("generate", 3),
                    ("download", 3, 1),
                    ("download", 3, 2),
                ],
            )
            store = RunStateStore(run_dir / "run_state.json")
            partial = store.load()
            self.assertEqual(
                [item.status for item in partial.items],
                ["completed", "completed", "completed", "pending", "pending"],
            )
            self.assertEqual(partial.items[2].download_status, "failed")
            self.assertEqual(
                [candidate.download_status for candidate in partial.items[2].candidates],
                ["completed", "failed"],
            )
            preserved_path = Path(partial.items[2].candidates[0].local_path)
            preserved_bytes = preserved_path.read_bytes()
            historical_details = json.loads(partial.items[0].result_details)
            historical_details["notes"] = ["historical generation note", "historical download note"]
            partial.items[0].result_details = json.dumps(historical_details)
            store.save(partial)
            preserved_first = store.load().items[0]

            retry = SequentialFakeAdapter()
            final, outcomes = execute_sequential_batch(
                batch,
                run_dir,
                retry,
                GenerationSettings(variants=2),
            )

            self.assertEqual(
                retry.events,
                [
                    ("download", 3, 2),
                    ("generate", 4),
                    ("download", 4, 1),
                    ("download", 4, 2),
                    ("generate", 5),
                    ("download", 5, 1),
                    ("download", 5, 2),
                ],
            )
            self.assertEqual(final.items[0], preserved_first)
            self.assertEqual(preserved_path.read_bytes(), preserved_bytes)
            self.assertEqual(final.items[2].attempt_count, 1)
            self.assertEqual(final.items[2].download_attempt_count, 2)
            self.assertEqual(
                [(outcome.order, outcome.generation, outcome.downloads) for outcome in outcomes],
                [
                    (1, "skipped_completed", "skipped_valid"),
                    (2, "skipped_completed", "skipped_valid"),
                    (3, "skipped_completed", "resumed"),
                    (4, "generated", "downloaded"),
                    (5, "generated", "downloaded"),
                ],
            )
            self.assertTrue(all(item.status == "completed" for item in final.items))
            self.assertTrue(
                all(item.download_status == "completed" for item in final.items)
            )

    def test_all_complete_generic_rerun_is_adapter_free_and_state_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(5)
            execute_sequential_batch(
                batch,
                run_dir,
                SequentialFakeAdapter(),
                GenerationSettings(variants=2),
            )
            store = RunStateStore(run_dir / "run_state.json")
            before = store.path.read_bytes()
            adapter = SequentialFakeAdapter(
                fail_generation_orders=set(range(1, 6)),
                fail_downloads={(order, variant) for order in range(1, 6) for variant in (1, 2)},
            )

            final, outcomes = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=2),
            )

            self.assertEqual(adapter.events, [])
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(
                [(outcome.generation, outcome.downloads) for outcome in outcomes],
                [("skipped_completed", "skipped_valid")] * 5,
            )
            self.assertTrue(all(item.attempt_count == 1 for item in final.items))
            self.assertTrue(
                all(item.download_attempt_count == 1 for item in final.items)
            )

    def test_order_two_partial_failure_stops_order_three_and_retry_resumes_missing_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            first = SequentialFakeAdapter(fail_downloads={(2, 2)})

            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic download failure order 2 v02",
            ):
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    first,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(
                first.events,
                [
                    ("generate", 2),
                    ("download", 2, 1),
                    ("download", 2, 2),
                ],
            )
            partial = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(partial.items[1].status, "completed")
            self.assertEqual(partial.items[1].download_status, "failed")
            self.assertEqual(
                [candidate.download_status for candidate in partial.items[1].candidates],
                ["completed", "failed"],
            )
            self.assertEqual(partial.items[2].status, "pending")
            preserved_path = Path(partial.items[1].candidates[0].local_path)
            preserved_bytes = preserved_path.read_bytes()

            retry = SequentialFakeAdapter()
            final, _ = execute_sequential_batch(
                self.sample_batch(),
                run_dir,
                retry,
                GenerationSettings(variants=2),
            )
            self.assertEqual(
                retry.events,
                [
                    ("download", 2, 2),
                    ("generate", 3),
                    ("download", 3, 1),
                    ("download", 3, 2),
                ],
            )
            self.assertEqual(preserved_path.read_bytes(), preserved_bytes)
            self.assertEqual(final.items[1].attempt_count, 1)
            self.assertEqual(final.items[1].download_attempt_count, 2)
            self.assertEqual([item.status for item in final.items], ["completed"] * 3)
            self.assertEqual(
                [item.download_status for item in final.items],
                ["completed"] * 3,
            )

    def test_order_three_partial_retry_preserves_v01_and_completed_order_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            first = SequentialFakeAdapter(fail_downloads={(3, 2)})

            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic download failure order 3 v02",
            ):
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    first,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(
                first.events,
                [
                    ("generate", 2),
                    ("download", 2, 1),
                    ("download", 2, 2),
                    ("generate", 3),
                    ("download", 3, 1),
                    ("download", 3, 2),
                ],
            )
            store = RunStateStore(run_dir / "run_state.json")
            partial = store.load()
            completed_order_two = partial.items[1]
            self.assertEqual(completed_order_two.status, "completed")
            self.assertEqual(completed_order_two.download_status, "completed")
            self.assertEqual(
                [candidate.download_status for candidate in partial.items[2].candidates],
                ["completed", "failed"],
            )
            preserved_path = Path(partial.items[2].candidates[0].local_path)
            preserved_bytes = preserved_path.read_bytes()

            retry = SequentialFakeAdapter()
            final, _ = execute_sequential_batch(
                self.sample_batch(),
                run_dir,
                retry,
                GenerationSettings(variants=2),
            )

            self.assertEqual(retry.events, [("download", 3, 2)])
            self.assertEqual(final.items[1], completed_order_two)
            self.assertEqual(preserved_path.read_bytes(), preserved_bytes)
            self.assertEqual(final.items[2].attempt_count, 1)
            self.assertEqual(final.items[2].download_attempt_count, 2)
            self.assertEqual(final.items[2].download_status, "completed")

    def test_order_two_generation_failure_preserves_order_one_and_never_starts_order_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            before = RunStateStore(run_dir / "run_state.json").load()
            order_one_attempts = before.items[0].attempt_count
            order_one_download_attempts = before.items[0].download_attempt_count
            adapter = SequentialFakeAdapter(fail_generation_orders={2})

            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic generation failure order 2",
            ):
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(adapter.events, [("generate", 2)])
            failed = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(failed.items[0].status, "completed")
            self.assertEqual(failed.items[0].download_status, "completed")
            self.assertEqual(failed.items[0].attempt_count, order_one_attempts)
            self.assertEqual(
                failed.items[0].download_attempt_count,
                order_one_download_attempts,
            )
            self.assertEqual(failed.items[1].status, "failed")
            self.assertEqual(failed.items[1].attempt_count, 1)
            self.assertEqual(failed.items[1].download_status, "not_started")
            self.assertEqual(failed.items[2].status, "pending")
            self.assertEqual(failed.items[2].download_status, "not_started")

    def test_current_attempt_usage_limit_pauses_batch_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            before = RunStateStore(run_dir / "run_state.json").load()
            adapter = SequentialFakeAdapter(flow_limit_orders={2})

            with self.assertRaises(TemporaryGenerationBlock) as raised:
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(raised.exception.code, FLOW_USAGE_LIMIT_REACHED)
            self.assertEqual(raised.exception.order, 2)
            self.assertEqual(adapter.events, [("generate", 2)])
            paused = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(paused.items[0], before.items[0])
            self.assertEqual(paused.items[1].status, WAITING_FOR_FLOW_LIMIT_RESET)
            self.assertEqual(paused.items[1].generation_error_code, FLOW_USAGE_LIMIT_REACHED)
            self.assertEqual(paused.items[1].attempt_count, 1)
            self.assertEqual(paused.items[1].candidates, [])
            self.assertEqual(paused.items[1].download_status, "not_started")
            self.assertEqual(paused.items[1].download_attempt_count, 0)
            self.assertEqual(paused.items[2].status, "pending")
            self.assertEqual(paused.items[2].attempt_count, 0)
            self.assertEqual(paused.items[2].download_status, "not_started")

    def test_post_activation_usage_limit_records_exactly_one_generation_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = SequentialFakeAdapter(
                flow_limit_orders={1},
                flow_limit_attempt_activated=True,
            )

            with self.assertRaises(TemporaryGenerationBlock):
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=2),
                )

            paused = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(adapter.events, [("generate", 1)])
            self.assertEqual(paused.items[0].status, WAITING_FOR_FLOW_LIMIT_RESET)
            self.assertEqual(paused.items[0].attempt_count, 1)
            self.assertEqual(paused.items[0].download_attempt_count, 0)
            self.assertEqual(paused.items[1].status, "pending")

    def test_flow_limit_wait_resumes_without_retry_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.accepted_order_one_run(run_dir)
            store = RunStateStore(run_dir / "run_state.json")
            before = store.load()
            before.items[1].status = WAITING_FOR_FLOW_LIMIT_RESET
            before.items[1].attempt_count = 3
            before.items[1].error = (
                f"{FLOW_USAGE_LIMIT_REACHED}: visible Flow usage limit"
            )
            before.items[1].generation_error_code = FLOW_USAGE_LIMIT_REACHED
            store.save(before)
            preserved_order_one = store.load().items[0]
            adapter = SequentialFakeAdapter()

            final, outcomes = execute_sequential_batch(
                self.sample_batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=2),
            )

            self.assertEqual(final.items[0], preserved_order_one)
            self.assertEqual(final.items[1].status, "completed")
            self.assertEqual(final.items[1].attempt_count, 4)
            self.assertIsNone(final.items[1].generation_error_code)
            self.assertEqual(final.items[2].status, "completed")
            self.assertEqual(
                adapter.events,
                [
                    ("generate", 2),
                    ("download", 2, 1),
                    ("download", 2, 2),
                    ("generate", 3),
                    ("download", 3, 1),
                    ("download", 3, 2),
                ],
            )
            self.assertEqual(outcomes[1].generation, "resumed_external_block")

    def test_repeated_current_attempt_limit_increments_historical_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            state = initialize_run_state(self.sample_batch(), run_dir)
            state.items[0].status = WAITING_FOR_FLOW_LIMIT_RESET
            state.items[0].attempt_count = 2
            state.items[0].error = f"{FLOW_USAGE_LIMIT_REACHED}: visible Flow usage limit"
            state.items[0].generation_error_code = FLOW_USAGE_LIMIT_REACHED
            RunStateStore(run_dir / "run_state.json").save(state)

            with self.assertRaises(TemporaryGenerationBlock):
                execute_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    SequentialFakeAdapter(flow_limit_orders={1}),
                    GenerationSettings(variants=2),
                )

            paused = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(paused.items[0].attempt_count, 3)
            self.assertEqual(paused.items[0].status, WAITING_FOR_FLOW_LIMIT_RESET)

    def test_download_authorization_save_failure_stops_before_browser_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.sample_batch()
            self.accepted_order_one_run(run_dir)
            adapter = SequentialFakeAdapter()
            execute_one(
                batch,
                run_dir,
                2,
                adapter,
                GenerationSettings(variants=2),
            )
            self.assertEqual(adapter.events, [("generate", 2)])
            adapter.events.clear()
            store = RunStateStore(run_dir / "run_state.json")
            before_bytes = store.path.read_bytes()
            before = store.load()

            with patch(
                "nano_batch.state.os.replace",
                side_effect=PermissionError(13, "persistent access denied"),
            ), patch("nano_batch.state.time.sleep"), self.assertRaises(
                StatePersistenceError
            ):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    adapter,
                    GenerationSettings(variants=2),
                )

            self.assertEqual(adapter.events, [])
            self.assertEqual(store.path.read_bytes(), before_bytes)
            after = store.load()
            self.assertEqual(after.items[0], before.items[0])
            self.assertEqual(after.items[1].status, "completed")
            self.assertEqual(after.items[1].attempt_count, 1)
            self.assertEqual(after.items[1].download_status, "not_started")
            self.assertEqual(after.items[1].download_attempt_count, 0)
            self.assertEqual(after.items[2].status, "pending")
            self.assertEqual(after.items[2].download_status, "not_started")

    def test_batch_plan_is_read_only_and_supports_one_or_two_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "missing"
            plan = plan_sequential_batch(
                self.sample_batch(),
                run_dir,
                GenerationSettings(variants=2),
            )
            self.assertFalse(plan.state_exists)
            self.assertFalse(run_dir.exists())
            self.assertEqual(
                [order.generation_action for order in plan.orders],
                ["generate", "generate", "generate"],
            )
            one_variant_plan = plan_sequential_batch(
                self.sample_batch(),
                run_dir,
                GenerationSettings(variants=1),
            )
            self.assertEqual(
                [order.candidate_actions for order in one_variant_plan.orders],
                [((1, "download_after_generation"),)] * 3,
            )
            with self.assertRaisesRegex(ValueError, "exactly one or two candidates"):
                plan_sequential_batch(
                    self.sample_batch(),
                    run_dir,
                    GenerationSettings(variants=3),
                )

            short_batch = Batch(
                batch_id="sha256:short",
                source_schema="test",
                items=self.sample_batch().items[:2],
            )
            short_plan = plan_sequential_batch(
                short_batch,
                run_dir,
                GenerationSettings(variants=2),
            )
            self.assertEqual([item.order for item in short_plan.orders], [1, 2])
            empty_batch = Batch(
                batch_id="sha256:empty",
                source_schema="test",
                items=(),
            )
            with self.assertRaisesRegex(ValueError, "non-empty validated batch"):
                plan_sequential_batch(
                    empty_batch,
                    run_dir,
                    GenerationSettings(variants=2),
                )

    def test_declared_model_hint_is_informational_not_runtime_enforcement(self):
        batch = Batch(
            batch_id="sha256:nb2",
            source_schema="test",
            items=(
                BatchItem(
                    order=1,
                    visual_id="v1",
                    prompt="safe prompt",
                    mode="image",
                    aspect_ratio="16:9",
                    preferred_model_hint="Nano Banana 2",
                    variants=2,
                ),
            ),
        )
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            _state, item = execute_one(
                batch,
                Path(tmp) / "run",
                1,
                adapter,
                GenerationSettings(preferred_model_hint="Nano Banana Pro"),
            )
        self.assertEqual(item.status, "completed")
        self.assertEqual(len(adapter.calls), 1)

    def test_existing_valid_download_is_skipped_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            execute_download_one(self.sample_batch(), run_dir, 1, FakeDownloadAdapter())
            before = RunStateStore(run_dir / "run_state.json").load()
            first_path = Path(before.items[0].candidates[0].local_path)
            first_bytes = first_path.read_bytes()
            adapter = FakeDownloadAdapter()
            _, item = execute_download_one(self.sample_batch(), run_dir, 1, adapter)
            self.assertEqual(adapter.calls, [])
            self.assertEqual(item.download_attempt_count, 1)
            self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_untracked_existing_candidate_is_not_overwritten_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.completed_download_run(run_dir)
            plan = plan_download_one(self.sample_batch(), run_dir, 1)
            plan.asset_dir.mkdir(parents=True)
            plan.candidates[0].destination.write_bytes(b"existing")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                plan_download_one(self.sample_batch(), run_dir, 1)
            self.assertEqual(plan.candidates[0].destination.read_bytes(), b"existing")

    def test_candidate_repair_preview_plan_performs_zero_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            state_path = run_dir / "run_state.json"
            before = state_path.read_bytes()
            assets_before = {
                str(path.relative_to(run_dir)): path.read_bytes()
                for path in (run_dir / "assets").iterdir()
                if path.is_file()
            }

            plan = plan_repair_order_candidates(batch, run_dir, 4)

            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(plan.item.status, "completed")
            self.assertEqual(len(plan.conflicts), 2)
            self.assertEqual(
                {
                    str(path.relative_to(run_dir)): path.read_bytes()
                    for path in (run_dir / "assets").iterdir()
                    if path.is_file()
                },
                assets_before,
            )

    def test_exact_ownership_conflict_repairs_only_target_and_preserves_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            store = RunStateStore(run_dir / "run_state.json")
            before = store.load()
            prior_items = [asdict(item) for item in before.items[:3]]
            later_items = [asdict(item) for item in before.items[4:]]
            target_before = before.items[3]
            preserved = {
                "attempt_count": target_before.attempt_count,
                "started_at": target_before.started_at,
                "completed_at": target_before.completed_at,
                "download_status": target_before.download_status,
                "download_attempt_count": target_before.download_attempt_count,
                "download_started_at": target_before.download_started_at,
                "download_completed_at": target_before.download_completed_at,
                "download_error": target_before.download_error,
            }
            assets_before = {
                path.name: path.read_bytes()
                for path in (run_dir / "assets").iterdir()
                if path.is_file()
            }

            _, repaired = execute_repair_order_candidates(batch, run_dir, 4)
            after = store.load()

            self.assertEqual(repaired.status, "failed")
            self.assertEqual(
                repaired.generation_error_code,
                CANDIDATE_UUID_OWNERSHIP_CONFLICT,
            )
            self.assertEqual(repaired.candidates, [])
            self.assertEqual(repaired.output_files, [])
            self.assertIsNone(repaired.result_details)
            for field_name, value in preserved.items():
                self.assertEqual(getattr(repaired, field_name), value)
            self.assertIn("historical generation diagnostic", repaired.error)
            self.assertIn("historical candidate download failure", repaired.error)
            self.assertEqual([asdict(item) for item in after.items[:3]], prior_items)
            self.assertEqual([asdict(item) for item in after.items[4:]], later_items)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in (run_dir / "assets").iterdir()
                    if path.is_file()
                },
                assets_before,
            )

    def test_candidate_repair_batch_id_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            wrong_batch = Batch(
                batch_id="sha256:different",
                source_schema=batch.source_schema,
                items=batch.items,
            )
            before = (run_dir / "run_state.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "different semantic batch"):
                plan_repair_order_candidates(wrong_batch, run_dir, 4)
            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)

    def test_candidate_repair_refuses_valid_nonconflicting_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            store = RunStateStore(run_dir / "run_state.json")
            state = store.load()
            state.items[3].candidates[0].candidate_id = "unique-order-4-v01"
            state.items[3].candidates[0].asset_url = None
            state.items[3].candidates[1].candidate_id = "unique-order-4-v02"
            state.items[3].candidates[1].asset_url = None
            store.save(state)
            before = store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "no candidate UUID conflict"):
                plan_repair_order_candidates(batch, run_dir, 4)
            self.assertEqual(store.path.read_bytes(), before)

    def test_candidate_repair_refuses_target_with_canonical_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            target_asset = (
                run_dir / "assets" / "0004__scene_0004_visual_001__v01.jpeg"
            )
            target_asset.write_bytes(b"must-not-delete")
            state_before = (run_dir / "run_state.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "canonical asset file"):
                execute_repair_order_candidates(batch, run_dir, 4)
            self.assertEqual(target_asset.read_bytes(), b"must-not-delete")
            self.assertEqual((run_dir / "run_state.json").read_bytes(), state_before)

    def test_candidate_repair_refuses_invalid_earlier_completed_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            store = RunStateStore(run_dir / "run_state.json")
            state = store.load()
            state.items[1].download_status = "failed"
            store.save(state)
            before = store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Earlier order 2"):
                plan_repair_order_candidates(batch, run_dir, 4)
            self.assertEqual(store.path.read_bytes(), before)

    def test_candidate_repair_atomic_save_failure_preserves_old_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            state_path = run_dir / "run_state.json"
            before = state_path.read_bytes()
            with patch(
                "nano_batch.runner.RunStateStore.save",
                side_effect=StatePersistenceError("synthetic atomic failure"),
            ), self.assertRaisesRegex(StatePersistenceError, "synthetic atomic failure"):
                execute_repair_order_candidates(batch, run_dir, 4)
            self.assertEqual(state_path.read_bytes(), before)

    def test_repaired_order_requires_and_accepts_explicit_retry_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            execute_repair_order_candidates(batch, run_dir, 4)

            blocked = plan_sequential_batch(batch, run_dir, GenerationSettings())
            allowed = plan_sequential_batch(
                batch,
                run_dir,
                GenerationSettings(),
                retry_failed_generation=True,
            )

            self.assertEqual(blocked.orders[3].generation_action, "blocked_retry_required")
            self.assertEqual(allowed.orders[3].generation_action, "retry")

    def test_download_ownership_guard_remains_active_after_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.ownership_conflict_repair_run(run_dir)
            execute_repair_order_candidates(batch, run_dir, 4)
            store = RunStateStore(run_dir / "run_state.json")
            state = store.load()
            later = state.items[4]
            later.status = "completed"
            later.candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0005__scene_0005_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="order-1-v01",
                ),
                CandidateRecord(
                    variant=2,
                    suggested_filename="0005__scene_0005_visual_001__v02.png",
                    source_kind="rendered-image",
                    candidate_id="unique-order-5-v02",
                ),
            ]
            store.save(state)

            with self.assertRaisesRegex(
                ValueError,
                CANDIDATE_UUID_OWNERSHIP_CONFLICT,
            ):
                plan_download_one(batch, run_dir, 5)

    def test_order_twenty_seven_partial_download_plan_skips_prior_orders_and_preserves_v01(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(28)
            settings = GenerationSettings()
            accepted = SequentialFakeAdapter()
            for order in range(1, 27):
                execute_one(batch, run_dir, order, accepted, settings)
                execute_download_one(batch, run_dir, order, accepted)
            execute_one(batch, run_dir, 27, accepted, settings)
            failing = SequentialFakeAdapter(fail_downloads={(27, 2)})
            with self.assertRaisesRegex(RuntimeError, "order 27 v02"):
                execute_download_one(batch, run_dir, 27, failing)

            store = RunStateStore(run_dir / "run_state.json")
            before_bytes = store.path.read_bytes()
            before = store.load()
            earlier = [asdict(item) for item in before.items[:26]]
            plan = plan_sequential_batch(batch, run_dir, settings)
            after = store.load()

            self.assertEqual(store.path.read_bytes(), before_bytes)
            self.assertTrue(
                all(
                    order.generation_action == "skip_completed"
                    and order.download_action == "skip_valid"
                    for order in plan.orders[:26]
                )
            )
            self.assertEqual(plan.orders[26].generation_action, "skip_completed")
            self.assertEqual(
                plan.orders[26].candidate_actions,
                ((1, "skip_valid"), (2, "download")),
            )
            self.assertEqual([asdict(item) for item in after.items[:26]], earlier)
            self.assertEqual(after.items[26].candidates[0].download_status, "completed")
            self.assertEqual(after.items[26].candidates[1].download_status, "failed")
            self.assertEqual(after.items[27].status, "pending")
            self.assertEqual(after.items[27].attempt_count, 0)

    def test_order_twenty_eight_v01_failure_resumes_downloads_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.generic_batch(29)
            settings = GenerationSettings()
            accepted = SequentialFakeAdapter()
            for order in range(1, 28):
                execute_one(batch, run_dir, order, accepted, settings)
                execute_download_one(batch, run_dir, order, accepted)
            execute_one(batch, run_dir, 28, accepted, settings)
            failing = SequentialFakeAdapter(fail_downloads={(28, 1)})
            with self.assertRaisesRegex(RuntimeError, "order 28 v01"):
                execute_download_one(batch, run_dir, 28, failing)

            store = RunStateStore(run_dir / "run_state.json")
            before = store.load()
            earlier = [asdict(item) for item in before.items[:27]]
            order_28_candidate_ids = [
                candidate.candidate_id for candidate in before.items[27].candidates
            ]
            plan = plan_sequential_batch(batch, run_dir, settings)

            self.assertTrue(
                all(
                    order.generation_action == "skip_completed"
                    and order.download_action == "skip_valid"
                    for order in plan.orders[:27]
                )
            )
            self.assertEqual(plan.orders[27].generation_action, "skip_completed")
            self.assertEqual(
                plan.orders[27].candidate_actions,
                ((1, "download"), (2, "download")),
            )
            self.assertEqual(plan.orders[28].generation_action, "generate")

            retry = SequentialFakeAdapter()
            execute_download_one(batch, run_dir, 28, retry)
            after = store.load()

            self.assertEqual(retry.events, [("download", 28, 1), ("download", 28, 2)])
            self.assertEqual([asdict(item) for item in after.items[:27]], earlier)
            self.assertEqual(after.items[27].status, "completed")
            self.assertEqual(after.items[27].download_status, "completed")
            self.assertEqual(
                [candidate.candidate_id for candidate in after.items[27].candidates],
                order_28_candidate_ids,
            )
            self.assertEqual(after.items[27].attempt_count, 1)
            self.assertEqual(after.items[28].status, "pending")
            self.assertEqual(after.items[28].attempt_count, 0)


if __name__ == "__main__":
    unittest.main()
