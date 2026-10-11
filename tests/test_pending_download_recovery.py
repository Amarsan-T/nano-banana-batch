"""Real synthetic files and atomic state replacements for pending recovery."""

import copy
import json
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image

from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.downloads import commit_staged_file, validate_image_file
from nano_batch.models import BrowserDownloadResult, CandidateRecord, GenerationSettings
from nano_batch.runner import (
    execute_download_one, execute_sequential_batch, output_basename, plan_download_one,
)
from nano_batch.state import RunStateStore, StatePersistenceError
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class PendingDownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_pending_download_fixture(Path(temporary.name) / "run")
        self.adapter = MagicMock(spec=["download_candidate"])
        self.adapter.download_candidate.side_effect = AssertionError("local recovery must not download")

    def recover(self, **kwargs):
        return execute_download_one(self.fixture.batch, self.fixture.run_dir, 1, self.adapter, **kwargs)

    def canonical_only(self):
        self.fixture.canonical_path.write_bytes(self.fixture.staging_path.read_bytes())
        self.fixture.staging_path.unlink()

    def completed_neighbor(self):
        """An independently owned, fully valid completed download in Order 2."""
        fixture = self.fixture
        item = fixture.state.items[1]
        path = fixture.run_dir / "assets" / output_basename(2, item.visual_id, 1, "jpeg")
        Image.new("RGB", (16, 9), "blue").save(path, format="JPEG")
        candidate = CandidateRecord(
            variant=1, suggested_filename=path.name, source_kind="rendered-image",
            candidate_id=str(UUID(int=201)), download_status="completed",
            local_path=str(path), local_relative_path=str(path.relative_to(fixture.run_dir)),
            downloaded_at="synthetic neighbor completion", **asdict(validate_image_file(path)),
        )
        item.status = "completed"
        item.attempt_count = 1
        item.completed_at = "synthetic neighbor generation"
        item.download_status = "completed"
        item.download_completed_at = "synthetic neighbor completion"
        item.result_details = '{"configured_variants":1,"requested_variants":1}'
        item.candidates = [candidate]
        item.output_files = [str(path)]
        return item, candidate, path

    def assert_complete(self, recovered):
        state, item = recovered
        candidate = item.candidates[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(candidate.download_status, "completed")
        self.assertIsNone(candidate.pending_download_commit)
        self.assertEqual(candidate.candidate_id, str(UUID(int=101)))
        self.assertEqual(candidate.local_path, str(self.fixture.canonical_path))
        expected = validate_image_file(self.fixture.canonical_path)
        for field, value in asdict(expected).items():
            self.assertEqual(getattr(candidate, field), value)
        self.assertEqual(item.output_files, [str(self.fixture.canonical_path)])
        for field in ("attempt_count", "download_attempt_count", "completed_at", "prompt",
                      "automatic_policy_retry_count", "automatic_generic_failure_retry_count"):
            self.assertEqual(getattr(item, field), getattr(self.fixture.item, field))
        self.assertEqual(state.flow_project, self.fixture.state.flow_project)
        self.assertEqual(self.fixture.store.load(), state)
        self.adapter.download_candidate.assert_not_called()

    def assert_rejected_without_writes(self, error=ValueError):
        state_bytes = self.fixture.store.path.read_bytes()
        files = {path: path.read_bytes() for path in self.fixture.run_dir.rglob("*") if path.is_file()}
        with patch("nano_batch.runner.commit_staged_file") as install, self.assertRaises(error):
            self.recover()
        install.assert_not_called()
        self.adapter.download_candidate.assert_not_called()
        self.assertEqual(self.fixture.store.path.read_bytes(), state_bytes)
        self.assertEqual({path: path.read_bytes() for path in files}, files)

    def test_staging_only_recovers_without_another_download_or_attempt(self):
        with patch("nano_batch.runner.commit_staged_file", wraps=commit_staged_file) as install:
            self.assert_complete(self.recover())
        install.assert_called_once_with(self.fixture.staging_path, self.fixture.canonical_path, overwrite=False)

    def test_canonical_only_recovers_without_installation(self):
        self.canonical_only()
        with patch("nano_batch.runner.commit_staged_file") as install:
            self.assert_complete(self.recover())
        install.assert_not_called()

    def test_direct_recovery_rejects_canonical_claimed_by_completed_candidate(self):
        self.canonical_only()
        _, candidate, _ = self.completed_neighbor()
        candidate.local_path = str(self.fixture.canonical_path)
        candidate.local_relative_path = str(self.fixture.canonical_path.relative_to(self.fixture.run_dir))
        for field, value in asdict(validate_image_file(self.fixture.canonical_path)).items():
            setattr(candidate, field, value)
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes()

    def test_sequential_resume_rejects_canonical_registered_by_other_completed_order(self):
        self.canonical_only()
        neighbor, _, _ = self.completed_neighbor()
        neighbor.output_files = [str(self.fixture.canonical_path)]
        self.fixture.store.save(self.fixture.state)
        before = {path: path.read_bytes() for path in self.fixture.run_dir.rglob("*") if path.is_file()}
        adapter = MagicMock(spec=["generate_one", "download_candidate"])
        with patch("nano_batch.runner.commit_staged_file") as install, self.assertRaises(ValueError):
            execute_sequential_batch(
                self.fixture.batch, self.fixture.run_dir, adapter, GenerationSettings(variants=1),
            )
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        adapter.assert_not_called()
        self.assertEqual(adapter.mock_calls, [])
        install.assert_not_called()

    def test_recovery_preserves_unrelated_completed_download_and_its_claims(self):
        neighbor, _, path = self.completed_neighbor()
        self.fixture.store.save(self.fixture.state)
        before = copy.deepcopy(neighbor)
        asset = path.read_bytes()
        self.assert_complete(self.recover())
        self.assertEqual(self.fixture.store.load().items[1], before)
        self.assertEqual(path.read_bytes(), asset)

    def test_sequential_recovery_with_unrelated_completed_order_is_idempotent(self):
        neighbor, _, path = self.completed_neighbor()
        self.fixture.store.save(self.fixture.state)
        before = copy.deepcopy(neighbor)
        asset = path.read_bytes()
        adapter = MagicMock(spec=["generate_one", "download_candidate"])
        state, outcomes = execute_sequential_batch(
            self.fixture.batch, self.fixture.run_dir, adapter, GenerationSettings(variants=1),
        )
        self.assertEqual([entry.downloads for entry in outcomes], ["resumed", "skipped_valid"])
        self.assertEqual(state.items[1], before)
        state_bytes = self.fixture.store.path.read_bytes()
        execute_sequential_batch(
            self.fixture.batch, self.fixture.run_dir, adapter, GenerationSettings(variants=1),
        )
        self.assertEqual(self.fixture.store.path.read_bytes(), state_bytes)
        self.assertEqual(path.read_bytes(), asset)
        self.assertEqual(adapter.mock_calls, [])

    def test_other_candidate_relative_path_or_incomplete_metadata_still_claims_file(self):
        _, candidate, _ = self.completed_neighbor()
        candidate.local_relative_path = self.fixture.pending.canonical_relative_path
        candidate.download_status = "failed"  # A stale status does not erase its path claim.
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes()

    def test_run_relative_and_duplicate_order_outputs_cannot_hide_claims(self):
        neighbor, _, _ = self.completed_neighbor()
        neighbor.output_files = [self.fixture.pending.canonical_relative_path] * 2
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes()
        self.assert_rejected_without_writes()  # Repeated rejection leaves receipt/files intact.

    def test_other_order_cannot_register_pending_staging_as_output(self):
        neighbor, _, _ = self.completed_neighbor()
        neighbor.output_files = [str(self.fixture.staging_path)]
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes()

    def test_other_candidate_cannot_claim_same_canonical_stem_with_another_extension(self):
        _, candidate, _ = self.completed_neighbor()
        alternate = self.fixture.canonical_path.with_suffix(".jpg")
        candidate.local_path = str(alternate)
        candidate.local_relative_path = str(alternate.relative_to(self.fixture.run_dir))
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes()

    def test_claim_path_aliases_resolve_against_trusted_run_root(self):
        neighbor, candidate, _ = self.completed_neighbor()
        original = copy.deepcopy(self.fixture.state)
        aliases = (
            str(self.fixture.canonical_path).swapcase(),
            self.fixture.pending.canonical_relative_path.replace("/", "\\"),
            "./" + self.fixture.pending.canonical_relative_path,
            "assets/../" + self.fixture.pending.canonical_relative_path,
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                self.fixture.state = copy.deepcopy(original)
                self.fixture.state.items[1].output_files = [alias]
                self.fixture.store.save(self.fixture.state)
                self.assert_rejected_without_writes()

    def test_symlink_or_junction_alias_in_other_candidate_claim_is_rejected(self):
        _, candidate, _ = self.completed_neighbor()
        alias = self.fixture.canonical_path.with_name("synthetic-alias.jpeg")
        candidate.local_path = str(alias)
        candidate.local_relative_path = str(alias.relative_to(self.fixture.run_dir))
        self.fixture.store.save(self.fixture.state)
        real_resolve = Path.resolve

        def resolve_alias(path, *args, **kwargs):
            resolved = real_resolve(path, *args, **kwargs)
            return self.fixture.canonical_path if resolved == alias else resolved

        with patch.object(Path, "resolve", new=resolve_alias):
            self.assert_rejected_without_writes()

    def test_malformed_or_escaping_file_claims_fail_closed(self):
        self.completed_neighbor()
        original = copy.deepcopy(self.fixture.state)
        for field, value in (
            ("local_path", 7), ("local_path", "C:ambiguous.jpeg"),
            ("local_path", str(self.fixture.run_dir.parent / "outside.jpeg")),
            ("local_relative_path", str(self.fixture.canonical_path)),
            ("local_relative_path", "../../outside.jpeg"),
        ):
            with self.subTest(field=field, value=value):
                self.fixture.state = copy.deepcopy(original)
                setattr(self.fixture.state.items[1].candidates[0], field, value)
                self.fixture.store.save(self.fixture.state)
                self.assert_rejected_without_writes()

    def assert_late_claim_rejected(self, *, canonical_exists):
        if canonical_exists:
            self.canonical_only()
        neighbor, _, _ = self.completed_neighbor()
        self.fixture.store.save(self.fixture.state)
        real_validate = validate_image_file
        reads = 0
        injected_state = []

        def validate_then_add_claim(path, **kwargs):
            nonlocal reads
            result = real_validate(path, **kwargs)
            reads += 1
            if reads == 3:  # Planning, initial recovery check, final pre-commit byte check.
                neighbor.output_files = [str(self.fixture.canonical_path)]
                self.fixture.store.save(self.fixture.state)
                injected_state.append(self.fixture.store.path.read_bytes())
            return result

        with patch("nano_batch.runner.validate_image_file", new=validate_then_add_claim), patch(
            "nano_batch.runner.commit_staged_file",
        ) as install, self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(len(injected_state), 1)
        self.assertEqual(self.fixture.store.path.read_bytes(), injected_state[0])
        install.assert_not_called()
        self.assertEqual(self.fixture.canonical_path.exists(), canonical_exists)
        self.assertIsNotNone(self.fixture.store.load().items[0].candidates[0].pending_download_commit)
        self.adapter.download_candidate.assert_not_called()

    def test_new_persisted_claim_is_rechecked_before_recovery_install(self):
        self.assert_late_claim_rejected(canonical_exists=False)

    def test_new_persisted_claim_is_rechecked_before_completion_save(self):
        self.assert_late_claim_rejected(canonical_exists=True)

    def test_changed_pending_receipt_is_not_silently_rebased_at_completion(self):
        self.canonical_only()
        real_validate = validate_image_file
        reads = 0
        changed = []

        def validate_then_change_receipt(path, **kwargs):
            nonlocal reads
            result = real_validate(path, **kwargs)
            reads += 1
            if reads == 3:
                self.fixture.replace_pending(download_mechanism="synthetic-different-receipt")
                self.fixture.store.save(self.fixture.state)
                changed.append(self.fixture.store.path.read_bytes())
            return result

        with patch("nano_batch.runner.validate_image_file", new=validate_then_change_receipt), self.assertRaisesRegex(
            ValueError, "evidence changed",
        ):
            self.recover()
        self.assertEqual(len(changed), 1)
        self.assertEqual(self.fixture.store.path.read_bytes(), changed[0])
        self.assertEqual(self.fixture.store.load().items[0].candidates[0].download_status, "running")

    def test_fresh_unrelated_completion_is_preserved_at_recovery_boundary(self):
        real_validate = validate_image_file
        reads = 0
        neighbor_completion = []

        def validate_then_complete_neighbor(path, **kwargs):
            nonlocal reads
            result = real_validate(path, **kwargs)
            reads += 1
            if reads == 3:
                neighbor, _, asset = self.completed_neighbor()
                neighbor_completion.append((copy.deepcopy(neighbor), asset, asset.read_bytes()))
                self.fixture.store.save(self.fixture.state)
            return result

        with patch("nano_batch.runner.validate_image_file", new=validate_then_complete_neighbor):
            state, _ = self.recover()
        self.assertEqual(len(neighbor_completion), 1)
        neighbor, asset, content = neighbor_completion[0]
        self.assertEqual(state.items[1], neighbor)
        self.assertEqual(asset.read_bytes(), content)
        self.assertEqual(state.items[0].download_attempt_count, self.fixture.item.download_attempt_count)
        self.adapter.download_candidate.assert_not_called()

    def test_both_matching_files_recover_without_overwrite(self):
        self.fixture.canonical_path.write_bytes(self.fixture.staging_path.read_bytes())
        original = self.fixture.canonical_path.read_bytes()
        with patch("nano_batch.runner.commit_staged_file") as install:
            self.assert_complete(self.recover())
        install.assert_not_called()
        self.assertEqual(self.fixture.canonical_path.read_bytes(), original)

    def test_repeated_recovery_is_idempotent_in_state_and_assets(self):
        self.recover()
        state_bytes = self.fixture.store.path.read_bytes()
        asset_bytes = self.fixture.canonical_path.read_bytes()
        self.recover()
        self.assertEqual(self.fixture.store.path.read_bytes(), state_bytes)
        self.assertEqual(self.fixture.canonical_path.read_bytes(), asset_bytes)
        self.adapter.download_candidate.assert_not_called()

    def test_preview_validates_pending_evidence_without_installing_or_saving(self):
        state_bytes = self.fixture.store.path.read_bytes()
        with patch("nano_batch.runner.commit_staged_file") as install, patch.object(RunStateStore, "save") as save:
            plan = plan_download_one(self.fixture.batch, self.fixture.run_dir, 1)
        self.assertEqual(plan.candidates[0].action, "recover_pending")
        self.assertEqual(plan.candidates[0].destination, self.fixture.canonical_path)
        self.assertEqual(self.fixture.store.path.read_bytes(), state_bytes)
        self.assertFalse(self.fixture.canonical_path.exists())
        install.assert_not_called()
        save.assert_not_called()

    def test_missing_both_files_never_marks_completion(self):
        self.fixture.staging_path.unlink()
        self.assert_rejected_without_writes()

    def test_each_persisted_integrity_field_is_required(self):
        original = self.fixture.pending
        for field, value in (
            ("sha256", "0" * 64), ("byte_size", original.byte_size + 1),
            ("image_format", "PNG"), ("mime_type", "image/png"),
            ("width", original.width + 1), ("height", original.height + 1),
        ):
            with self.subTest(field=field):
                changes = {field: value}
                if field in {"image_format", "mime_type"}:
                    changes.update(image_format="PNG", mime_type="image/png")
                    changes["canonical_relative_path"] = original.canonical_relative_path[:-4] + "png"
                self.fixture.candidate.pending_download_commit = replace(original, **changes)
                self.fixture.store.save(self.fixture.state)
                self.assert_rejected_without_writes()

    def test_newly_calculated_valid_canonical_digest_is_insufficient(self):
        self.canonical_only()
        Image.new("RGB", (16, 9), "red").save(self.fixture.canonical_path, format="JPEG")
        self.assertNotEqual(validate_image_file(self.fixture.canonical_path).sha256, self.fixture.pending.sha256)
        self.assert_rejected_without_writes(FileExistsError)

    def test_bad_staging_is_not_ignored_when_canonical_matches(self):
        self.fixture.canonical_path.write_bytes(self.fixture.staging_path.read_bytes())
        Image.new("RGB", (16, 9), "red").save(self.fixture.staging_path, format="JPEG")
        self.assert_rejected_without_writes()

    def test_partial_copy_fallback_asset_remains_protected(self):
        self.fixture.canonical_path.write_bytes(self.fixture.staging_path.read_bytes()[:50])
        self.assert_rejected_without_writes(FileExistsError)

    def test_alternate_canonical_extension_is_not_overwritten(self):
        alternate = self.fixture.canonical_path.with_suffix(".png")
        Image.new("RGB", (16, 9)).save(alternate, format="PNG")
        self.assert_rejected_without_writes(FileExistsError)

    def test_old_orphan_without_a_pending_record_is_not_adopted(self):
        self.canonical_only()
        self.fixture.candidate.pending_download_commit = None
        self.fixture.store.save(self.fixture.state)
        self.assert_rejected_without_writes(FileExistsError)

    def test_legacy_pending_without_project_proof_is_not_adopted(self):
        self.canonical_only()
        raw = json.loads(self.fixture.store.path.read_text(encoding="utf-8"))
        raw["flow_project"] = None
        raw["flow_project_required"] = False
        raw["items"][0]["candidates"][0]["pending_download_commit"]["project_id"] = None
        # Disposable historical-state simulation; no production ownership edit.
        self.fixture.store.path.write_text(json.dumps(raw), encoding="utf-8")
        self.assert_rejected_without_writes()

    def test_explicit_overwrite_cannot_bypass_a_pending_record(self):
        before = self.fixture.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "without --overwrite"):
            self.recover(overwrite=True)
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.adapter.download_candidate.assert_not_called()

    def test_completion_save_failure_keeps_durable_pending_and_valid_asset(self):
        before = self.fixture.store.path.read_bytes()
        real_save = RunStateStore.save

        def failed_completion(store, state):
            if state.items[0].candidates[0].download_status == "completed":
                raise StatePersistenceError("synthetic completion persistence failure")
            return real_save(store, state)

        with patch.object(RunStateStore, "save", new=failed_completion), self.assertRaises(StatePersistenceError):
            self.recover()
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.assertEqual(validate_image_file(self.fixture.canonical_path).sha256, self.fixture.pending.sha256)
        self.assertIsNotNone(self.fixture.store.load().items[0].candidates[0].pending_download_commit)
        self.assert_complete(self.recover())

    def test_record_clear_and_candidate_completion_share_one_atomic_save(self):
        self.canonical_only()
        real_replace = RunStateStore._replace_atomically
        replacements = []

        def inspect_replace(store, temporary):
            document = json.loads(Path(temporary).read_text(encoding="utf-8"))
            candidate = document["items"][0]["candidates"][0]
            if candidate["download_status"] == "completed":
                self.assertNotIn("pending_download_commit", candidate)
                self.assertEqual(candidate["sha256"], self.fixture.pending.sha256)
                self.assertEqual(document["items"][0]["output_files"], [str(self.fixture.canonical_path)])
                replacements.append(document)
            return real_replace(store, temporary)

        with patch.object(RunStateStore, "_replace_atomically", new=inspect_replace):
            self.recover()
        self.assertEqual(len(replacements), 2)  # Candidate completion, then unchanged Order finalization contract.

    def test_cancellation_during_recovery_install_keeps_original_attempt_and_receipt(self):
        before = self.fixture.store.path.read_bytes()
        with authoritative_cancellation_scope() as cancellation:
            def install_then_interrupt(staged, canonical, *, overwrite):
                commit_staged_file(staged, canonical, overwrite=overwrite)
                raise cancellation.request()
            with patch("nano_batch.runner.commit_staged_file", new=install_then_interrupt), self.assertRaises(KeyboardInterrupt):
                self.recover()
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.assertEqual(cancellation.interrupt_signal_count, 1)
        self.assert_complete(self.recover())

    def test_candidate_path_is_rechecked_before_recovery_install(self):
        real_resolve = Path.resolve
        real_validate = validate_image_file
        redirect = False

        def validate_then_redirect(*args, **kwargs):
            nonlocal redirect
            result = real_validate(*args, **kwargs)
            redirect = True
            return result

        def redirected_resolve(path, *args, **kwargs):
            resolved = real_resolve(path, *args, **kwargs)
            if redirect and resolved == self.fixture.canonical_path:
                return resolved.with_name("wrong-candidate.jpeg")
            return resolved

        before = self.fixture.store.path.read_bytes()
        with patch("nano_batch.runner.validate_image_file", new=validate_then_redirect), patch.object(
            Path, "resolve", new=redirected_resolve,
        ), patch("nano_batch.runner.commit_staged_file") as install, self.assertRaises(ValueError):
            self.recover()
        install.assert_not_called()
        self.assertEqual(self.fixture.store.path.read_bytes(), before)

    def test_new_file_at_install_boundary_is_never_overwritten(self):
        original = self.fixture.staging_path.read_bytes()
        before = self.fixture.store.path.read_bytes()

        def competing_file(staged, canonical, *, overwrite):
            canonical.write_bytes(original)
            commit_staged_file(staged, canonical, overwrite=overwrite)

        with patch("nano_batch.runner.commit_staged_file", new=competing_file), self.assertRaises(FileExistsError):
            self.recover()
        self.assertEqual(self.fixture.canonical_path.read_bytes(), original)
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        # A later run may use matching evidence; the record does not prove the writer.
        self.assert_complete(self.recover())

    def test_partial_order_recovers_one_candidate_and_downloads_only_the_other(self):
        fixture = self.fixture
        fixture.batch = replace(fixture.batch, items=(replace(fixture.batch.items[0], variants=2), *fixture.batch.items[1:]))
        fixture.item.result_details = '{"configured_variants":2,"requested_variants":2}'
        other_id = str(UUID(int=102))
        fixture.item.candidates.append(CandidateRecord(
            variant=2, suggested_filename=output_basename(1, fixture.item.visual_id, 2, "png"),
            source_kind="rendered-image", candidate_id=other_id,
        ))
        fixture.store.save(fixture.state)
        original = fixture.staging_path.read_bytes()

        def download_other(*, candidate, destination):
            self.assertEqual(candidate.candidate_id, other_id)
            Image.new("RGB", (16, 9), "blue").save(destination, format="PNG")
            return BrowserDownloadResult(candidate_id=other_id, staged_path=str(destination))

        self.adapter.download_candidate.side_effect = download_other
        _, item = self.recover()
        self.assertEqual(item.download_attempt_count, fixture.item.download_attempt_count + 1)
        self.assertEqual(item.attempt_count, fixture.item.attempt_count)
        self.assertEqual(fixture.canonical_path.read_bytes(), original)
        self.assertEqual([entry.download_status for entry in item.candidates], ["completed", "completed"])
        self.assertTrue(all(entry.pending_download_commit is None for entry in item.candidates))
        self.adapter.download_candidate.assert_called_once()

    def test_existing_completed_candidate_is_preserved_when_the_second_recovers(self):
        fixture = self.fixture
        pending = fixture.pending
        first = fixture.candidate
        fixture.canonical_path.write_bytes(fixture.staging_path.read_bytes())
        first.download_status = "completed"
        first.suggested_filename = fixture.canonical_path.name
        first.local_path = str(fixture.canonical_path)
        first.local_relative_path = str(fixture.canonical_path.relative_to(fixture.run_dir))
        for field, value in asdict(validate_image_file(fixture.canonical_path)).items():
            setattr(first, field, value)
        first.downloaded_at = "synthetic prior completion"
        first.pending_download_commit = None
        fixture.item.output_files = [str(fixture.canonical_path)]
        canonical = fixture.canonical_path.with_name(output_basename(1, fixture.item.visual_id, 2, "jpeg"))
        staged = fixture.staging_path.with_name("v02.download")
        staged.write_bytes(fixture.staging_path.read_bytes())
        fixture.item.candidates.append(CandidateRecord(
            variant=2, suggested_filename=output_basename(1, fixture.item.visual_id, 2, "png"),
            source_kind="rendered-image", candidate_id=str(UUID(int=102)), download_status="running",
            pending_download_commit=replace(
                pending, variant=2, candidate_id=str(UUID(int=102)),
                canonical_relative_path=canonical.relative_to(fixture.run_dir).as_posix(),
                staging_relative_path=staged.relative_to(fixture.run_dir).as_posix(),
            ),
        ))
        fixture.batch = replace(fixture.batch, items=(replace(fixture.batch.items[0], variants=2), *fixture.batch.items[1:]))
        fixture.item.result_details = '{"configured_variants":2,"requested_variants":2}'
        fixture.store.save(fixture.state)
        first_before = copy.deepcopy(first)
        bytes_before = fixture.canonical_path.read_bytes()
        _, item = self.recover()
        self.assertEqual(item.candidates[0], first_before)
        self.assertEqual(fixture.canonical_path.read_bytes(), bytes_before)
        self.assertEqual(item.download_attempt_count, fixture.item.download_attempt_count)
        self.assertEqual(item.download_status, "completed")
        self.assertIsNone(item.candidates[1].pending_download_commit)
        self.adapter.download_candidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
