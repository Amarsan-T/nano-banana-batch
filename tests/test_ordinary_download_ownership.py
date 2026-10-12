"""Stage 3: global file ownership at ordinary download boundaries, offline only."""

import copy
import unittest
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from PIL import Image

from nano_batch.downloads import commit_staged_file, validate_image_file
from nano_batch.models import BrowserDownloadResult, CandidateRecord
from nano_batch.runner import execute_download_one, output_basename, plan_download_one
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class OrdinaryDownloadOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_pending_download_fixture(Path(temporary.name) / "run")
        self.receipt = self.fixture.pending
        self.fixture.candidate.pending_download_commit = None
        self.fixture.candidate.download_status = "not_started"
        self.fixture.item.download_status = "not_started"
        self.fixture.item.download_attempt_count = 0
        self.fixture.item.download_started_at = None
        self.fixture.store.save(self.fixture.state)
        self.calls = []

    def download_candidate(self, *, candidate, destination):
        self.calls.append(candidate.candidate_id)
        Image.new("RGB", (16, 9), (20, 40, 60)).save(destination, format="JPEG")
        return BrowserDownloadResult(candidate.candidate_id, str(destination), mechanism="synthetic-offline")

    def execute(self, **kwargs):
        return execute_download_one(self.fixture.batch, self.fixture.run_dir, 1, self, **kwargs)

    def neighbor(self):
        item = self.fixture.state.items[1]
        item.candidates = [CandidateRecord(
            variant=1, suggested_filename=output_basename(2, item.visual_id, 1, "jpeg"),
            source_kind="rendered-image", candidate_id=str(UUID(int=201)), download_status="failed",
        )]
        return item, item.candidates[0]

    def inventory(self):
        return {path: path.read_bytes() for path in self.fixture.run_dir.rglob("*") if path.is_file()}

    def add_second_variant(self, saved):
        self.fixture.batch = replace(self.fixture.batch, items=(
            replace(self.fixture.batch.items[0], variants=2), self.fixture.batch.items[1],
        ))
        saved.items[0].result_details = '{"configured_variants":2,"requested_variants":2}'
        saved.items[0].candidates.append(CandidateRecord(
            variant=2, suggested_filename=output_basename(1, saved.items[0].visual_id, 2, "png"),
            source_kind="rendered-image", candidate_id=str(UUID(int=102)),
        ))

    def assert_planning_rejects(self, **kwargs):
        self.fixture.store.save(self.fixture.state)
        before = self.inventory()
        with self.assertRaises(ValueError):
            plan_download_one(self.fixture.batch, self.fixture.run_dir, 1, **kwargs)
        with patch("nano_batch.runner.commit_staged_file") as install, self.assertRaises(ValueError):
            self.execute(**kwargs)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.calls, [])
        install.assert_not_called()

    def test_new_download_rejects_another_candidates_stale_canonical_claim(self):
        _, other = self.neighbor()
        other.local_path = str(self.fixture.canonical_path)
        other.local_relative_path = self.receipt.canonical_relative_path
        self.assert_planning_rejects()

    def test_execute_does_not_install_or_complete_a_file_claimed_by_another_candidate(self):
        _, other = self.neighbor()
        other.local_path = str(self.fixture.canonical_path)
        other.local_relative_path = self.receipt.canonical_relative_path
        self.fixture.store.save(self.fixture.state)
        before = self.inventory()
        with patch("nano_batch.runner.commit_staged_file", wraps=commit_staged_file) as install, self.assertRaises(ValueError):
            self.execute()
        install.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.inventory(), before)

    def test_order_outputs_cannot_claim_a_new_candidate_file(self):
        for owner in (self.fixture.item, self.fixture.state.items[1]):
            with self.subTest(order=owner.order):
                owner.output_files = [self.receipt.canonical_relative_path]
                self.assert_planning_rejects()
                owner.output_files = []

    def test_same_stem_with_another_extension_is_a_conflict(self):
        _, other = self.neighbor()
        other.local_path = str(self.fixture.canonical_path.with_suffix(".png"))
        self.assert_planning_rejects()

    def test_ambiguous_or_escaping_neighbor_claims_fail_closed(self):
        _, other = self.neighbor()
        for value in (7, "C:ambiguous.jpeg", "../../outside.jpeg"):
            with self.subTest(value=value):
                other.local_relative_path = value
                self.assert_planning_rejects()

    def test_conflicting_absolute_and_relative_neighbor_claims_fail_closed(self):
        _, other = self.neighbor()
        other.local_path = str(self.fixture.run_dir / "assets" / other.suggested_filename)
        other.local_relative_path = "assets/unrelated.jpeg"
        self.assert_planning_rejects()

    def test_competing_pending_canonical_claim_is_rejected(self):
        _, other = self.neighbor()
        other.pending_download_commit = replace(self.receipt, order=2, candidate_id=other.candidate_id)
        self.assert_planning_rejects()

    def test_competing_valid_pending_staging_claim_is_checked_before_browser_download(self):
        neighbor, other = self.neighbor()
        other.pending_download_commit = replace(
            self.receipt, order=2, visual_id=neighbor.visual_id, candidate_id=other.candidate_id,
            canonical_relative_path="assets/" + other.suggested_filename,
        )
        self.fixture.store.save(self.fixture.state)

        @contextmanager
        def same_staging(*args, **kwargs):
            yield str(self.fixture.staging_path.parent)

        before = self.inventory()
        with patch("nano_batch.runner._download_staging_directory", new=same_staging), patch(
            "nano_batch.runner.commit_staged_file", wraps=commit_staged_file,
        ) as install, self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [])
        install.assert_not_called()
        self.assertEqual(self.fixture.staging_path.read_bytes(), before[self.fixture.staging_path])
        self.assertFalse(self.fixture.canonical_path.exists())
        saved = self.fixture.store.load()
        self.assertEqual(saved.items[1], self.fixture.state.items[1])
        self.assertNotEqual(saved.items[0].candidates[0].download_status, "completed")

    def test_nonconflicting_completed_neighbor_is_preserved(self):
        neighbor, other = self.neighbor()
        asset = self.fixture.run_dir / "assets" / other.suggested_filename
        Image.new("RGB", (16, 9), "blue").save(asset, format="JPEG")
        other.download_status = "completed"
        other.local_path = str(asset)
        other.local_relative_path = asset.relative_to(self.fixture.run_dir).as_posix()
        for field, value in asdict(validate_image_file(asset)).items():
            setattr(other, field, value)
        neighbor.output_files = [str(asset)]
        self.fixture.store.save(self.fixture.state)
        expected = copy.deepcopy(neighbor)
        content = asset.read_bytes()
        state, item = self.execute()
        self.assertEqual(state.items[1], expected)
        self.assertEqual(asset.read_bytes(), content)
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertIsNone(item.candidates[0].pending_download_commit)
        self.assertEqual(self.calls, [self.fixture.candidate.candidate_id])

    def assert_late_claim_rejected(self, boundary):
        injected = []
        real_validate = validate_image_file
        real_save = RunStateStore.save

        def inject():
            saved = self.fixture.store.load()
            saved.items[1].output_files = [str(self.fixture.canonical_path)]
            real_save(self.fixture.store, saved)
            injected.append(self.fixture.store.path.read_bytes())

        def validate_then_inject(path, **kwargs):
            metadata = real_validate(path, **kwargs)
            if (boundary == "before_receipt" and Path(path).suffix == ".download") or (
                boundary == "before_completion" and Path(path) == self.fixture.canonical_path
            ):
                if not injected:
                    inject()
            return metadata

        def save_then_inject(store, state):
            real_save(store, state)
            if boundary == "before_install" and not injected and state.items[0].candidates[0].pending_download_commit:
                inject()

        with patch("nano_batch.runner.validate_image_file", new=validate_then_inject), patch.object(
            RunStateStore, "save", new=save_then_inject,
        ), patch("nano_batch.runner.commit_staged_file", wraps=commit_staged_file) as install, self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(len(injected), 1)
        self.assertEqual(self.fixture.store.path.read_bytes(), injected[0])
        self.assertEqual(install.call_count, int(boundary == "before_completion"))
        saved = self.fixture.store.load()
        self.assertEqual(saved.items[0].status, "completed")
        self.assertEqual(saved.items[0].attempt_count, self.fixture.item.attempt_count)
        self.assertEqual(saved.items[0].automatic_policy_retry_count, 0)
        self.assertEqual(saved.items[0].automatic_generic_failure_retry_count, 0)
        self.assertNotEqual(saved.items[0].candidates[0].download_status, "completed")

    def test_late_claim_is_checked_before_receipt_persistence(self):
        self.assert_late_claim_rejected("before_receipt")

    def test_late_claim_is_checked_before_installation(self):
        self.assert_late_claim_rejected("before_install")

    def test_late_claim_is_checked_before_completion_persistence(self):
        self.assert_late_claim_rejected("before_completion")

    def test_download_error_does_not_overwrite_a_new_persisted_claim(self):
        injected = []

        def fail_after_claim(*args, **kwargs):
            saved = self.fixture.store.load()
            saved.items[1].output_files = [str(self.fixture.canonical_path)]
            self.fixture.store.save(saved)
            injected.append(self.fixture.store.path.read_bytes())
            raise RuntimeError("synthetic browser failure after a saved claim")

        with patch.object(self, "download_candidate", side_effect=fail_after_claim), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.fixture.store.path.read_bytes(), injected[0])
        self.assertFalse(self.fixture.canonical_path.exists())

    def test_ownership_rejection_preserves_staging_claimed_by_a_competing_pending_record(self):
        real_validate = validate_image_file
        evidence = []

        def validate_then_claim(path, **kwargs):
            metadata = real_validate(path, **kwargs)
            if Path(path).suffix == ".download" and not evidence:
                saved = self.fixture.store.load()
                neighbor = saved.items[1]
                other = CandidateRecord(
                    variant=1, suggested_filename=output_basename(2, neighbor.visual_id, 1, "jpeg"),
                    source_kind="rendered-image", candidate_id=str(UUID(int=201)),
                )
                other.pending_download_commit = replace(
                    self.receipt, order=2, visual_id=neighbor.visual_id, candidate_id=other.candidate_id,
                    canonical_relative_path="assets/" + other.suggested_filename,
                    staging_relative_path=Path(path).relative_to(self.fixture.run_dir).as_posix(),
                )
                neighbor.candidates = [other]
                self.fixture.store.save(saved)
                evidence.append((Path(path), Path(path).read_bytes(), self.fixture.store.path.read_bytes()))
            return metadata

        with patch("nano_batch.runner.validate_image_file", new=validate_then_claim), self.assertRaises(ValueError):
            self.execute()
        path, content, state_bytes = evidence[0]
        self.assertEqual(self.fixture.store.path.read_bytes(), state_bytes)
        self.assertTrue(path.is_file(), "Ownership rejection must retain disputed staging evidence")
        self.assertEqual(path.read_bytes(), content)
        self.assertFalse(self.fixture.canonical_path.exists())

    def test_claim_added_after_candidate_save_is_not_overwritten_by_order_finalization(self):
        real_save = RunStateStore.save
        injected = []

        def save_then_claim(store, state):
            real_save(store, state)
            if not injected and state.items[0].candidates[0].download_status == "completed":
                saved = self.fixture.store.load()
                saved.items[1].output_files = [str(self.fixture.canonical_path)]
                real_save(self.fixture.store, saved)
                injected.append(self.fixture.store.path.read_bytes())

        with patch.object(RunStateStore, "save", new=save_then_claim), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(len(injected), 1)
        self.assertEqual(self.fixture.store.path.read_bytes(), injected[0])
        saved = self.fixture.store.load()
        self.assertEqual(saved.items[0].candidates[0].download_status, "completed")
        self.assertEqual(saved.items[0].download_status, "running")

    def test_partial_two_variant_resume_preserves_completed_first_variant(self):
        self.execute()
        saved = self.fixture.store.load()
        self.add_second_variant(saved)
        saved.items[0].download_status = "failed"
        self.fixture.store.save(saved)
        first = copy.deepcopy(saved.items[0].candidates[0])
        content = self.fixture.canonical_path.read_bytes()
        self.calls.clear()
        _, item = self.execute()
        self.assertEqual(item.candidates[0], first)
        self.assertEqual(self.fixture.canonical_path.read_bytes(), content)
        self.assertEqual(self.calls, [str(UUID(int=102))])
        self.assertEqual(item.download_status, "completed")

    def test_claim_added_after_first_completion_stops_before_next_candidate_save(self):
        saved = self.fixture.store.load()
        self.add_second_variant(saved)
        self.fixture.store.save(saved)
        real_save = RunStateStore.save
        injected = []

        def save_then_claim(store, state):
            real_save(store, state)
            if not injected and state.items[0].candidates[0].download_status == "completed":
                current = self.fixture.store.load()
                second = current.items[0].candidates[1]
                current.items[1].output_files = ["assets/" + second.suggested_filename]
                real_save(self.fixture.store, current)
                injected.append(self.fixture.store.path.read_bytes())

        with patch.object(RunStateStore, "save", new=save_then_claim), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.calls, [str(UUID(int=101))])
        self.assertEqual(self.fixture.store.path.read_bytes(), injected[0])
        self.assertEqual(self.fixture.store.load().items[0].candidates[1].download_status, "not_started")

    def test_completed_download_is_idempotent_and_explicit_overwrite_still_works(self):
        self.execute()
        before = self.inventory()
        self.execute()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(len(self.calls), 1)
        _, item = self.execute(overwrite=True)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(item.download_status, "completed")
        self.assertIsNone(item.candidates[0].pending_download_commit)

    def test_explicit_overwrite_does_not_bypass_another_orders_claim(self):
        self.execute()
        self.fixture.state = self.fixture.store.load()
        self.fixture.state.items[1].output_files = [str(self.fixture.canonical_path)]
        self.calls.clear()
        self.assert_planning_rejects(overwrite=True)

    def test_skip_valid_does_not_hide_another_orders_file_claim(self):
        self.execute()
        self.fixture.state = self.fixture.store.load()
        self.fixture.state.items[1].output_files = [str(self.fixture.canonical_path)]
        self.calls.clear()
        self.assert_planning_rejects()

    def test_duplicate_selected_order_registrations_are_ambiguous(self):
        self.execute()
        self.fixture.state = self.fixture.store.load()
        self.fixture.item.output_files *= 2
        self.calls.clear()
        self.assert_planning_rejects()


if __name__ == "__main__":
    unittest.main()
