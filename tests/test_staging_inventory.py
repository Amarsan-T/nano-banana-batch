"""Read-only inventory contracts. All runs and image bytes are synthetic."""

import copy
import importlib
import importlib.util
import io
import json
import os
import stat
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from nano_batch.models import CandidateRecord
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


CANARY = "private-inventory-canary"


class StagingInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = make_pending_download_fixture(self.root / "selected-run")

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("nano_batch.staging_inventory"),
                             "The isolated staging inventory is not implemented yet.")
        return importlib.import_module("nano_batch.staging_inventory")

    def snapshot(self):
        result = {}
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in list(dirs) + files:
                path = Path(directory) / name
                info = path.lstat()
                redirected = stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0) & 0x400)
                key = path.relative_to(self.root).as_posix()
                result[key] = (info.st_mode, getattr(info, "st_file_attributes", 0), info.st_nlink,
                               info.st_size if not stat.S_ISDIR(info.st_mode) else None,
                               info.st_mtime_ns, None if redirected or not path.is_file()
                               else path.read_bytes())
                if redirected and name in dirs:
                    dirs.remove(name)
        return result

    def inspect(self, run=None):
        module = self.module()
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with ExitStack() as guards, redirect_stdout(stdout), redirect_stderr(stderr):
            for target in (
                "nano_batch.state.RunStateStore.save", "nano_batch.runner.execute_download_one",
                "nano_batch.runner._reconcile_pending_downloads", "nano_batch.runner.commit_staged_file",
                "nano_batch.runner._cleanup_download_staging", "app._flow_session_from_args",
                "app._load_config", "app.load_batch", "PIL.Image.open", "socket.socket",
                "pathlib.Path.mkdir", "pathlib.Path.unlink", "pathlib.Path.rmdir",
                "pathlib.Path.chmod", "pathlib.Path.write_text", "pathlib.Path.write_bytes",
                "os.unlink", "os.rmdir", "os.mkdir", "os.chmod", "os.rename", "os.replace", "os.link",
                "os.utime", "shutil.rmtree", "subprocess.Popen",
                "nano_batch.runner._download_staging_directory",
            ):
                guards.enter_context(patch(target, side_effect=AssertionError("Inventory must not mutate or attach")))
            try:
                report = module.inspect_staging(run or self.fixture.run_dir)
            finally:
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        encoded = json.dumps(report)
        for private in (CANARY, str(self.root), self.fixture.pending.sha256 if self.fixture.pending else "never-a-hash",
                        "synthetic source", "synthetic.jpeg", "https://", "Traceback"):
            self.assertNotIn(private, encoded)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["cleanup_authorized"])
        self.assertEqual(report["recovery_eligibility"], "not_evaluated")
        return report

    def entry(self, report):
        self.assertEqual(len(report["entries"]), 1)
        return report["entries"][0]

    def edit_state(self, mutate):
        raw = json.loads(self.fixture.store.path.read_text(encoding="utf-8"))
        mutate(raw)
        self.fixture.store.path.write_text(json.dumps(raw), encoding="utf-8")

    def without_receipt(self):
        self.fixture.candidate.pending_download_commit = None
        self.fixture.store.save(self.fixture.state)

    def completed(self):
        f = self.fixture
        os.link(f.staging_path, f.canonical_path)
        candidate = f.candidate
        candidate.pending_download_commit = None
        candidate.download_status = "completed"
        candidate.suggested_filename = f.canonical_path.name
        candidate.local_path = str(f.canonical_path)
        candidate.local_relative_path = f.canonical_path.relative_to(f.run_dir).as_posix()
        candidate.downloaded_at = "synthetic"
        for key in ("sha256", "byte_size", "width", "height", "image_format", "mime_type"):
            setattr(candidate, key, getattr(self.expected, key))
        f.item.output_files = [str(f.canonical_path)]
        f.item.download_status = "completed"
        f.item.download_completed_at = "synthetic"
        f.store.save(f.state)

    def test_pending_staging_reports_reference_and_potential_need_without_eligibility(self):
        entry = self.entry(self.inspect())
        self.assertEqual(entry["labels"], ["A", "B"])
        self.assertEqual(entry["state_consistency"], "consistent")
        self.assertEqual(entry["owners"], [{"order": 1, "variant": 1}])
        self.assertEqual(entry["files_observed"], 1)
        self.assertFalse(entry["canonical_metadata_present"])

    def test_matching_size_is_not_an_integrity_validation(self):
        self.fixture.staging_path.write_bytes(b"x" * self.fixture.pending.byte_size)
        report = self.inspect()
        self.assertEqual(report["content_integrity"], "not_checked")
        self.assertIn("B", self.entry(report)["labels"])

    def test_canonical_only_rename_keeps_missing_staging_distinct(self):
        self.fixture.staging_path.rename(self.fixture.canonical_path)
        entry = self.entry(self.inspect())
        self.assertEqual(entry["files_observed"], 0)
        self.assertTrue(entry["canonical_metadata_present"])
        self.assertIn("staging_file_missing", entry["issues"])
        self.assertNotIn("D", entry["labels"])

    def test_missing_referenced_directory_is_reported_without_allocation(self):
        self.fixture.staging_path.rename(self.fixture.canonical_path)
        self.fixture.staging_path.parent.rmdir()
        entry = self.entry(self.inspect())
        self.assertEqual(entry["status"], "missing")
        self.assertIn("A", entry["labels"])

    def test_completed_leftover_hardlink_has_metadata_association_only(self):
        self.expected = self.fixture.pending
        self.completed()
        entry = self.entry(self.inspect())
        self.assertEqual(entry["labels"], ["D"])
        self.assertEqual(entry["canonical_links_observed"], 1)
        self.assertEqual(entry["shared_files_observed"], 1)
        self.assertTrue(entry["canonical_metadata_present"])

    def test_identical_independent_bytes_do_not_establish_completed_association(self):
        self.expected = self.fixture.pending
        self.completed()
        data = self.fixture.staging_path.read_bytes()
        self.fixture.staging_path.unlink()
        self.fixture.staging_path.write_bytes(data)
        self.assertEqual(self.entry(self.inspect())["labels"], ["E"])

    def test_pending_hardlink_reports_relationship_without_opening_media(self):
        os.link(self.fixture.staging_path, self.fixture.canonical_path)
        entry = self.entry(self.inspect())
        self.assertEqual(entry["canonical_links_observed"], 1)
        self.assertEqual(entry["labels"], ["A", "B"])

    def test_unknown_shared_file_is_ambiguous(self):
        self.without_receipt()
        os.link(self.fixture.staging_path, self.root / "unrelated-synthetic.jpeg")
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_duplicate_uuid_ownership_is_disputed(self):
        self.fixture.state.items[1].candidates = [CandidateRecord(
            variant=1, suggested_filename="synthetic.png", source_kind="test",
            candidate_id=self.fixture.candidate.candidate_id)]
        self.fixture.store.save(self.fixture.state)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_order_output_claim_to_staging_is_disputed(self):
        self.fixture.state.items[1].output_files = [str(self.fixture.staging_path)]
        self.fixture.store.save(self.fixture.state)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_receiptless_staging_claimed_by_an_order_is_not_reported_as_orphaned(self):
        self.without_receipt()
        self.fixture.state.items[1].output_files = [str(self.fixture.staging_path)]
        self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.entry(self.inspect())["labels"], ["C"])

    def test_receiptless_candidate_staging_claim_is_not_reported_as_orphaned(self):
        self.without_receipt()
        self.fixture.candidate.local_relative_path = self.fixture.staging_path.relative_to(self.fixture.run_dir).as_posix()
        self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.entry(self.inspect())["labels"], ["C"])

    def test_competing_pending_claim_and_alternate_extension_are_disputed(self):
        original = asdict(self.fixture.pending)
        def conflict(raw):
            neighbor = raw["items"][1]
            candidate = copy.deepcopy(raw["items"][0]["candidates"][0])
            candidate["candidate_id"] = str(__import__("uuid").UUID(int=202))
            candidate["pending_download_commit"] = dict(original, candidate_id=candidate["candidate_id"])
            neighbor["candidates"] = [candidate]
        self.edit_state(conflict)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_malformed_pending_is_not_silently_treated_as_no_receipt(self):
        self.edit_state(lambda raw: raw["items"][0]["candidates"][0].update(
            pending_download_commit={"private": CANARY}))
        report = self.inspect()
        self.assertEqual(report["state_status"], "inconsistent")
        self.assertIn("C", self.entry(report)["labels"])

    def test_wrong_project_attempt_or_pending_path_fails_metadata_consistency(self):
        original = self.fixture.store.path.read_bytes()
        for field, value in (("project_id", None), ("generation_attempt_count", 99),
                             ("download_attempt_count", 99),
                             ("staging_relative_path", "../" + CANARY)):
            with self.subTest(field=field):
                self.fixture.store.path.write_bytes(original)
                self.edit_state(lambda raw: raw["items"][0]["candidates"][0][
                    "pending_download_commit"].update({field: value}))
                self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_legacy_projectless_receipt_does_not_gain_recovery_eligibility(self):
        def legacy(raw):
            raw["flow_project_required"], raw["flow_project"] = False, None
            raw["items"][0]["candidates"][0]["pending_download_commit"]["project_id"] = None
        self.edit_state(legacy)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_receiptless_image_partial_and_empty_directory_remain_unproven(self):
        self.without_receipt()
        self.assertEqual(self.entry(self.inspect())["labels"], ["E"])
        self.fixture.staging_path.unlink()
        partial = self.fixture.staging_path.parent / (CANARY + ".crdownload")
        partial.write_bytes(b"synthetic partial bytes")
        self.assertEqual(self.entry(self.inspect())["partial_downloads_observed"], 1)
        partial.unlink()
        self.assertEqual(self.entry(self.inspect())["labels"], ["E"])

    def test_missing_state_keeps_inventory_and_uncertainty(self):
        self.fixture.store.path.unlink()
        report = self.inspect()
        self.assertEqual(report["state_status"], "missing")
        self.assertIn("E", self.entry(report)["labels"])
        self.assertIn("state_unavailable", self.entry(report)["issues"])

    def test_corrupt_duplicate_key_and_deep_state_are_bounded_and_private(self):
        for payload in (CANARY.encode(), b'{"items":[],"items":[]}',
                        ("[" * 100 + '"' + CANARY + '"' + "]" * 100).encode()):
            with self.subTest(kind=payload[:1]):
                self.fixture.store.path.write_bytes(payload)
                self.assertEqual(self.inspect()["state_status"], "malformed")

    def test_oversized_state_is_not_read(self):
        module = self.module()
        with patch.object(module, "MAX_STATE_BYTES", 64), patch.object(
            module, "_state_metadata", side_effect=AssertionError("oversized state must not parse")
        ) as parse:
            self.assertEqual(self.inspect()["state_status"], "oversized")
        parse.assert_not_called()

    def test_state_item_limit_does_not_drop_unknown_claims_silently(self):
        module = self.module()
        with patch.object(module, "MAX_STATE_ITEMS", 1):
            report = self.inspect()
        self.assertTrue(report["truncated"])
        self.assertNotEqual(self.entry(report)["state_consistency"], "consistent")

    def test_readonly_files_are_not_chmodded(self):
        staged = self.fixture.staging_path
        staged.chmod(stat.S_IREAD)
        self.addCleanup(staged.chmod, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(self.entry(self.inspect())["labels"], ["A", "B"])

    def directory_link(self, link, target):
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)

    def test_redirected_assets_are_not_enumerated(self):
        assets = self.fixture.run_dir / "assets"
        saved = self.fixture.run_dir / "saved-assets"
        assets.rename(saved)
        self.directory_link(assets, saved)
        self.assertEqual(self.inspect()["assets_status"], "unsafe")

    def test_staging_junction_is_reported_without_descending(self):
        stage = self.fixture.staging_path.parent
        target = self.root / CANARY
        stage.rename(target)
        self.directory_link(stage, target)
        self.assertEqual(self.entry(self.inspect())["status"], "unsafe")

    def test_windows_case_alias_is_not_counted_as_two_directories(self):
        if os.name == "nt":
            stage = self.fixture.staging_path.parent
            stage.rename(stage.with_name(".staging-SYNTHETIC"))
            entry = self.entry(self.inspect())
            self.assertEqual(entry["status"], "unsafe")
            self.assertIn("A", entry["labels"])

    def test_root_junction_and_traversal_are_rejected(self):
        alias = self.root / "alias"
        self.directory_link(alias, self.fixture.run_dir)
        self.assertEqual(self.inspect(alias)["run_status"], "unsafe")
        self.assertEqual(self.inspect(self.fixture.run_dir / ".." / "selected-run")["run_status"], "unsafe")

    def test_missing_root_does_not_create_it(self):
        self.assertEqual(self.inspect(self.root / "missing")["run_status"], "missing")

    def test_unexpected_nested_directory_is_not_crawled(self):
        nested = self.fixture.staging_path.parent / CANARY
        nested.mkdir()
        (nested / "private.txt").write_text(CANARY)
        entry = self.entry(self.inspect())
        self.assertIn("nested_directory_skipped", entry["issues"])
        self.assertIn("C", entry["labels"])

    def test_unknown_reparse_metadata_is_rejected(self):
        module = self.module()
        real = module._stat_entry
        def reparse(directory, name):
            info = real(directory, name)
            if name == "v01.download":
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(module, "_stat_entry", side_effect=reparse):
            self.assertIn("unsafe_entry", self.entry(self.inspect())["issues"])

    def test_directory_enumeration_limit_is_explicit(self):
        module = self.module()
        for number in range(4):
            (self.fixture.run_dir / "assets" / (".staging-extra-" + str(number))).mkdir()
        with patch.object(module, "MAX_ASSET_ENTRIES", 2), patch.object(module, "MAX_STAGING_ENTRIES", 2):
            report = self.inspect()
        self.assertTrue(report["truncated"])
        self.assertLessEqual(len(report["entries"]), 2)

    def test_file_enumeration_limit_and_no_recursion_are_explicit(self):
        module = self.module()
        for number in range(4):
            (self.fixture.staging_path.parent / (CANARY + str(number))).write_bytes(b"synthetic")
        with patch.object(module, "MAX_FILES_PER_STAGING", 2):
            report = self.inspect()
        self.assertTrue(report["truncated"])
        self.assertEqual(self.entry(report)["files_observed"], 2)

    def test_permission_error_is_fixed_and_does_not_escape(self):
        module = self.module()
        with patch.object(module, "_entry_names", side_effect=PermissionError(CANARY)):
            report = self.inspect()
        self.assertEqual(report["assets_status"], "unreadable")

    def test_file_acl_error_is_reported_without_raw_message(self):
        module = self.module()
        real = module._stat_entry
        def denied(directory, name):
            if name == "v01.download":
                raise PermissionError(CANARY)
            return real(directory, name)
        with patch.object(module, "_stat_entry", side_effect=denied):
            self.assertIn("entry_unreadable", self.entry(self.inspect())["issues"])

    def test_changed_file_metadata_is_reported(self):
        module = self.module()
        real = module._stat_entry
        calls = 0
        def changing(directory, name):
            nonlocal calls
            info = real(directory, name)
            if name == "v01.download":
                calls += 1
                if calls > 1:
                    values = {key: getattr(info, key) for key in (
                        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
                    values["st_mtime_ns"] += 1
                    values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
                    return SimpleNamespace(**values)
            return info
        with patch.object(module, "_stat_entry", side_effect=changing):
            self.assertIn("entry_changed", self.entry(self.inspect())["issues"])

    def test_changed_state_metadata_is_reported(self):
        module = self.module()
        real = module._stat_entry
        calls = 0
        def changing(directory, name):
            nonlocal calls
            info = real(directory, name)
            if name == "run_state.json":
                calls += 1
                if calls > 1:
                    values = {key: getattr(info, key) for key in (
                        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
                    values["st_size"] += 1
                    values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
                    return SimpleNamespace(**values)
            return info
        with patch.object(module, "_stat_entry", side_effect=changing):
            self.assertEqual(self.inspect()["state_status"], "changed")

    def test_path_claim_escapes_are_not_inspected(self):
        self.fixture.state.items[1].output_files = [str(self.root / CANARY), "../" + CANARY]
        self.fixture.store.save(self.fixture.state)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_partial_canonical_metadata_is_flagged_without_reading_bytes(self):
        self.fixture.canonical_path.write_bytes(b"synthetic partial")
        entry = self.entry(self.inspect())
        self.assertIn("canonical_size_mismatch", entry["issues"])
        self.assertIn("C", entry["labels"])

    def test_malformed_completed_metadata_is_not_treated_as_consistent(self):
        self.expected = self.fixture.pending
        self.completed()
        self.edit_state(lambda raw: raw["items"][0]["candidates"][0].update(
            sha256=CANARY, byte_size=True, width="unknown", mime_type="wrong"))
        self.assertEqual(self.inspect()["state_status"], "inconsistent")
        self.edit_state(lambda raw: raw["items"][0]["candidates"][0].update(image_format="unknown"))
        self.assertEqual(self.inspect()["state_status"], "inconsistent")

    def test_disappearing_staged_entry_is_distinct_from_permission_denial(self):
        module = self.module()
        real = module._stat_entry
        def missing(directory, name):
            if name == "v01.download":
                raise FileNotFoundError(CANARY)
            return real(directory, name)
        with patch.object(module, "_stat_entry", side_effect=missing):
            self.assertIn("entry_missing", self.entry(self.inspect())["issues"])

    def test_canonical_disappearing_at_final_check_reports_changed_assets(self):
        os.link(self.fixture.staging_path, self.fixture.canonical_path)
        module = self.module()
        real = module._stat_entry
        calls = 0
        def missing(directory, name):
            nonlocal calls
            if name == self.fixture.canonical_path.name:
                calls += 1
                if calls > 1:
                    raise FileNotFoundError(CANARY)
            return real(directory, name)
        with patch.object(module, "_stat_entry", side_effect=missing):
            self.assertEqual(self.inspect()["assets_status"], "changed")

    def test_state_hardlink_is_not_opened_as_json(self):
        original = self.fixture.store.path
        os.link(original, self.root / "synthetic-state-alias.json")
        self.assertEqual(self.inspect()["state_status"], "unsafe")

    def test_state_handle_identity_must_match_before_json_parsing(self):
        module = self.module()
        real = module._stat_entry
        def different_identity(directory, name):
            info = real(directory, name)
            if name == "run_state.json":
                values = {key: getattr(info, key) for key in (
                    "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
                values["st_ino"] += 1
                values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
                return SimpleNamespace(**values)
            return info
        with patch.object(module, "_stat_entry", side_effect=different_identity), patch.object(
            module, "_json_object", side_effect=AssertionError("replaced state must not be parsed")
        ) as parse:
            self.assertEqual(self.inspect()["state_status"], "changed")
        parse.assert_not_called()

    def test_state_permission_denial_is_fixed_and_inventory_continues(self):
        module = self.module()
        with patch.object(module, "_read_state_bytes", side_effect=PermissionError(CANARY)):
            report = self.inspect()
        self.assertEqual(report["state_status"], "unreadable")
        self.assertIn("E", self.entry(report)["labels"])

    def test_alternate_extension_candidate_claim_is_ambiguous(self):
        self.fixture.state.items[1].output_files = [str(self.fixture.canonical_path.with_suffix(".png"))]
        self.fixture.store.save(self.fixture.state)
        self.assertIn("C", self.entry(self.inspect())["labels"])

    def test_non_directory_staging_name_is_not_opened(self):
        stage = self.fixture.staging_path.parent
        self.fixture.staging_path.unlink()
        stage.rmdir()
        stage.write_text(CANARY)
        self.assertEqual(self.entry(self.inspect())["status"], "unsafe")

    def test_canonical_metadata_budget_is_explicit(self):
        module = self.module()
        with patch.object(module, "MAX_CANONICAL_ENTRIES", 0):
            report = self.inspect()
        self.assertTrue(report["truncated"])
        self.assertEqual(report["inspection_status"], "incomplete")

    def test_directory_handle_sharing_denial_is_private(self):
        module = self.module()
        with patch.object(module, "_directory", side_effect=PermissionError(CANARY)):
            self.assertEqual(self.inspect()["run_status"], "unreadable")

    def test_network_and_device_root_aliases_are_rejected_before_open(self):
        module = self.module()
        if os.name == "nt":
            with patch.object(module, "_directory", side_effect=AssertionError("unsafe root must not open")) as open_dir:
                for root in (Path("\\\\example.invalid\\" + CANARY), Path("\\\\?\\C:\\" + CANARY), Path("C:" + CANARY)):
                    self.assertEqual(self.inspect(root)["run_status"], "unsafe")
            open_dir.assert_not_called()

    def test_mapped_network_drive_is_rejected_before_directory_access(self):
        module = self.module()
        if os.name == "nt":
            with patch.object(module, "_local_windows_drive", return_value=False), patch.object(
                module, "_directory", side_effect=AssertionError("network directory must not open")
            ) as open_dir:
                self.assertEqual(self.inspect()["run_status"], "unsafe")
            open_dir.assert_not_called()

    def test_native_windows_guard_prevents_staging_directory_replacement(self):
        module = self.module()
        if os.name == "nt":
            real = module._entry_names
            external_rename = os.rename  # Bypass program-mutation mocks only for this competing-actor probe.
            attempts = []
            def attempt_swap(directory, limit):
                if directory.path == self.fixture.staging_path.parent:
                    with self.assertRaises(PermissionError):
                        external_rename(directory.path, self.fixture.run_dir / "assets" / ".staging-moved")
                    attempts.append(True)
                return real(directory, limit)
            with patch.object(module, "_entry_names", side_effect=attempt_swap):
                self.assertIn("A", self.entry(self.inspect())["labels"])
            self.assertEqual(attempts, [True])

    def test_state_file_symlink_is_not_read(self):
        original = self.fixture.store.path
        target = self.root / "synthetic-target.json"
        original.rename(target)
        try:
            original.symlink_to(target)
        except OSError:
            # File-symlink privileges are not assumed on Windows. Simulate only
            # the final entry's reparse metadata; directory junction tests above
            # are separate native evidence.
            original.write_text(CANARY)
            module = self.module()
            real = module._stat_entry
            def reparse(directory, name):
                info = real(directory, name)
                if name == "run_state.json":
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with patch.object(module, "_stat_entry", side_effect=reparse):
                self.assertEqual(self.inspect()["state_status"], "unsafe")
        else:
            self.assertEqual(self.inspect()["state_status"], "unsafe")

    def test_prompts_urls_filenames_and_errors_do_not_enter_structured_output(self):
        def private(raw):
            raw["items"][0]["prompt"] = CANARY
            raw["items"][0]["source_prompt"] = CANARY
            raw["items"][0]["result_details"] = CANARY
            candidate = raw["items"][0]["candidates"][0]
            candidate["asset_url"] = "https://example.invalid/" + CANARY + "?token=" + CANARY
            candidate["accessible_name"] = CANARY
            candidate["pending_download_commit"]["browser_suggested_filename"] = CANARY
        self.edit_state(private)
        (self.fixture.staging_path.parent / CANARY).write_text(CANARY)
        report = self.inspect()
        self.assertIn("A", self.entry(report)["labels"])

    def test_cli_help_and_inventory_do_not_initialize_browser_or_config(self):
        self.module()
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("app.FlowSession", side_effect=AssertionError("no browser")), patch(
            "app._load_config", side_effect=AssertionError("no private config")
        ), patch("sys.argv", ["app.py", "inspect-staging", "--run-dir", str(self.fixture.run_dir)]), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(app.main(), 0)
        self.assertNotIn(str(self.root), stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["entries"][0]["labels"], ["A", "B"])
        with patch("app.FlowSession", side_effect=AssertionError("no browser")), patch(
            "sys.argv", ["app.py", "inspect-staging", "--help"]
        ), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_status:
            app.main()
        self.assertEqual(exit_status.exception.code, 0)
        self.assertEqual(self.snapshot(), before)

    def test_cli_requires_explicit_root_and_redacts_argument_errors(self):
        self.module()
        before = self.snapshot()
        for arguments in ([], ["--run-dir"], ["--run-dir", str(self.fixture.run_dir), "--" + CANARY]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sys.argv", ["app.py", "inspect-staging", *arguments]), redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_status:
                app.main()
            self.assertEqual(exit_status.exception.code, 2)
            self.assertNotIn(CANARY, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(str(self.root), stdout.getvalue() + stderr.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_cli_unexpected_failure_is_redacted_without_traceback(self):
        module = self.module()
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(module, "inspect_staging", side_effect=RuntimeError(CANARY)), patch(
            "sys.argv", ["app.py", "inspect-staging", "--run-dir", str(self.fixture.run_dir)]
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(app.main(), 2)
        self.assertNotIn(CANARY, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
