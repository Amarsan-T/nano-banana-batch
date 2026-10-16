"""Stage 2.2 checkpoint: reproduce the commit-before-state recovery gap offline."""

import copy
import os
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image

from nano_batch import downloads
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.downloads import commit_staged_file, validate_image_file
from nano_batch.models import (
    Batch, BatchItem, BrowserDownloadResult, DiscoveredCandidate,
    FlowProjectReference, GenerationResult, GenerationSettings,
)
from nano_batch.runner import (
    execute_download_one, execute_one, execute_sequential_batch,
    initialize_run_state, output_basename,
)
from nano_batch.state import RunStateStore, StatePersistenceError
from pending_download_fixtures import pending_download_test_directory


class SyntheticJpegDownload:
    def __init__(self):
        self.calls = []

    def download_candidate(self, *, candidate, destination):
        self.calls.append((candidate.candidate_id, candidate.variant))
        Image.new("RGB", (16, 9), (20, 40, 60)).save(destination, format="JPEG")
        return BrowserDownloadResult(
            candidate_id=candidate.candidate_id,
            staged_path=str(destination),
            browser_suggested_filename="synthetic-flow.jpeg",
            browser_download_guid="synthetic-download-guid",
            mechanism="synthetic-offline-download",
        )


class DownloadCommitRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"
        self.store = RunStateStore(self.run_dir / "run_state.json")
        self.batch = Batch(
            batch_id="sha256:synthetic-download-commit-gap",
            source_schema="test",
            items=(BatchItem(order=1, visual_id="synthetic_visual", prompt="synthetic prompt", variants=1),),
        )
        self.settings = GenerationSettings(variants=1)
        self.candidate_id = str(UUID(int=101))
        state = initialize_run_state(self.batch, self.run_dir, require_flow_project=True)
        project_id = str(UUID(int=999))
        state.flow_project = FlowProjectReference(
            project_id, f"https://flow.google.com/project/{project_id}",
        )
        self.store.save(state)
        generation = MagicMock(spec=["generate_one"])
        generation.generate_one.return_value = GenerationResult(
            candidates=(DiscoveredCandidate(
                source_kind="rendered-image", candidate_id=self.candidate_id,
                asset_url=f"https://flow-content.google/image/{self.candidate_id}",
            ),),
            details='{"configured_variants":1,"requested_variants":1}',
        )
        execute_one(self.batch, self.run_dir, 1, generation, self.settings)
        self.generated = self.store.load()
        self.canonical = (
            self.run_dir / "assets" / output_basename(1, "synthetic_visual", 1, "jpeg")
        ).resolve()
        self.adapter = SyntheticJpegDownload()

    def interrupt_at(self, boundary):
        committed = []
        completion_save = []
        real_save = RunStateStore.save

        with authoritative_cancellation_scope() as cancellation:
            def commit_then_interrupt(staged, destination, *, overwrite):
                # Install actual bytes through the production commit function.
                # No fabricated canonical file or persisted completion metadata.
                commit_staged_file(staged, destination, overwrite=overwrite)
                self.assertEqual(destination, self.canonical)
                self.assertFalse(overwrite)
                committed.append(validate_image_file(destination))
                if boundary == "after_commit":
                    raise cancellation.request()

            def save_then_interrupt(store, state):
                candidate = state.items[0].candidates[0]
                if candidate.download_status == "completed":
                    completion_save.append(copy.deepcopy(candidate))
                    if boundary == "before_candidate_save":
                        raise cancellation.request()
                return real_save(store, state)

            with patch("nano_batch.runner.commit_staged_file", new=commit_then_interrupt), patch.object(
                RunStateStore, "save", new=save_then_interrupt,
            ), self.assertRaises(KeyboardInterrupt):
                execute_download_one(self.batch, self.run_dir, 1, self.adapter)
            self.assertEqual(cancellation.interrupt_signal_count, 1)

        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0].image_format, "JPEG")
        self.assertEqual(self.adapter.calls, [(self.candidate_id, 1)])
        if boundary == "before_candidate_save":
            self.assertEqual(len(completion_save), 1)
            self.assertEqual(completion_save[0].sha256, committed[0].sha256)
            self.assertEqual(completion_save[0].local_path, str(self.canonical))
        else:
            self.assertEqual(completion_save, [])

        surviving = self.store.load()
        item = surviving.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.attempt_count, self.generated.items[0].attempt_count)
        self.assertEqual(item.completed_at, self.generated.items[0].completed_at)
        self.assertEqual(item.download_status, "running")
        self.assertEqual(item.candidates[0].download_status, "running")
        self.assertEqual(item.candidates[0].candidate_id, self.candidate_id)
        self.assertEqual(item.output_files, [])
        self.assertIsNone(item.download_completed_at)
        self.assertIsNone(item.candidates[0].downloaded_at)
        self.assertEqual(surviving.flow_project, self.generated.flow_project)
        return surviving

    def resume_adapter(self):
        adapter = MagicMock(spec=["generate_one", "download_candidate"])
        adapter.generate_one.side_effect = AssertionError("must not regenerate completed generation")
        adapter.download_candidate.side_effect = AssertionError("must not replace committed JPEG")
        return adapter

    def assert_resume_recovers(self, boundary):
        surviving = self.interrupt_at(boundary)
        state_bytes = self.store.path.read_bytes()
        asset_bytes = self.canonical.read_bytes()
        metadata = validate_image_file(self.canonical, expected_filename=self.canonical.name)
        adapter = self.resume_adapter()
        try:
            recovered, outcomes = execute_sequential_batch(
                self.batch, self.run_dir, adapter, self.settings,
            )
        except FileExistsError as exc:
            self.assertIn("Refusing to overwrite untracked candidate asset", str(exc))
            self.assertEqual(adapter.mock_calls, [])
            self.assertEqual(self.store.path.read_bytes(), state_bytes)
            self.assertEqual(self.canonical.read_bytes(), asset_bytes)
            self.fail(
                f"Desired interrupted-download recovery is unavailable at {boundary}: "
                f"{exc}. The JPEG is installed, but candidate completion is not durable."
            )

        self.assertEqual(adapter.mock_calls, [])
        item = recovered.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertEqual(item.candidates[0].candidate_id, self.candidate_id)
        self.assertEqual(item.candidates[0].local_path, str(self.canonical))
        for field, value in asdict(metadata).items():
            self.assertEqual(getattr(item.candidates[0], field), value)
        self.assertEqual(item.output_files, [str(self.canonical)])
        self.assertIsNotNone(item.download_completed_at)
        self.assertEqual(recovered.flow_project, surviving.flow_project)
        for field in (
            "attempt_count", "download_attempt_count", "automatic_policy_retry_count",
            "automatic_generic_failure_retry_count", "prompt", "completed_at",
        ):
            self.assertEqual(getattr(item, field), getattr(surviving.items[0], field))
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        self.assertEqual(self.canonical.read_bytes(), asset_bytes)
        self.assertEqual(self.store.load(), recovered)

    def test_resume_recovers_jpeg_interrupted_after_canonical_commit(self):
        self.assert_resume_recovers("after_commit")

    def test_resume_recovers_jpeg_interrupted_before_candidate_state_save(self):
        self.assert_resume_recovers("before_candidate_save")

    def test_valid_external_jpeg_with_running_candidate_is_not_adopted(self):
        self.interrupt_at("after_commit")
        # Replace only this disposable synthetic fixture with unrelated valid bytes.
        Image.new("RGB", (16, 9), (200, 180, 160)).save(self.canonical, format="JPEG")
        self.assertEqual(validate_image_file(self.canonical).image_format, "JPEG")
        state_bytes = self.store.path.read_bytes()
        asset_bytes = self.canonical.read_bytes()
        adapter = self.resume_adapter()
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite untracked candidate asset"):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.store.path.read_bytes(), state_bytes)
        self.assertEqual(self.canonical.read_bytes(), asset_bytes)

    def test_uninterrupted_download_persists_candidate_and_order_completion(self):
        _, item = execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertEqual(item.candidates[0].candidate_id, self.candidate_id)
        self.assertEqual(item.candidates[0].sha256, validate_image_file(self.canonical).sha256)
        self.assertEqual(self.store.load().items[0], item)

    def test_pending_evidence_is_durable_before_the_first_install(self):
        real_commit = commit_staged_file
        inspected = []

        def inspect_then_commit(staged, destination, *, overwrite):
            saved = self.store.load()
            item = saved.items[0]
            pending = item.candidates[0].pending_download_commit
            self.assertIsNotNone(pending)
            self.assertEqual(saved.version, 6)
            self.assertEqual(pending.candidate_id, self.candidate_id)
            self.assertEqual(pending.project_id, saved.flow_project.project_id)
            self.assertEqual(pending.generation_attempt_count, item.attempt_count)
            self.assertEqual(pending.download_attempt_count, item.download_attempt_count)
            self.assertEqual(self.run_dir / pending.staging_relative_path, staged)
            self.assertEqual(self.run_dir / pending.canonical_relative_path, destination)
            for field, value in asdict(validate_image_file(staged)).items():
                self.assertEqual(getattr(pending, field), value)
            self.assertFalse(destination.exists())
            inspected.append(pending)
            real_commit(staged, destination, overwrite=overwrite)

        with patch("nano_batch.runner.commit_staged_file", new=inspect_then_commit):
            _, item = execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        self.assertEqual(len(inspected), 1)
        self.assertIsNone(item.candidates[0].pending_download_commit)

    def assert_interrupted_staging_recovers(self):
        with authoritative_cancellation_scope() as cancellation:
            with patch("nano_batch.runner.commit_staged_file", side_effect=lambda *a, **k: cancellation.request()), self.assertRaises(KeyboardInterrupt):
                # Returning an interrupt is insufficient; the shared latch raises
                # on exit from the blocking call even if it returns normally.
                execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        saved = self.store.load()
        pending = saved.items[0].candidates[0].pending_download_commit
        self.assertIsNotNone(pending)
        self.assertFalse(self.canonical.exists())
        staged = self.run_dir / pending.staging_relative_path
        self.assertTrue(staged.is_file())
        # Windows rename consumes this pathname; retain independent evidence
        # before recovery rather than requiring a second name for the bytes.
        expected_bytes = staged.read_bytes()
        expected_metadata = validate_image_file(staged)
        for field, value in asdict(expected_metadata).items():
            self.assertEqual(getattr(pending, field), value)
        adapter = self.resume_adapter()
        recovered, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(self.canonical.read_bytes(), expected_bytes)
        self.assertEqual(validate_image_file(self.canonical), expected_metadata)
        item = recovered.items[0]
        candidate = item.candidates[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(candidate.download_status, "completed")
        self.assertEqual(candidate.candidate_id, pending.candidate_id)
        self.assertEqual(candidate.candidate_id, self.candidate_id)
        self.assertEqual(candidate.local_path, str(self.canonical))
        self.assertEqual(item.output_files, [str(self.canonical)])
        self.assertIsNone(candidate.pending_download_commit)
        for field, value in asdict(expected_metadata).items():
            self.assertEqual(getattr(candidate, field), value)
        for field in (
            "attempt_count", "download_attempt_count", "automatic_policy_retry_count",
            "automatic_generic_failure_retry_count", "prompt", "completed_at",
        ):
            self.assertEqual(getattr(item, field), getattr(saved.items[0], field))
        self.assertEqual(recovered.batch_id, saved.batch_id)
        self.assertEqual(recovered.flow_project, saved.flow_project)
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        adapter.assert_not_called()
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.adapter.calls, [(self.candidate_id, 1)])
        self.assertEqual(self.store.load(), recovered)

        state_bytes = self.store.path.read_bytes()
        repeated, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(repeated, recovered)
        self.assertEqual(outcomes[0].downloads, "skipped_valid")
        self.assertEqual(self.store.path.read_bytes(), state_bytes)
        self.assertEqual(self.canonical.read_bytes(), expected_bytes)
        self.assertEqual(adapter.mock_calls, [])
        return staged

    def test_interrupt_before_install_recovers_the_saved_staged_image(self):
        self.assert_interrupted_staging_recovers()

    def test_interrupt_before_install_recovers_through_hard_link(self):
        with patch.object(downloads.os, "link", wraps=os.link) as link, patch.object(
            downloads.os, "rename", side_effect=AssertionError("Hard-link recovery must not rename"),
        ) as rename:
            staged = self.assert_interrupted_staging_recovers()
        link.assert_called_once()
        rename.assert_not_called()
        self.assertTrue(staged.is_file())
        self.assertTrue(os.path.samefile(staged, self.canonical))

    @unittest.skipUnless(os.name == "nt", "Native Windows no-overwrite rename semantics")
    def test_interrupt_before_install_recovers_through_windows_rename(self):
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic hard-link unavailable")) as link, patch.object(
            downloads.os, "rename", wraps=os.rename,
        ) as rename:
            staged = self.assert_interrupted_staging_recovers()
        link.assert_called_once()
        rename.assert_called_once_with(staged.resolve(), self.canonical)
        self.assertFalse(staged.exists())

    def test_failed_pending_save_prevents_installation(self):
        real_save = RunStateStore.save

        def fail_receipt(store, state):
            if state.items[0].candidates[0].pending_download_commit is not None:
                raise StatePersistenceError("synthetic pending save failure")
            return real_save(store, state)

        with patch.object(RunStateStore, "save", new=fail_receipt), patch(
            "nano_batch.runner.commit_staged_file",
        ) as install, self.assertRaises(StatePersistenceError):
            execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        install.assert_not_called()
        self.assertFalse(self.canonical.exists())
        self.assertIsNone(self.store.load().items[0].candidates[0].pending_download_commit)

    def test_non_cancellation_install_error_preserves_receipt_and_staging(self):
        with patch("nano_batch.runner.commit_staged_file", side_effect=PermissionError("synthetic lock")), self.assertRaises(PermissionError):
            execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        saved = self.store.load()
        item = saved.items[0]
        self.assertEqual(item.download_status, "running")
        self.assertIsNone(item.download_error)
        staged = self.run_dir / item.candidates[0].pending_download_commit.staging_relative_path
        self.assertTrue(staged.is_file())
        recovered, _ = execute_sequential_batch(self.batch, self.run_dir, self.resume_adapter(), self.settings)
        self.assertEqual(recovered.items[0].download_status, "completed")
        self.assertEqual(recovered.items[0].download_attempt_count, item.download_attempt_count)

    def test_failed_candidate_save_after_install_remains_locally_recoverable(self):
        real_save = RunStateStore.save

        def fail_completion(store, state):
            if state.items[0].candidates[0].download_status == "completed":
                raise StatePersistenceError("synthetic completion save failure")
            return real_save(store, state)

        with patch.object(RunStateStore, "save", new=fail_completion), self.assertRaises(StatePersistenceError):
            execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        saved = self.store.load()
        self.assertEqual(saved.items[0].candidates[0].download_status, "running")
        self.assertIsNotNone(saved.items[0].candidates[0].pending_download_commit)
        recovered, _ = execute_sequential_batch(self.batch, self.run_dir, self.resume_adapter(), self.settings)
        self.assertEqual(recovered.items[0].download_status, "completed")
        self.assertEqual(recovered.items[0].download_attempt_count, saved.items[0].download_attempt_count)

    def test_changed_bytes_after_install_are_not_sealed_with_a_new_digest(self):
        def install_then_change(staged, destination, *, overwrite):
            commit_staged_file(staged, destination, overwrite=overwrite)
            Image.new("RGB", (16, 9), "red").save(destination, format="JPEG")

        with patch("nano_batch.runner.commit_staged_file", new=install_then_change), self.assertRaises(FileExistsError):
            execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        state = self.store.load()
        candidate = state.items[0].candidates[0]
        self.assertEqual(candidate.download_status, "running")
        self.assertIsNone(candidate.sha256)
        self.assertIsNotNone(candidate.pending_download_commit)
        self.assertNotEqual(candidate.pending_download_commit.sha256, validate_image_file(self.canonical).sha256)

    def test_saved_metadata_uses_the_observation_matched_to_pending_evidence(self):
        real_validate = validate_image_file
        interposed = []

        def temporarily_changed_content(path, **kwargs):
            if path == self.canonical and not interposed:
                original = path.read_bytes()
                Image.new("RGB", (16, 9), "red").save(path, format="JPEG")
                unrelated_metadata = real_validate(path, **kwargs)
                path.write_bytes(original)
                interposed.append(unrelated_metadata)
                return unrelated_metadata
            return real_validate(path, **kwargs)

        with patch("nano_batch.runner.validate_image_file", new=temporarily_changed_content):
            _, item = execute_download_one(self.batch, self.run_dir, 1, self.adapter)
        actual = real_validate(self.canonical)
        self.assertEqual(len(interposed), 1)
        self.assertNotEqual(interposed[0].sha256, actual.sha256)
        self.assertEqual(item.candidates[0].sha256, actual.sha256)


if __name__ == "__main__":
    unittest.main()
