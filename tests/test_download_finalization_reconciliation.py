import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image

from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.downloads import validate_image_file
from nano_batch.models import (
    Batch, BatchItem, BrowserDownloadResult, CandidateRecord, DiscoveredCandidate,
    FlowProjectReference, GenerationResult, GenerationSettings,
)
from nano_batch.runner import (
    execute_download_one, execute_sequential_batch, initialize_run_state,
    output_basename, plan_sequential_batch,
)
from nano_batch.state import RunStateStore, StatePersistenceError


class OfflineAdapter:
    def __init__(self, before_generate=None):
        self.events = []
        self.before_generate = before_generate

    def generate_one(self, *, item, settings, forbidden_candidate_ids):
        if self.before_generate:
            self.before_generate(item)
        self.events.append(("generate", item.order, item.prompt))
        return GenerationResult(candidates=tuple(
            DiscoveredCandidate(source_kind="rendered-image", candidate_id=str(UUID(int=100 + variant)))
            for variant in range(1, settings.variants + 1)
        ))

    def download_candidate(self, *, candidate, destination):
        self.events.append(("download", int(candidate.suggested_filename[:4]), candidate.variant))
        Image.new("RGB", (16, 9), (10, 20, 30)).save(destination, format="JPEG")
        return BrowserDownloadResult(candidate_id=candidate.candidate_id, staged_path=str(destination))


class DownloadFinalizationReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name) / "run"
        self.store = RunStateStore(self.run_dir / "run_state.json")
        self.batch = Batch(
            batch_id="sha256:interrupted-finalization", source_schema="test",
            items=tuple(BatchItem(order=i, visual_id=f"prompt_{i:04d}_visual_001",
                                  prompt=f"source prompt {i}", variants=1) for i in (1, 2, 3)),
        )
        self.make_state()

    def make_state(self, variants=1):
        self.batch = replace(self.batch, items=tuple(replace(i, variants=variants) for i in self.batch.items))
        state = initialize_run_state(self.batch, self.run_dir)
        state.version = 5
        state.flow_project_required = True
        project_id = str(UUID(int=999))
        state.flow_project = FlowProjectReference(project_id, f"https://flow.google.com/project/{project_id}")
        for item in state.items[:2]:
            item.status = "completed"
            item.attempt_count = 6
            item.completed_at = "2026-10-07T10:20:31+00:00"
            item.started_at = "2026-10-07T10:20:00+00:00"
            item.download_status = "completed"
            item.download_attempt_count = 3
            item.download_started_at = "2026-10-07T10:20:32+00:00"
            item.download_completed_at = "2026-10-07T10:20:40+00:00"
            item.result_details = json.dumps({"configured_variants": variants, "requested_variants": variants})
            item.candidates = []
            item.output_files = []
            for variant in range(1, variants + 1):
                filename = output_basename(item.order, item.visual_id, variant, "jpeg")
                path = (self.run_dir / "assets" / filename).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (16, 9), (item.order, variant, 40)).save(path, format="JPEG")
                metadata = validate_image_file(path, expected_filename=filename)
                candidate_id = str(UUID(int=item.order * 10 + variant))
                item.candidates.append(CandidateRecord(
                    variant=variant, suggested_filename=filename, source_kind="rendered-image",
                    candidate_id=candidate_id, asset_url=f"https://flow-content.google/image/{candidate_id}",
                    download_status="completed", local_path=str(path),
                    local_relative_path=str(path.relative_to(self.run_dir.resolve())),
                    downloaded_at="2026-10-07T10:20:38+00:00", **asdict(metadata),
                ))
                item.output_files.append(str(path))
        target = state.items[1]
        target.source_prompt = self.batch.items[1].prompt
        target.prompt = "effective Unicode café\nsecond line"
        target.automatic_policy_retry_count = 1
        target.automatic_generic_failure_retry_count = 2
        target.download_status = "running"
        target.download_completed_at = None
        self.store.save(state)
        self.before = self.store.load()
        self.adapter = MagicMock()
        self.adapter.generate_one.side_effect = AssertionError("unexpected generation")
        self.adapter.download_candidate.side_effect = AssertionError("unexpected download")

    def resume_download(self):
        return execute_download_one(self.batch, self.run_dir, 2, self.adapter)

    def mutate(self, callback):
        state = self.store.load()
        callback(state)
        self.store.save(state)

    def assert_refused(self, callback=None, *, sequential=False):
        if callback:
            self.mutate(callback)
        before = self.store.path.read_bytes()
        with self.assertRaises((ValueError, FileExistsError)):
            if sequential:
                execute_sequential_batch(self.batch, self.run_dir, self.adapter, GenerationSettings(variants=self.batch.items[0].variants))
            else:
                self.resume_download()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.adapter.mock_calls, [])

    def test_running_finalization_is_repaired_without_browser_actions(self):
        state, item = self.resume_download()
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.download_status, "completed")
        self.assertIsNone(item.download_error)
        self.assertIsNotNone(datetime.fromisoformat(item.download_completed_at).tzinfo)
        self.assertEqual(state.updated_at, item.download_completed_at)
        self.assertEqual(self.adapter.mock_calls, [])

    def test_only_three_item_fields_and_run_updated_at_change(self):
        state, _ = self.resume_download()
        expected = copy.deepcopy(self.before)
        expected.items[1].download_status = "completed"
        expected.items[1].download_error = None
        expected.items[1].download_completed_at = state.items[1].download_completed_at
        expected.updated_at = state.updated_at
        self.assertEqual(asdict(state), asdict(expected))
        self.assertEqual(asdict(self.store.load()), asdict(expected))

    def test_completed_candidates_are_preserved_exactly(self):
        _, item = self.resume_download()
        self.assertEqual(item.candidates, self.before.items[1].candidates)
        self.assertEqual(item.output_files, self.before.items[1].output_files)
        self.assertEqual(item.completed_at, self.before.items[1].completed_at)

    def test_attempt_counts_and_retry_budgets_are_preserved(self):
        _, item = self.resume_download()
        for name in ("attempt_count", "download_attempt_count", "automatic_policy_retry_count", "automatic_generic_failure_retry_count"):
            self.assertEqual(getattr(item, name), getattr(self.before.items[1], name))

    def test_prompts_project_neighbors_and_pending_order_are_preserved(self):
        state, item = self.resume_download()
        self.assertEqual(item.prompt, self.before.items[1].prompt)
        self.assertEqual(item.source_prompt, self.before.items[1].source_prompt)
        self.assertEqual(state.flow_project, self.before.flow_project)
        self.assertEqual(state.items[0], self.before.items[0])
        self.assertEqual(state.items[2], self.before.items[2])

    def test_no_start_or_download_for_target_and_resume_continues_next_order(self):
        def before_generate(item):
            self.assertEqual(item.order, 3)
            saved = self.store.load()
            self.assertEqual(saved.items[1].download_status, "completed")
            self.assertEqual(saved.items[2].status, "running")
            self.assertEqual(saved.items[2].attempt_count, 1)
        def completed(outcome):
            if outcome.order == 2:
                self.assertEqual(self.store.load().items[2], self.before.items[2])
        adapter = OfflineAdapter(before_generate)
        state, outcomes = execute_sequential_batch(
            self.batch, self.run_dir, adapter, GenerationSettings(variants=1), order_completed=completed,
        )
        self.assertEqual(adapter.events, [("generate", 3, "source prompt 3"), ("download", 3, 1)])
        self.assertEqual([(o.generation, o.downloads) for o in outcomes[:2]], [("skipped_completed", "skipped_valid")] * 2)
        self.assertEqual(state.items[0], self.before.items[0])
        self.assertEqual(state.items[1].candidates, self.before.items[1].candidates)
        self.assertEqual(state.flow_project, self.before.flow_project)

    def test_missing_timestamp_with_completed_status_is_repaired(self):
        self.mutate(lambda s: setattr(s.items[1], "download_status", "completed"))
        _, item = self.resume_download()
        self.assertIsNotNone(item.download_completed_at)

    def test_not_started_order_metadata_is_repaired(self):
        self.mutate(lambda s: setattr(s.items[1], "download_status", "not_started"))
        self.assertEqual(self.resume_download()[1].download_status, "completed")

    def test_two_variants_are_both_validated_before_repair(self):
        self.make_state(variants=2)
        with patch("nano_batch.runner.validate_image_file", wraps=validate_image_file) as validate:
            _, item = self.resume_download()
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(item.candidates, self.before.items[1].candidates)

    def test_atomic_persistence_is_used_exactly_once(self):
        with patch("nano_batch.state.os.replace", wraps=os.replace) as atomic_replace:
            self.resume_download()
        atomic_replace.assert_called_once()
        source, destination = atomic_replace.call_args.args
        self.assertEqual(Path(source).parent, self.run_dir)
        self.assertEqual(Path(destination), self.store.path)
        self.assertFalse(Path(source).exists())

    def test_atomic_save_failure_preserves_previous_state_and_assets(self):
        before = self.store.path.read_bytes()
        path = Path(self.before.items[1].candidates[0].local_path)
        asset = path.read_bytes()
        with patch.object(RunStateStore, "_replace_atomically", side_effect=StatePersistenceError("synthetic failure")):
            with self.assertRaises(StatePersistenceError):
                self.resume_download()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(path.read_bytes(), asset)
        self.assertEqual(self.adapter.mock_calls, [])

    def test_already_completed_order_remains_read_only_and_skipped(self):
        self.mutate(lambda s: (setattr(s.items[1], "download_status", "completed"), setattr(s.items[1], "download_completed_at", "2026-10-07T10:20:40+00:00")))
        before = self.store.path.read_bytes()
        completed_batch = replace(self.batch, items=self.batch.items[:2])
        _, outcomes = execute_sequential_batch(completed_batch, self.run_dir, self.adapter, GenerationSettings(variants=1))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual([(o.generation, o.downloads) for o in outcomes], [("skipped_completed", "skipped_valid")] * 2)
        self.assertEqual(self.adapter.mock_calls, [])

    def test_resume_plan_stays_read_only(self):
        before = self.store.path.read_bytes()
        plan = plan_sequential_batch(self.batch, self.run_dir, GenerationSettings(variants=1))
        self.assertEqual(plan.orders[1].generation_action, "skip_completed")
        self.assertEqual(plan.orders[1].download_action, "skip_valid")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_sha256_mismatch_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1].candidates[0], "sha256", "0" * 64))

    def test_byte_size_mismatch_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1].candidates[0], "byte_size", 1))

    def test_corrupt_image_fails_closed(self):
        Path(self.before.items[1].candidates[0].local_path).write_bytes(b"not an image")
        self.assert_refused()

    def test_missing_file_fails_closed(self):
        Path(self.before.items[1].candidates[0].local_path).unlink()
        self.assert_refused()

    def test_nonregular_file_fails_closed(self):
        path = Path(self.before.items[1].candidates[0].local_path)
        path.unlink()
        path.mkdir()
        self.assert_refused()

    def test_empty_file_fails_closed(self):
        Path(self.before.items[1].candidates[0].local_path).write_bytes(b"")
        self.assert_refused()

    def test_dimensions_format_and_mime_must_match(self):
        for field, value in (("width", 1), ("height", 1), ("image_format", "PNG"), ("mime_type", "image/png")):
            with self.subTest(field=field):
                self.store.save(self.before)
                self.assert_refused(lambda s: setattr(s.items[1].candidates[0], field, value))

    def test_duplicate_uuid_ownership_fails_closed(self):
        def duplicate(state):
            state.items[2].candidates = [copy.deepcopy(state.items[1].candidates[0])]
        self.assert_refused(duplicate)

    def test_wrong_candidate_uuid_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1].candidates[0], "candidate_id", str(UUID(int=555))))

    def test_missing_or_invalid_uuid_fails_closed(self):
        for value in (None, "not-a-uuid"):
            with self.subTest(value=value):
                self.store.save(self.before)
                self.assert_refused(lambda s: setattr(s.items[1].candidates[0], "candidate_id", value))

    def test_missing_required_variant_fails_closed(self):
        self.make_state(variants=2)
        self.assert_refused(lambda s: s.items[1].candidates.pop(), sequential=True)

    def test_conflicting_candidate_download_error_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1].candidates[0], "download_error", "conflicting error"))

    def test_conflicting_order_download_error_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1], "download_error", "conflicting error"))

    def test_missing_generation_completion_proof_fails_closed(self):
        self.assert_refused(lambda s: setattr(s.items[1], "completed_at", None))

    def test_failed_order_is_never_repaired_or_automatically_retried(self):
        self.assert_refused(lambda s: setattr(s.items[1], "status", "failed"), sequential=True)

    def test_noninterrupted_failed_download_state_is_not_repaired(self):
        self.assert_refused(lambda s: setattr(s.items[1], "download_status", "failed"))

    def test_output_files_and_relative_path_must_point_to_same_canonical_file(self):
        for change in (
            lambda s: setattr(s.items[1], "output_files", []),
            lambda s: setattr(s.items[1], "output_files", [s.items[0].output_files[0]]),
            lambda s: setattr(s.items[1].candidates[0], "local_relative_path", "assets/wrong.jpeg"),
            lambda s: setattr(s.items[1].candidates[0], "local_path", s.items[0].output_files[0]),
        ):
            with self.subTest(change=change):
                self.store.save(self.before)
                self.assert_refused(change)

    def test_download_one_uses_saved_variant_proof_if_batch_does_not_declare_it(self):
        self.batch = replace(self.batch, items=tuple(replace(i, variants=None) for i in self.batch.items))
        self.assertEqual(self.resume_download()[1].download_status, "completed")

    def test_unknown_or_conflicting_saved_variant_count_fails_closed(self):
        self.batch = replace(self.batch, items=tuple(replace(i, variants=None) for i in self.batch.items))
        for details in (None, "not JSON", "{}", '{"configured_variants":1,"requested_variants":2}'):
            with self.subTest(details=details):
                self.store.save(self.before)
                self.assert_refused(lambda s: setattr(s.items[1], "result_details", details))

    def test_genuinely_missing_download_keeps_existing_download_only_recovery(self):
        path = Path(self.before.items[1].candidates[0].local_path)
        path.unlink()
        def missing(state):
            item = state.items[1]
            item.output_files = []
            item.candidates = [CandidateRecord(
                variant=1, suggested_filename=output_basename(item.order, item.visual_id, 1),
                candidate_id=item.candidates[0].candidate_id, source_kind="rendered-image",
            )]
        self.mutate(missing)
        adapter = OfflineAdapter()
        _, item = execute_download_one(self.batch, self.run_dir, 2, adapter)
        self.assertEqual(adapter.events, [("download", 2, 1)])
        self.assertEqual(item.attempt_count, self.before.items[1].attempt_count)
        self.assertEqual(item.download_attempt_count, self.before.items[1].download_attempt_count + 1)
        self.assertEqual(item.download_status, "completed")

    def test_one_cancel_signal_before_reconciliation_save_remains_authoritative(self):
        before = self.store.path.read_bytes()
        with authoritative_cancellation_scope() as cancellation:
            def completion_time():
                try:
                    raise cancellation.request()
                except KeyboardInterrupt:
                    return "2026-10-07T10:30:00+00:00"
            with patch("nano_batch.runner.utc_now", side_effect=completion_time), self.assertRaises(KeyboardInterrupt):
                self.resume_download()
            self.assertEqual(cancellation.interrupt_signal_count, 1)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.adapter.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
