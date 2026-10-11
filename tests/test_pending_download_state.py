"""Supporting record/codec tests; these should pass before the owner validator exists."""

import copy
import json
import os
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from unittest.mock import patch

from nano_batch.models import PendingDownloadCommit
from nano_batch.state import RunStateStore, StatePersistenceError
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class PendingDownloadStateTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_pending_download_fixture(Path(temporary.name) / "run")

    def raw(self):
        return json.loads(self.fixture.store.path.read_text(encoding="utf-8"))

    def write_raw(self, raw):
        self.fixture.store.path.write_text(json.dumps(raw), encoding="utf-8")

    def legacy_document(self, version=4):
        raw = self.raw()
        raw["version"] = version
        raw["items"][0]["candidates"][0].pop("pending_download_commit", None)
        return raw

    def test_record_round_trip_is_typed_and_preserves_every_field(self):
        loaded = self.fixture.store.load()
        self.assertEqual(asdict(loaded), asdict(self.fixture.state))
        self.assertIsInstance(loaded.items[0].candidates[0].pending_download_commit, PendingDownloadCommit)
        self.assertEqual(self.raw()["version"], 6)
        self.assertEqual(loaded.items[0].candidates[0].download_status, "running")
        self.assertEqual(loaded.items[0].output_files, [])

    def test_record_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.fixture.pending.order = 2

    def test_legacy_versions_without_pending_records_still_load(self):
        for version in (1, 2, 3, 4, 5):
            with self.subTest(version=version):
                self.write_raw(self.legacy_document(version))
                loaded = self.fixture.store.load()
                self.assertEqual(loaded.version, version)
                self.assertIsNone(loaded.items[0].candidates[0].pending_download_commit)

    def test_explicit_null_loads_and_is_omitted_on_save(self):
        raw = self.legacy_document(5)
        raw["items"][0]["candidates"][0]["pending_download_commit"] = None
        self.write_raw(raw)
        loaded = self.fixture.store.load()
        self.fixture.store.save(loaded)
        self.assertEqual(loaded.version, 5)
        self.assertNotIn("pending_download_commit", self.raw()["items"][0]["candidates"][0])

    def test_saving_legacy_candidates_does_not_migrate_or_add_fields(self):
        raw = self.legacy_document(4)
        self.write_raw(raw)
        loaded = self.fixture.store.load()
        self.fixture.store.save(loaded)
        self.assertEqual(self.raw(), raw)
        self.assertEqual(loaded.version, 4)

    def test_migration_and_pending_record_use_one_atomic_replacement(self):
        self.write_raw(self.legacy_document(4))
        self.fixture.state.version = 4
        real_replace = os.replace
        replacements = []

        def inspect_replace(source, target):
            document = json.loads(Path(source).read_text(encoding="utf-8"))
            self.assertEqual(document["version"], 6)
            self.assertEqual(document["items"][0]["candidates"][0]["pending_download_commit"], asdict(self.fixture.pending))
            self.assertEqual(self.fixture.state.version, 4)
            replacements.append((source, target))
            return real_replace(source, target)

        with patch("nano_batch.state.os.replace", new=inspect_replace):
            self.fixture.store.save(self.fixture.state)
        self.assertEqual(len(replacements), 1)
        self.assertEqual(self.fixture.state.version, 6)
        self.assertFalse(self.fixture.canonical_path.exists())

    def test_failed_migration_preserves_disk_state_version_and_assets(self):
        self.write_raw(self.legacy_document(4))
        self.fixture.state.version = 4
        before = self.fixture.store.path.read_bytes()
        staged = self.fixture.staging_path.read_bytes()
        with patch.object(RunStateStore, "_replace_atomically", side_effect=StatePersistenceError("synthetic failure")):
            with self.assertRaises(StatePersistenceError):
                self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.fixture.store.path.read_bytes(), before)
        self.assertEqual(self.fixture.state.version, 4)
        self.assertEqual(self.fixture.staging_path.read_bytes(), staged)
        self.assertFalse(self.fixture.canonical_path.exists())
        self.assertEqual(list(self.fixture.run_dir.glob("run_state.json.*.tmp")), [])

    def test_clearing_record_keeps_version_six_and_omits_the_field(self):
        self.fixture.candidate.pending_download_commit = None
        self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.raw()["version"], 6)
        self.assertNotIn("pending_download_commit", self.raw()["items"][0]["candidates"][0])
        self.assertEqual(self.fixture.store.load().items[0].download_status, "running")

    def test_candidates_without_records_are_omitted_in_mixed_state(self):
        neighbor = copy.deepcopy(self.fixture.candidate)
        neighbor.pending_download_commit = None
        self.fixture.state.items[1].candidates = [neighbor]
        self.fixture.store.save(self.fixture.state)
        self.assertNotIn("pending_download_commit", self.raw()["items"][1]["candidates"][0])

    def test_null_project_is_representable_but_does_not_establish_ownership(self):
        self.fixture.replace_pending(project_id=None)
        self.fixture.state.flow_project = None
        self.fixture.state.flow_project_required = False
        legacy_store = RunStateStore(self.fixture.run_dir.parent / "legacy" / "run_state.json")
        legacy_store.save(self.fixture.state)
        self.assertIsNone(legacy_store.load().items[0].candidates[0].pending_download_commit.project_id)

    def test_codec_does_not_substitute_for_contextual_ownership_validation(self):
        self.fixture.replace_pending(batch_id="sha256:different-synthetic-batch", order=2)
        self.fixture.store.save(self.fixture.state)
        loaded = self.fixture.store.load()
        pending = loaded.items[0].candidates[0].pending_download_commit
        self.assertNotEqual(pending.batch_id, loaded.batch_id)
        self.assertNotEqual(pending.order, loaded.items[0].order)

    def test_record_in_an_older_or_noninteger_state_version_is_rejected(self):
        original = self.raw()
        for version in (5, "6", True):
            with self.subTest(version=version):
                raw = copy.deepcopy(original)
                raw["version"] = version
                self.write_raw(raw)
                with self.assertRaises(ValueError):
                    self.fixture.store.load()

    def test_malformed_record_objects_fields_and_versions_are_rejected(self):
        original = self.raw()
        malformed = [[], "invalid", {}, {**asdict(self.fixture.pending), "extra": 1},
                     {**asdict(self.fixture.pending), "record_version": 2}]
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                raw = copy.deepcopy(original)
                raw["items"][0]["candidates"][0]["pending_download_commit"] = value
                self.write_raw(raw)
                with self.assertRaises(ValueError):
                    self.fixture.store.load()

    def test_invalid_field_shapes_fail_before_state_replacement(self):
        original = self.fixture.pending
        changes = (
            {"order": True}, {"variant": 0}, {"generation_attempt_count": 2.0},
            {"download_attempt_count": -1}, {"width": 0}, {"height": False},
            {"byte_size": "630"}, {"sha256": "not-a-digest"}, {"sha256": "A" * 64},
            {"candidate_id": "not-a-uuid"}, {"project_id": 9},
            {"image_format": "JPEG", "mime_type": "image/png"}, {"image_format": "TIFF"},
            {"canonical_relative_path": ""}, {"staging_relative_path": None},
            {"browser_download_guid": 3}, {"record_version": True},
        )
        before = self.fixture.store.path.read_bytes()
        for change in changes:
            with self.subTest(fields=tuple(change)):
                self.fixture.candidate.pending_download_commit = replace(original, **change)
                with self.assertRaises(ValueError):
                    self.fixture.store.save(self.fixture.state)
                self.assertEqual(self.fixture.store.path.read_bytes(), before)

    def test_untyped_pending_mapping_is_not_silently_saved(self):
        before = self.fixture.store.path.read_bytes()
        self.fixture.candidate.pending_download_commit = asdict(self.fixture.pending)
        with self.assertRaises(ValueError):
            self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.fixture.store.path.read_bytes(), before)

    def test_optional_browser_metadata_may_be_absent(self):
        self.fixture.replace_pending(browser_suggested_filename=None, browser_download_guid=None)
        self.fixture.store.save(self.fixture.state)
        self.assertEqual(self.fixture.store.load().items[0].candidates[0].pending_download_commit, self.fixture.pending)


if __name__ == "__main__":
    unittest.main()
