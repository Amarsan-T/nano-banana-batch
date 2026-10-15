"""Early ordinary-download confinement, using synthetic local files only."""

import copy
import gc
import os
import stat
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from PIL import Image

from nano_batch import runner as runner_module
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.models import BrowserDownloadResult, CandidateRecord
from nano_batch.downloads import validate_image_file
from nano_batch.runner import _download_staging_directory, execute_download_one, output_basename, plan_download_one
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class EarlyDownloadConfinementTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = make_pending_download_fixture(self.root / "run")
        self.receipt = self.fixture.pending
        self.fixture.candidate.pending_download_commit = None
        self.fixture.candidate.download_status = "not_started"
        self.fixture.item.download_status = "not_started"
        self.fixture.item.download_attempt_count = 0
        self.fixture.item.download_started_at = None
        self.fixture.store.save(self.fixture.state)
        self.calls = []

    def clear_fixture_readonly_files(self):
        # This root contains disposable synthetic fixtures only. Never traverse
        # symlink/junction targets while restoring permissions for test teardown.
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [name for name in dirs if not (
                (Path(directory) / name).is_symlink() or (Path(directory) / name).is_junction()
            )]
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink() and not path.is_junction():
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_planned_staging_cleanup_removes_a_valid_readonly_jpeg(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-readonly"
        try:
            with _download_staging_directory(assets, planned_dir=stage) as directory:
                self.assertEqual(Path(directory), stage)
                image = stage / "v01.download"
                Image.new("RGB", (16, 9), "blue").save(image, format="JPEG")
                image.chmod(stat.S_IREAD)
                self.assertEqual(validate_image_file(image).image_format, "JPEG")
        except PermissionError:
            self.fail("Valid read-only staging must retain the previous successful cleanup behavior")
        self.assertFalse(stage.exists())

    def test_readonly_download_finalizes_without_retry_and_cleans_staging(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        original = self.download_candidate

        def readonly_download(*, candidate, destination):
            result = original(candidate=candidate, destination=destination)
            destination.chmod(stat.S_IREAD)
            return result

        with patch.object(self, "download_candidate", new=readonly_download):
            try:
                state, item = self.execute()
            except PermissionError:
                saved = self.fixture.store.load().items[0]
                self.assertEqual(saved.candidates[0].download_status, "completed")
                self.assertEqual(saved.download_status, "running")
                self.fail("Read-only cleanup must not interrupt finalization of a valid committed download")
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertEqual(self.fixture.store.load(), state)
        self.assertEqual(validate_image_file(self.fixture.canonical_path).sha256, item.candidates[0].sha256)
        self.assertEqual(item.download_attempt_count, 1)
        self.assertEqual(item.attempt_count, self.fixture.item.attempt_count)
        self.assertEqual(item.automatic_policy_retry_count, 0)
        self.assertEqual(item.automatic_generic_failure_retry_count, 0)
        self.assertEqual(state.flow_project, self.fixture.state.flow_project)
        self.assertIsNone(item.candidates[0].pending_download_commit)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0][1].parent.exists())
        before = self.inventory()
        self.execute()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(len(self.calls), 1)

    def test_readonly_legacy_download_also_finalizes(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        state = copy.deepcopy(self.fixture.state)
        state.version, state.flow_project_required, state.flow_project = 3, False, None
        candidate = state.items[0].candidates[0]
        candidate.candidate_id = "synthetic-legacy-readonly"
        candidate.asset_url = "https://example.invalid/image/synthetic-legacy-readonly"
        run = self.root / "legacy-readonly"
        store = RunStateStore(run / "run_state.json")
        store.save(state)
        original = self.download_candidate

        def readonly_download(*, candidate, destination):
            result = original(candidate=candidate, destination=destination)
            destination.chmod(stat.S_IREAD)
            return result

        with patch.object(self, "download_candidate", new=readonly_download):
            final, item = self.execute(run_dir=run)
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertEqual(item.download_attempt_count, 1)
        self.assertIsNone(item.candidates[0].pending_download_commit)
        self.assertIsNone(final.flow_project)
        self.assertFalse(self.calls[0][1].parent.exists())
        self.assertEqual(store.load(), final)

    def test_cleanup_keeps_committed_canonical_attributes_and_bytes(self):
        observations = []
        cleanup = runner_module._cleanup_download_staging

        def observed_cleanup(directory, identity):
            canonical = self.fixture.canonical_path
            before = (canonical.stat().st_mode, getattr(canonical.stat(), "st_file_attributes", 0), canonical.read_bytes())
            cleanup(directory, identity)
            after = (canonical.stat().st_mode, getattr(canonical.stat(), "st_file_attributes", 0), canonical.read_bytes())
            self.assertEqual(after, before)
            observations.append(before)

        with patch.object(runner_module, "_cleanup_download_staging", new=observed_cleanup):
            self.execute()
        self.assertEqual(len(observations), 1)

    def test_planned_cleanup_preserves_cancel_pending_and_disputed_evidence(self):
        assets = self.fixture.run_dir / "assets"
        for reason in ("cancel", "pending", "ownership"):
            with self.subTest(reason=reason):
                stage = assets / (".staging-retained-" + reason)
                with patch.object(runner_module, "_cleanup_download_staging") as cleanup:
                    if reason == "cancel":
                        with authoritative_cancellation_scope() as cancellation, self.assertRaises(KeyboardInterrupt):
                            with _download_staging_directory(assets, planned_dir=stage):
                                (stage / "v01.download").write_bytes(b"synthetic pending bytes")
                                raise cancellation.request()
                        self.assertEqual(cancellation.interrupt_signal_count, 1)
                    elif reason == "ownership":
                        with self.assertRaises(runner_module._DownloadFileOwnershipError):
                            with _download_staging_directory(assets, planned_dir=stage):
                                (stage / "v01.download").write_bytes(b"synthetic pending bytes")
                                raise runner_module._DownloadFileOwnershipError("synthetic disputed ownership")
                    else:
                        with _download_staging_directory(assets, planned_dir=stage, preserve=lambda: True):
                            (stage / "v01.download").write_bytes(b"synthetic pending bytes")
                    cleanup.assert_not_called()
                self.assertEqual((stage / "v01.download").read_bytes(), b"synthetic pending bytes")

    def test_cancellation_during_planned_cleanup_remains_latched(self):
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-cancel-cleanup"
        with authoritative_cancellation_scope() as cancellation:
            def translated_cancel(*args, **kwargs):
                cancellation.request()
                return None

            with patch.object(runner_module.shutil, "rmtree", side_effect=translated_cancel), self.assertRaises(KeyboardInterrupt):
                with _download_staging_directory(assets, planned_dir=stage):
                    (stage / "v01.download").write_bytes(b"synthetic still writing")
        self.assertEqual(cancellation.interrupt_signal_count, 1)
        self.assertEqual((stage / "v01.download").read_bytes(), b"synthetic still writing")

    def test_planned_cleanup_rejects_escaped_allocation(self):
        assets = self.fixture.run_dir / "assets"
        escaped = self.root / ".staging-escaped"
        with self.assertRaises(ValueError):
            with _download_staging_directory(assets, planned_dir=escaped):
                self.fail("Escaped staging must not be allocated")
        self.assertFalse(escaped.exists())

    def test_cleanup_rejects_redirected_staging_root_without_touching_target(self):
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-redirected"
        target = self.root / "unrelated-cleanup-target"
        target.mkdir()
        protected = target / "protected.txt"
        protected.write_bytes(b"synthetic unrelated bytes")
        with self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets, planned_dir=stage):
                (stage / "v01.download").write_bytes(b"synthetic owned bytes")
                stage.rename(assets / ".staging-retained-original")
                self.directory_link(stage, target, force_junction=os.name == "nt")
        self.assertEqual(protected.read_bytes(), b"synthetic unrelated bytes")
        self.assertEqual((assets / ".staging-retained-original" / "v01.download").read_bytes(), b"synthetic owned bytes")

    def test_cleanup_rejects_replacement_directory_with_same_name(self):
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-replaced"
        with self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets, planned_dir=stage):
                stage.rename(assets / ".staging-original-directory")
                stage.mkdir()
                (stage / "protected.txt").write_bytes(b"synthetic replacement owner")
        self.assertEqual((stage / "protected.txt").read_bytes(), b"synthetic replacement owner")

    def test_nested_junction_or_symlink_never_exposes_unrelated_files_to_cleanup(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-nested-link"
        target = self.root / "unrelated-nested-target"
        target.mkdir()
        protected = target / "protected.jpeg"
        Image.new("RGB", (16, 9), "red").save(protected)
        protected.chmod(stat.S_IREAD)
        before = (protected.stat().st_mode, protected.read_bytes())
        with self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets, planned_dir=stage):
                self.directory_link(stage / "redirect", target, force_junction=os.name == "nt")
        self.assertEqual((protected.stat().st_mode, protected.read_bytes()), before)

    def test_readonly_hardlink_cannot_change_unrelated_file_attributes(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-shared-file"
        protected = self.root / "protected-hardlink.jpeg"
        Image.new("RGB", (16, 9), "red").save(protected)
        protected.chmod(stat.S_IREAD)
        before = (protected.stat().st_mode, protected.read_bytes())
        try:
            with _download_staging_directory(assets, planned_dir=stage):
                os.link(protected, stage / "v01.download")
        except runner_module._DownloadFileOwnershipError:
            self.assertEqual(os.name, "nt")
        else:
            self.assertNotEqual(os.name, "nt", "Windows must reject chmod of a shared read-only file")
        self.assertEqual((protected.stat().st_mode, protected.read_bytes()), before)

    def test_unplanned_helper_never_repairs_permissions_of_a_shared_file(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        protected = self.root / "protected-unplanned-hardlink.jpeg"
        Image.new("RGB", (16, 9), "red").save(protected)
        protected.chmod(stat.S_IREAD)
        before = (protected.stat().st_mode, protected.read_bytes())
        with self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets) as directory:
                stage = Path(directory)
                os.link(protected, stage / "v01.download")
        gc.collect()
        self.assertTrue((stage / "v01.download").is_file())
        self.assertEqual((protected.stat().st_mode, protected.read_bytes()), before)

    def test_unplanned_unsafe_cleanup_is_not_retried_by_a_gc_finalizer(self):
        assets = self.fixture.run_dir / "assets"
        target = self.root / "unplanned-protected-target"
        target.mkdir()
        protected = target / "protected.txt"
        protected.write_bytes(b"synthetic retained target")
        with self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets) as directory:
                stage = Path(directory)
                self.directory_link(stage / "redirect", target, force_junction=os.name == "nt")
        gc.collect()
        self.assertTrue(stage.exists())
        self.assertEqual(protected.read_bytes(), b"synthetic retained target")

    def test_cleanup_propagates_genuine_unlink_permission_failure(self):
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-real-error"
        with patch.object(runner_module.os, "unlink", side_effect=PermissionError("synthetic ACL failure")) as unlink:
            with self.assertRaisesRegex(PermissionError, "synthetic ACL failure"):
                with _download_staging_directory(assets, planned_dir=stage):
                    (stage / "v01.download").write_bytes(b"synthetic writable file")
            self.assertEqual(unlink.call_count, 1)
        self.assertEqual((stage / "v01.download").read_bytes(), b"synthetic writable file")

    def test_readonly_cleanup_retry_is_bounded_and_does_not_hide_a_real_failure(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-retry-error"
        with patch.object(runner_module.os, "unlink", side_effect=PermissionError("synthetic persistent failure")) as unlink:
            with self.assertRaisesRegex(PermissionError, "synthetic persistent failure"):
                with _download_staging_directory(assets, planned_dir=stage):
                    image = stage / "v01.download"
                    Image.new("RGB", (16, 9), "blue").save(image, format="JPEG")
                    image.chmod(stat.S_IREAD)
            self.assertEqual(unlink.call_count, 2 if os.name == "nt" else 1)
        self.assertEqual(validate_image_file(image).image_format, "JPEG")

    def test_cleanup_rejects_simulated_file_alias_without_changing_its_target(self):
        self.addCleanup(self.clear_fixture_readonly_files)
        assets = self.fixture.run_dir / "assets"
        stage = assets / ".staging-file-alias"
        protected = self.root / "protected-alias.jpeg"
        Image.new("RGB", (16, 9), "red").save(protected)
        protected.chmod(stat.S_IREAD)
        before = (protected.stat().st_mode, protected.read_bytes())
        real_resolve = Path.resolve
        redirect = False

        def alias(path, *args, **kwargs):
            return protected if redirect and path == stage / "v01.download" else real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=alias), self.assertRaises(runner_module._DownloadFileOwnershipError):
            with _download_staging_directory(assets, planned_dir=stage):
                (stage / "v01.download").write_bytes(b"synthetic alias")
                redirect = True
        self.assertEqual((protected.stat().st_mode, protected.read_bytes()), before)

    def download_candidate(self, *, candidate, destination):
        self.calls.append((candidate.candidate_id, destination))
        Image.new("RGB", (16, 9), "blue").save(destination, format="JPEG")
        return BrowserDownloadResult(candidate.candidate_id, str(destination), mechanism="synthetic-offline")

    def execute(self, *, run_dir=None, **kwargs):
        return execute_download_one(self.fixture.batch, run_dir or self.fixture.run_dir, 1, self, **kwargs)

    def inventory(self):
        result = {}
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in list(dirs) + files:
                path = Path(directory) / name
                key = path.relative_to(self.root).as_posix()
                if path.is_symlink() or path.is_junction():
                    result[key] = ("link", os.readlink(path))
                    if name in dirs:
                        dirs.remove(name)
                elif path.is_dir():
                    result[key] = ("directory",)
                else:
                    result[key] = ("file", path.read_bytes())
        return result

    def assert_rejected_without_effects(self, *, run_dir=None, overwrite=False):
        before, saved = self.inventory(), copy.deepcopy(self.fixture.store.load())
        with patch("nano_batch.runner._download_staging_directory") as allocate, patch.object(
            RunStateStore, "save", autospec=True,
        ) as save, patch("nano_batch.runner.commit_staged_file") as install:
            with self.assertRaises(ValueError):
                self.execute(run_dir=run_dir, overwrite=overwrite)
            allocate.assert_not_called()
            save.assert_not_called()
            install.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.fixture.store.load(), saved)
        self.assertEqual(saved.items[0].download_attempt_count, 0)

    def redirect(self, source, destination):
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            resolved = path.absolute()
            try:
                relative = resolved.relative_to(source)
            except ValueError:
                return real_resolve(path, *args, **kwargs)
            return destination / relative

        return patch.object(Path, "resolve", new=resolve)

    def directory_link(self, link, target, *, force_junction=False):
        """Prefer a real symlink; Windows junctions do not need symlink privileges."""
        try:
            if force_junction:
                raise OSError("Native junction requested")
            link.symlink_to(target, target_is_directory=True)
            kind = "symlink"
        except OSError:
            if os.name != "nt":
                raise
            # Both absolute paths were derived from this disposable fixture.
            for path in (link, target):
                path.relative_to(self.root)
                self.assertNotIn('"', str(path))
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            self.assertTrue(link.is_junction())
            kind = "junction"

        def remove_link():
            if link.is_junction():
                link.rmdir()  # Remove only the link, never recursively remove its target.
            else:
                link.unlink()

        self.addCleanup(remove_link)
        return kind

    def test_redirected_assets_root_is_rejected_before_attempt_and_adapter(self):
        """Reproduce the audit with the real allocator, state writes and mock download."""
        assets = self.fixture.run_dir / "assets"
        outside = self.root / "outside-assets"
        outside.mkdir()
        before = self.inventory()
        with self.redirect(assets, outside), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual((self.fixture.store.load().items[0].download_attempt_count, len(self.calls)), (0, 0))
        self.assertEqual(self.inventory(), before)

    def test_assets_escape_has_no_persistence_allocation_or_installation_calls(self):
        assets = self.fixture.run_dir / "assets"
        outside = self.root / "outside-assets"
        outside.mkdir()
        (outside / "unrelated.txt").write_text("synthetic protected bytes", encoding="utf-8")
        with self.redirect(assets, outside):
            self.assert_rejected_without_effects()

    def test_project_owned_download_succeeds_and_resume_is_idempotent(self):
        state, item = self.execute()
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.download_attempt_count, 1)
        self.assertEqual(item.attempt_count, self.fixture.item.attempt_count)
        self.assertEqual(state.flow_project, self.fixture.state.flow_project)
        self.assertEqual(Path(item.candidates[0].local_path), self.fixture.canonical_path)
        self.assertIsNone(item.candidates[0].pending_download_commit)
        before = self.inventory()
        self.execute()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(len(self.calls), 1)

    def test_legacy_download_without_project_or_uuid_still_succeeds(self):
        state = self.fixture.state
        state.version, state.flow_project_required, state.flow_project = 3, False, None
        self.fixture.candidate.candidate_id = "synthetic-legacy-candidate"
        self.fixture.candidate.asset_url = "https://example.invalid/image/synthetic-legacy-candidate"
        legacy_run = self.root / "legacy-run"
        RunStateStore(legacy_run / "run_state.json").save(state)
        saved, item = self.execute(run_dir=legacy_run)
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.download_attempt_count, 1)
        self.assertEqual(item.candidates[0].candidate_id, "synthetic-legacy-candidate")
        self.assertIsNone(saved.flow_project)
        self.assertFalse(saved.flow_project_required)
        self.assertIsNone(item.candidates[0].pending_download_commit)

    def test_missing_assets_directory_is_valid_and_planning_does_not_create_it(self):
        assets = self.fixture.run_dir / "assets"
        assets.rename(self.fixture.run_dir / "fixture-evidence")
        before = self.inventory()
        plan_download_one(self.fixture.batch, self.fixture.run_dir, 1)
        self.assertEqual(self.inventory(), before)
        self.assertFalse(assets.exists())
        self.assertEqual(self.execute()[1].download_status, "completed")

    def test_run_root_alias_resolves_to_the_same_trusted_run(self):
        alias = self.root / "run-alias"
        self.directory_link(alias, self.fixture.run_dir)
        plan = plan_download_one(self.fixture.batch, alias, 1)
        self.assertEqual(plan.asset_dir, self.fixture.run_dir / "assets")
        self.assertEqual(self.execute(run_dir=alias)[1].download_status, "completed")

    def test_canonical_escape_is_rejected_before_any_effects(self):
        source = self.fixture.run_dir / "assets" / self.fixture.candidate.suggested_filename
        with self.redirect(source, self.root / "outside.png"):
            self.assert_rejected_without_effects()

    def test_canonical_alias_to_another_in_root_filename_is_rejected(self):
        source = self.fixture.run_dir / "assets" / self.fixture.candidate.suggested_filename
        target = source.with_name("unrelated.png")
        Image.new("RGB", (16, 9), "red").save(target)
        with self.redirect(source, target):
            self.assert_rejected_without_effects(overwrite=True)

    def test_staging_escape_is_rejected_before_allocation_or_attempt(self):
        real_resolve = Path.resolve
        outside = self.root / "outside-staging"
        outside.mkdir()

        def resolve(path, *args, **kwargs):
            resolved = path.absolute()
            for parent in (resolved, *resolved.parents):
                if parent.name.startswith(".staging-") and parent != self.fixture.staging_path.parent:
                    return outside / resolved.relative_to(parent)
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolve):
            self.assert_rejected_without_effects()

    def test_resolution_errors_fail_closed_without_mutation(self):
        real_resolve = Path.resolve
        assets = self.fixture.run_dir / "assets"
        for error in (OSError("synthetic inaccessible path"), RuntimeError("synthetic link loop")):
            with self.subTest(error=type(error).__name__):
                def resolve(path, *args, **kwargs):
                    if path == assets:
                        raise error
                    return real_resolve(path, *args, **kwargs)

                with patch.object(Path, "resolve", new=resolve):
                    self.assert_rejected_without_effects()

    def test_native_assets_redirection_outside_run_is_rejected(self):
        assets = self.fixture.run_dir / "assets"
        assets.rename(self.fixture.run_dir / "original-assets")
        outside = self.root / "outside-assets"
        outside.mkdir()
        kind = self.directory_link(assets, outside, force_junction=os.name == "nt")
        self.assertEqual(kind, "junction" if os.name == "nt" else "symlink")
        self.assert_rejected_without_effects()

    def test_native_assets_alias_inside_run_is_rejected(self):
        assets = self.fixture.run_dir / "assets"
        target = self.fixture.run_dir / "original-assets"
        assets.rename(target)
        self.directory_link(assets, target)
        self.assert_rejected_without_effects()

    def test_preexisting_planned_staging_alias_is_rejected(self):
        token = UUID(int=777)
        staging = self.fixture.run_dir / "assets" / (".staging-" + token.hex)
        target = self.root / "outside-staging"
        target.mkdir()
        self.directory_link(staging, target)
        with patch("uuid.uuid4", return_value=token):
            self.assert_rejected_without_effects()

    def test_staging_file_escape_is_rejected_before_directory_allocation(self):
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path.name == "v01.download" and path.parent != self.fixture.staging_path.parent:
                return self.root / "outside.download"
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolve):
            self.assert_rejected_without_effects()

    def test_dangling_assets_link_is_not_treated_as_a_missing_directory(self):
        assets = self.fixture.run_dir / "assets"
        assets.rename(self.fixture.run_dir / "original-assets")
        target = self.root / "link-target"
        target.mkdir()
        self.directory_link(assets, target)
        target.rename(self.root / "moved-target")
        self.assert_rejected_without_effects()

    def test_redirected_alternate_canonical_extension_is_checked_before_attempt(self):
        with self.redirect(self.fixture.canonical_path, self.root / "outside.jpeg"):
            self.assert_rejected_without_effects()

    def test_existing_planned_staging_directory_is_never_reused(self):
        token = UUID(int=777)
        staging = self.fixture.run_dir / "assets" / (".staging-" + token.hex)
        staging.mkdir()
        (staging / "protected.txt").write_text("synthetic owned bytes", encoding="utf-8")
        with patch("uuid.uuid4", return_value=token):
            self.assert_rejected_without_effects()

    def test_missing_staging_claimed_by_another_pending_record_is_rejected_early(self):
        token = UUID(int=777)
        neighbor = self.fixture.state.items[1]
        other = CandidateRecord(
            variant=1, suggested_filename=output_basename(2, neighbor.visual_id, 1, "jpeg"),
            candidate_id=str(UUID(int=201)), source_kind="rendered-image",
        )
        other.pending_download_commit = replace(
            self.receipt, order=2, visual_id=neighbor.visual_id, candidate_id=other.candidate_id,
            canonical_relative_path="assets/" + other.suggested_filename,
            staging_relative_path="assets/.staging-" + token.hex + "/v01.download",
        )
        neighbor.candidates = [other]
        self.fixture.store.save(self.fixture.state)
        with patch("uuid.uuid4", return_value=token):
            self.assert_rejected_without_effects()


if __name__ == "__main__":
    unittest.main()
