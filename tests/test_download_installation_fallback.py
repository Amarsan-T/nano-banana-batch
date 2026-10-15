"""No-overwrite publication regressions, using disposable synthetic files only."""

import os
import shutil
import stat
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from nano_batch import downloads
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.models import BrowserDownloadResult
from nano_batch.runner import execute_download_one, plan_download_one
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class _InstallationFixture(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_pending_download_fixture(Path(temporary.name) / "run")
        self.expected = self.fixture.pending
        self.fixture.candidate.pending_download_commit = None
        self.fixture.candidate.download_status = "not_started"
        self.fixture.item.download_status = "not_started"
        self.fixture.item.download_attempt_count = 0
        self.fixture.item.download_started_at = None
        self.fixture.store.save(self.fixture.state)
        self.calls = []
        self.readonly = False

    def download_candidate(self, *, candidate, destination):
        self.calls.append(destination)
        Image.new("RGB", (16, 9), (20, 40, 60)).save(destination, format="JPEG")
        if self.readonly:
            destination.chmod(stat.S_IREAD)
        return BrowserDownloadResult(candidate.candidate_id, str(destination), mechanism="synthetic-offline")

    def execute(self):
        return execute_download_one(self.fixture.batch, self.fixture.run_dir, 1, self)

    def prepare_legacy_run(self):
        state = self.fixture.state
        state.version, state.flow_project_required, state.flow_project = 3, False, None
        self.fixture.candidate.candidate_id = "synthetic-legacy-installation"
        self.fixture.candidate.asset_url = "https://example.invalid/synthetic-legacy-installation"
        # A separate historical run, never removal of existing project ownership.
        self.fixture.run_dir = self.fixture.run_dir.parent / "legacy-run"
        self.fixture.canonical_path = self.fixture.run_dir / "assets" / self.fixture.canonical_path.name
        self.fixture.store = RunStateStore(self.fixture.run_dir / "run_state.json")
        self.fixture.store.save(state)

    def pending(self):
        state = self.fixture.store.load()
        receipt = state.items[0].candidates[0].pending_download_commit
        self.assertIsNotNone(receipt)
        self.assertEqual(state.items[0].download_status, "running")
        self.assertEqual(state.items[0].download_attempt_count, 1)
        return state, receipt, self.fixture.run_dir / receipt.staging_relative_path

    def assert_complete(self, result):
        state, item = result
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.assertIsNone(item.candidates[0].pending_download_commit)
        self.assertEqual(item.download_attempt_count, 1)
        for field in ("attempt_count", "automatic_policy_retry_count", "automatic_generic_failure_retry_count", "prompt"):
            self.assertEqual(getattr(item, field), getattr(self.fixture.item, field))
        self.assertEqual(state.flow_project, self.fixture.state.flow_project)
        metadata = downloads.validate_image_file(self.fixture.canonical_path)
        for field in ("sha256", "byte_size", "image_format", "mime_type", "width", "height"):
            self.assertEqual(getattr(metadata, field), getattr(self.expected, field))
            self.assertEqual(getattr(item.candidates[0], field), getattr(self.expected, field))
        self.assertEqual(self.fixture.store.load(), state)
        self.assertEqual(len(self.calls), 1)

    def assert_idempotent_resume(self):
        adapter = MagicMock(spec=["download_candidate"])
        adapter.download_candidate.side_effect = AssertionError("Resume must not download again")
        result = execute_download_one(self.fixture.batch, self.fixture.run_dir, 1, adapter)
        self.assert_complete(result)
        before = self.fixture.store.path.read_bytes(), self.fixture.canonical_path.read_bytes()
        self.assertEqual(execute_download_one(self.fixture.batch, self.fixture.run_dir, 1, adapter), result)
        self.assertEqual((self.fixture.store.path.read_bytes(), self.fixture.canonical_path.read_bytes()), before)
        adapter.download_candidate.assert_not_called()

    @staticmethod
    def exclusive_simulated_rename(source, target):
        # Portable failure/ordering simulation, not evidence of rename atomicity.
        # Exclusive creation prevents overwriting even on POSIX test hosts.
        with target.open("xb") as handle:
            handle.write(source.read_bytes())
        source.unlink()

    @contextmanager
    def simulated_windows_fallback(self):
        # Replace only this module's os reference; do not change pathlib's host OS.
        with patch.object(downloads, "os", wraps=os) as platform:
            platform.name = "nt"
            platform.link.side_effect = OSError("synthetic hard-link unavailable")
            platform.rename.side_effect = self.exclusive_simulated_rename
            yield platform


class SimulatedInstallationFallbackTests(_InstallationFixture):
    def test_interrupted_fallback_never_streams_partial_canonical(self):
        """Before the correction this leaves 50 canonical bytes beside valid staging."""
        with authoritative_cancellation_scope() as cancellation, self.simulated_windows_fallback() as platform:
            def partial_copy(source, target, length=0):
                target.write(source.read(50))
                raise cancellation.request()

            def interrupt_before_rename(*args):
                raise cancellation.request()

            platform.rename.side_effect = interrupt_before_rename
            with patch.object(shutil, "copyfileobj", side_effect=partial_copy) as copy_file:
                with self.assertRaises(KeyboardInterrupt):
                    self.execute()
                self.assertFalse(self.fixture.canonical_path.exists(), "Partial canonical bytes were exposed")
                copy_file.assert_not_called()
        _, receipt, staged = self.pending()
        self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)
        self.assertEqual(cancellation.interrupt_signal_count, 1)

    def test_fallback_flushes_closed_staging_before_rename_and_finalizes(self):
        events, descriptor = [], []
        with self.simulated_windows_fallback() as platform:
            def flush(fd):
                events.append("flush")
                descriptor.append(fd)
                os.fsync(fd)

            def rename(source, target):
                self.assertEqual(events, ["flush"])
                with self.assertRaises(OSError):
                    os.fstat(descriptor[0])  # Flush handle was closed before publication.
                self.assertIsNotNone(self.fixture.store.load().items[0].candidates[0].pending_download_commit)
                events.append("rename")
                self.exclusive_simulated_rename(source, target)

            platform.fsync.side_effect = flush
            platform.rename.side_effect = rename
            self.assert_complete(self.execute())
            self.assertEqual(events, ["flush", "rename"])
            self.assertFalse(self.calls[0].parent.exists())
        self.assert_idempotent_resume()

    def test_non_windows_link_failure_never_uses_overwriting_rename_or_copy(self):
        with self.simulated_windows_fallback() as platform:
            platform.name = "posix"
            with self.assertRaises(OSError):
                self.execute()
            platform.rename.assert_not_called()
            platform.fsync.assert_not_called()
        _, receipt, staged = self.pending()
        self.assertFalse(self.fixture.canonical_path.exists())
        self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)

    def test_preexisting_destination_is_not_overwritten(self):
        staged = self.fixture.staging_path
        canonical = self.fixture.canonical_path
        canonical.write_bytes(b"synthetic unrelated owner")
        before = staged.read_bytes(), canonical.read_bytes()
        with self.simulated_windows_fallback() as platform, self.assertRaises(FileExistsError):
            downloads.commit_staged_file(staged, canonical, overwrite=False)
        platform.link.assert_not_called()
        platform.rename.assert_not_called()
        self.assertEqual((staged.read_bytes(), canonical.read_bytes()), before)

    def test_explicit_overwrite_retains_existing_replace_contract(self):
        staged, canonical = self.fixture.staging_path, self.fixture.canonical_path
        expected = staged.read_bytes()
        canonical.write_bytes(b"synthetic explicitly replaceable bytes")
        with self.simulated_windows_fallback() as platform:
            downloads.commit_staged_file(staged, canonical, overwrite=True)
            platform.replace.assert_called_once_with(staged, canonical)
            platform.link.assert_not_called()
            platform.rename.assert_not_called()
        self.assertEqual(canonical.read_bytes(), expected)
        self.assertFalse(staged.exists())

    def test_cross_volume_preflight_refuses_publication_without_flushing(self):
        staged, canonical = self.fixture.staging_path, self.fixture.canonical_path
        real_stat = Path.stat
        def different_volume(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == canonical.parent:
                fields = list(info)
                fields[2] = info.st_dev + 1
                return os.stat_result(fields)
            return info
        before = staged.read_bytes()
        with self.simulated_windows_fallback() as platform, patch.object(Path, "stat", new=different_volume):
            with self.assertRaisesRegex(OSError, "same volume"):
                downloads.commit_staged_file(staged, canonical, overwrite=False)
            platform.rename.assert_not_called()
            platform.fsync.assert_not_called()
        self.assertFalse(canonical.exists())
        self.assertEqual(staged.read_bytes(), before)

    def test_legacy_download_completes_without_gaining_pending_recovery(self):
        self.prepare_legacy_run()
        with self.simulated_windows_fallback():
            result = self.execute()
        self.assert_complete(result)
        self.assertEqual(result[0].version, 3)
        self.assertFalse(self.calls[0].parent.exists())

    def test_legacy_publication_failure_retains_staging_without_orphan_adoption(self):
        self.prepare_legacy_run()
        with self.simulated_windows_fallback() as platform:
            platform.rename.side_effect = OSError("synthetic legacy publication failure")
            with self.assertRaisesRegex(OSError, "legacy publication failure"):
                self.execute()
        self.assertTrue(self.calls[0].exists(), "Publication failure erased legacy staging evidence")
        self.assertFalse(self.fixture.canonical_path.exists())
        self.assertEqual(downloads.validate_image_file(self.calls[0]).sha256, self.expected.sha256)
        saved = self.fixture.store.load().items[0]
        self.assertEqual(saved.download_status, "failed")
        self.assertEqual(saved.download_attempt_count, 1)
        self.assertIsNone(saved.candidates[0].pending_download_commit)
        before = self.fixture.store.path.read_bytes()
        plan = plan_download_one(self.fixture.batch, self.fixture.run_dir, 1)
        self.assertEqual(plan.candidates[0].action, "download")  # No new legacy adoption right.
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    def test_late_collision_preserves_destination_staging_and_receipt(self):
        with self.simulated_windows_fallback() as platform:
            def competing_rename(source, target):
                target.write_bytes(b"synthetic competing owner")
                self.exclusive_simulated_rename(source, target)
            platform.rename.side_effect = competing_rename
            with self.assertRaises(FileExistsError):
                self.execute()
        _, receipt, staged = self.pending()
        self.assertEqual(self.fixture.canonical_path.read_bytes(), b"synthetic competing owner")
        self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)
        before = self.fixture.store.path.read_bytes()
        with self.assertRaises(FileExistsError):
            plan_download_one(self.fixture.batch, self.fixture.run_dir, 1)
        self.assertEqual(self.fixture.store.path.read_bytes(), before)

    def test_flush_failure_preserves_evidence_and_resume_without_new_attempt(self):
        with self.simulated_windows_fallback() as platform:
            platform.fsync.side_effect = OSError("synthetic flush failure")
            with self.assertRaisesRegex(OSError, "synthetic flush failure"):
                self.execute()
            platform.rename.assert_not_called()
        _, receipt, staged = self.pending()
        self.assertFalse(self.fixture.canonical_path.exists())
        self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)
        with self.simulated_windows_fallback():
            self.assert_idempotent_resume()

    def test_publication_errors_do_not_delete_or_complete_evidence(self):
        for code in (17, 32, 50, None):
            with self.subTest(winerror=code), self.simulated_windows_fallback() as platform:
                error = OSError("synthetic unsupported/cross-volume/sharing failure") if code else RuntimeError("synthetic publication error")
                if code:
                    error.winerror = code
                platform.rename.side_effect = error
                with self.assertRaises(type(error)):
                    self.execute()
                _, receipt, staged = self.pending()
                self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)
                self.assertFalse(self.fixture.canonical_path.exists())
        self.assertEqual(len(self.calls), 1)

    def test_interrupt_after_rename_recovers_canonical_only(self):
        with authoritative_cancellation_scope() as cancellation, self.simulated_windows_fallback() as platform:
            def rename_then_interrupt(source, target):
                self.exclusive_simulated_rename(source, target)
                raise cancellation.request()
            platform.rename.side_effect = rename_then_interrupt
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        _, receipt, staged = self.pending()
        self.assertFalse(staged.exists())
        self.assertTrue(staged.parent.exists())  # Cancellation retains staging directory.
        self.assertEqual(downloads.validate_image_file(self.fixture.canonical_path).sha256, receipt.sha256)
        self.assert_idempotent_resume()

    def test_unexpected_error_after_successful_rename_is_recoverable(self):
        with self.simulated_windows_fallback() as platform:
            def rename_then_error(source, target):
                self.exclusive_simulated_rename(source, target)
                raise RuntimeError("synthetic lost acknowledgement")
            platform.rename.side_effect = rename_then_error
            with self.assertRaisesRegex(RuntimeError, "lost acknowledgement"):
                self.execute()
        self.assertFalse(self.pending()[2].exists())
        self.assert_idempotent_resume()


@unittest.skipUnless(os.name == "nt", "Native Windows installation semantics")
class NativeWindowsInstallationFallbackTests(_InstallationFixture):
    def test_native_hardlink_success_keeps_staging_and_skips_fallback(self):
        staged, canonical = self.fixture.staging_path, self.fixture.canonical_path
        with patch.object(downloads.os, "rename") as rename, patch.object(downloads.os, "fsync") as flush:
            downloads.commit_staged_file(staged, canonical, overwrite=False)
        self.assertTrue(os.path.samefile(staged, canonical))
        self.assertEqual(staged.stat().st_nlink, 2)
        rename.assert_not_called()
        flush.assert_not_called()

    def test_native_rename_fallback_finishes_and_cleans_consumed_staging(self):
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic hard-link unavailable")):
            self.assert_complete(self.execute())
        self.assertFalse(self.calls[0].parent.exists())
        self.assert_idempotent_resume()

    def test_native_readonly_browser_download_retains_ordinary_success(self):
        self.readonly = True  # Existing runner normalizes sole staging ownership before commit.
        self.test_native_rename_fallback_finishes_and_cleans_consumed_staging()

    def test_native_readonly_staging_fails_flush_without_permission_changes(self):
        staged, canonical = self.fixture.staging_path, self.fixture.canonical_path
        staged.chmod(stat.S_IREAD)
        try:
            before = staged.stat().st_mode, staged.stat().st_file_attributes, staged.read_bytes()
            with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")), patch.object(Path, "chmod") as chmod:
                with self.assertRaises(PermissionError):
                    downloads.commit_staged_file(staged, canonical, overwrite=False)
                chmod.assert_not_called()
            self.assertFalse(canonical.exists())
            self.assertEqual((staged.stat().st_mode, staged.stat().st_file_attributes, staged.read_bytes()), before)
        finally:
            staged.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_native_readonly_pending_staging_remains_protected_until_writable(self):
        state = self.fixture.state
        state.items[0].download_status = "running"
        state.items[0].download_attempt_count = self.expected.download_attempt_count
        state.items[0].candidates[0].download_status = "running"
        state.items[0].candidates[0].pending_download_commit = self.expected
        self.fixture.store.save(state)
        staged = self.fixture.staging_path
        staged.chmod(stat.S_IREAD)
        try:
            before = self.fixture.store.path.read_bytes(), staged.read_bytes(), staged.stat().st_file_attributes
            with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")):
                with self.assertRaises(PermissionError):
                    self.execute()
            self.assertEqual((self.fixture.store.path.read_bytes(), staged.read_bytes(), staged.stat().st_file_attributes), before)
            self.assertFalse(self.fixture.canonical_path.exists())
            self.assertEqual(self.calls, [])
        finally:
            staged.chmod(stat.S_IREAD | stat.S_IWRITE)  # Restore this synthetic fixture only.
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")):
            final, item = self.execute()
        self.assertEqual(item.download_status, "completed")
        self.assertEqual(item.download_attempt_count, self.expected.download_attempt_count)
        self.assertEqual(self.fixture.store.load(), final)
        self.assertEqual(self.calls, [])

    def test_native_shared_readonly_staging_never_changes_other_link_attributes(self):
        staged, canonical = self.fixture.staging_path, self.fixture.canonical_path
        other = self.fixture.run_dir / "synthetic-unrelated-hardlink.jpeg"
        os.link(staged, other)
        staged.chmod(stat.S_IREAD)
        try:
            before = other.read_bytes(), other.stat().st_mode, other.stat().st_file_attributes
            with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")), self.assertRaises(PermissionError):
                downloads.commit_staged_file(staged, canonical, overwrite=False)
            self.assertEqual((other.read_bytes(), other.stat().st_mode, other.stat().st_file_attributes), before)
            self.assertFalse(canonical.exists())
            self.assertTrue(staged.exists())
        finally:
            staged.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_native_interrupt_after_rename_recovers_without_staged_filename(self):
        real_rename = os.rename
        with authoritative_cancellation_scope() as cancellation:
            def rename_then_interrupt(source, target):
                before = source.stat()
                real_rename(source, target)
                after = target.stat()
                self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
                raise cancellation.request()
            with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")), patch.object(downloads.os, "rename", side_effect=rename_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.execute()
        self.assertFalse(self.pending()[2].exists())
        self.assertEqual(cancellation.interrupt_signal_count, 1)
        self.assert_idempotent_resume()

    def test_native_late_destination_is_preserved(self):
        real_rename = os.rename
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")), patch.object(downloads.os, "rename") as rename:
            def late_collision(source, target):
                target.write_bytes(b"synthetic native competing owner")
                real_rename(source, target)
            rename.side_effect = late_collision
            with self.assertRaises(FileExistsError):
                self.execute()
        _, receipt, staged = self.pending()
        self.assertEqual(self.fixture.canonical_path.read_bytes(), b"synthetic native competing owner")
        self.assertEqual(downloads.validate_image_file(staged).sha256, receipt.sha256)

    def test_native_delete_sharing_violation_preserves_pending_staging(self):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        create.restype = ctypes.c_void_p
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        real_rename = os.rename
        def locked_rename(source, target):
            handle = create(str(source), 0x80000000, 3, None, 3, 128, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            try:
                real_rename(source, target)
            finally:
                self.assertTrue(close(handle))
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")), patch.object(downloads.os, "rename", side_effect=locked_rename):
            with self.assertRaises(OSError) as caught:
                self.execute()
        self.assertEqual(caught.exception.winerror, 32)
        self.assertTrue(self.pending()[2].exists())
        with patch.object(downloads.os, "link", side_effect=OSError("synthetic unavailable")):
            self.assert_idempotent_resume()


if __name__ == "__main__":
    unittest.main()
