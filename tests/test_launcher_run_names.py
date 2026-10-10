import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image

from nano_batch.batch import load_prompts_file
from nano_batch.launcher import (
    create_named_run_dir, find_matching_runs, run_launcher, validate_run_folder_name,
)
from nano_batch.models import BrowserDownloadResult, DiscoveredCandidate, GenerationResult, GenerationSettings
from nano_batch.runner import execute_sequential_batch, initialize_run_state
from nano_batch.state import RunStateStore


class OfflineAdapter:
    def __init__(self):
        self.events = []

    def generate_one(self, *, item, settings, forbidden_candidate_ids):
        self.events.append(("generate", item.order))
        return GenerationResult(candidates=(DiscoveredCandidate(
            source_kind="rendered-image", candidate_id=str(UUID(int=item.order)),
        ),))

    def download_candidate(self, *, candidate, destination):
        self.events.append(("download", candidate.variant))
        Image.new("RGB", (16, 9), (10, 20, 30)).save(destination, format="JPEG")
        return BrowserDownloadResult(candidate_id=candidate.candidate_id, staged_path=str(destination))


class CustomRunNameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runs = self.root / "runs"
        self.prompts = self.root / "prompts.txt"
        self.prompts.write_text("one\n", encoding="utf-8")
        self.batch = load_prompts_file(self.prompts)

    def launcher(self, *choices, execute=None):
        self.output = []
        self.inputs = []
        values = iter(choices)
        def read(prompt):
            self.inputs.append(prompt)
            return next(values)
        self.execute = execute if execute is not None else MagicMock(return_value=0)
        return run_launcher(
            self.root, self.execute, input_fn=read, output=self.output.append,
            now_fn=lambda: datetime(2026, 10, 8, 12, 34, 56),
        )

    def assert_rejected_without_creation(self, names):
        for name in names:
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    create_named_run_dir(self.runs, name)
                self.assertFalse(self.runs.exists())

    def persisted_run(self, name):
        run_dir = create_named_run_dir(self.runs, name)
        initialize_run_state(self.batch, run_dir)
        return run_dir

    def test_custom_simple_name_is_used_exactly_and_delegated_once(self):
        self.assertEqual(self.launcher("2", "Example Batch", "6"), 0)
        self.execute.assert_called_once_with(self.prompts.resolve(), (self.runs / "Example Batch").resolve(), False)
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Example Batch"])
        self.assertIn("Batch folder name (blank = automatic): ", self.inputs)

    def test_spaces_hyphens_underscores_parentheses_and_unicode_are_preserved(self):
        for name in ("Example Batch - Hobbies", "Video 03", "procrastination_v2", "Research (final)", "Тестовый батч", "café (étude)", " leading space", "two..dots"):
            with self.subTest(name=name):
                path = create_named_run_dir(self.runs, name)
                self.assertEqual(path.name, name)
                self.assertEqual(path.parent, self.runs.resolve())
                self.assertTrue(path.is_dir())

    def test_unicode_name_works_through_launcher(self):
        self.launcher("2", "Тестовый батч", "6")
        self.execute.assert_called_once_with(self.prompts.resolve(), (self.runs / "Тестовый батч").resolve(), False)

    def test_blank_input_preserves_automatic_timestamp_name(self):
        self.launcher("2", "", "6")
        self.execute.assert_called_once_with(self.prompts.resolve(), (self.runs / "batch_20261008_123456").resolve(), False)

    def test_blank_input_preserves_existing_automatic_collision_suffix(self):
        existing = self.runs / "batch_20261008_123456"
        existing.mkdir(parents=True)
        self.launcher("2", "", "6")
        self.execute.assert_called_once_with(self.prompts.resolve(), (self.runs / "batch_20261008_123456_02").resolve(), False)

    def test_duplicate_custom_directory_is_preserved_and_reprompted(self):
        existing = self.persisted_run("Example Batch - Hobbies")
        marker = existing / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        before = (existing / "run_state.json").read_bytes()
        self.launcher("2", "Example Batch - Hobbies", "Different name", "6")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertEqual((existing / "run_state.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.runs.iterdir()), ["Different name", "Example Batch - Hobbies"])
        self.execute.assert_called_once_with(self.prompts.resolve(), (self.runs / "Different name").resolve(), False)
        self.assertTrue(any("already exists" in line and "Example Batch - Hobbies" in line for line in self.output))
        self.assertEqual(self.inputs.count("Batch folder name (blank = automatic): "), 2)

    def test_existing_file_is_not_replaced_by_directory(self):
        self.runs.mkdir()
        existing = self.runs / "Name"
        existing.write_bytes(b"preserve")
        self.launcher("2", "Name", "CANCEL", "6")
        self.execute.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"preserve")
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Name"])

    @unittest.skipUnless(os.name == "nt", "Windows filesystem case-insensitivity")
    def test_case_insensitive_duplicate_is_rejected_on_windows(self):
        create_named_run_dir(self.runs, "Example Batch")
        self.launcher("2", "example batch", "CANCEL", "6")
        self.execute.assert_not_called()
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Example Batch"])

    def test_concurrent_creation_fails_atomically_without_reuse(self):
        self.runs.mkdir()
        real_mkdir = Path.mkdir
        def race(path, *args, **kwargs):
            if path == self.runs / "Name":
                real_mkdir(path)
                (path / "keep.txt").write_bytes(b"other creator")
                raise FileExistsError("synthetic concurrent creation")
            return real_mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", race):
            with self.assertRaises(FileExistsError):
                create_named_run_dir(self.runs, "Name")
        self.assertEqual((self.runs / "Name" / "keep.txt").read_bytes(), b"other creator")
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Name"])

    def test_cancel_is_case_insensitive_and_creates_nothing(self):
        for cancel in ("CANCEL", "cancel", "CaNcEl", " cancel "):
            with self.subTest(cancel=cancel):
                self.assertEqual(self.launcher("2", cancel, "6"), 0)
                self.execute.assert_not_called()
                self.assertFalse(self.runs.exists())
                self.assertEqual(self.output.count("Nano Banana Batch"), 2)

    def test_invalid_names_reprompt_before_any_filesystem_mutation(self):
        choices = iter(("2", "..\\escape", "CON.txt", "bad. ", "CANCEL", "6"))
        execute = MagicMock()
        def read(_prompt):
            self.assertFalse(self.runs.exists())
            return next(choices)
        result = run_launcher(self.root, execute, input_fn=read, output=lambda _: None)
        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertFalse((self.root / "escape").exists())

    def test_invalid_then_valid_name_creates_only_valid_directory(self):
        self.launcher("2", "bad/name", "Valid name", "6")
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Valid name"])
        self.execute.assert_called_once()
        self.assertTrue(any("Invalid batch folder name" in line for line in self.output))

    def test_empty_custom_names_and_dot_components_are_rejected(self):
        self.assert_rejected_without_creation(("", " ", "\t", ".", ".."))

    def test_traversal_slashes_and_backslashes_are_rejected(self):
        self.assert_rejected_without_creation(("../escape", "..\\escape", "a/b", "a\\b", "/", "\\"))

    def test_absolute_and_drive_qualified_paths_are_rejected(self):
        self.assert_rejected_without_creation(("C:\\escape", "C:escape", "C:", "/tmp/escape", "\\\\server\\share", "\\rooted"))

    def test_windows_forbidden_characters_are_rejected(self):
        self.assert_rejected_without_creation(tuple(f"bad{char}name" for char in '<>:"/\\|?*'))

    def test_control_characters_are_rejected(self):
        self.assert_rejected_without_creation(tuple(f"bad{chr(number)}name" for number in (*range(32), *range(127, 160))))

    def test_reserved_device_names_and_extension_forms_are_rejected(self):
        devices = ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
        devices += [f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)]
        devices += [f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "¹²³"]
        self.assert_rejected_without_creation(tuple(name for device in devices for name in (device, device.lower(), device + ".txt", device + " .txt")))

    def test_reserved_prefix_is_not_confused_with_normal_name(self):
        for name in ("Console", "COM10", "LPT10", "NUL-project", "PRN_notes"):
            validate_run_folder_name(name)

    def test_trailing_dot_and_space_are_rejected_without_trimming(self):
        self.assert_rejected_without_creation(("Name.", "Name ", "Name...", "Name. "))

    def test_invalid_unicode_and_overlong_component_are_rejected(self):
        self.assert_rejected_without_creation(("bad\ud800name", "a" * 256, "🍌" * 128))
        validate_run_folder_name("a" * 255)

    def test_resolved_path_must_stay_directly_beneath_runs(self):
        real_resolve = Path.resolve
        def outside(path, *args, **kwargs):
            if path == self.runs / "Name":
                return self.root / "outside" / "Name"
            return real_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", outside):
            with self.assertRaises(ValueError):
                create_named_run_dir(self.runs, "Name")
        self.assertFalse(self.runs.exists())

    def test_legacy_and_custom_run_discovery_is_read_only_and_prefix_independent(self):
        legacy = self.persisted_run("batch_20000101_120000")
        custom = self.persisted_run("Example Batch - Hobbies")
        before = {p: (p / "run_state.json").read_bytes() for p in (legacy, custom)}
        incomplete, completed = find_matching_runs(self.runs, self.batch)
        self.assertEqual({match.run_dir for match in incomplete}, {legacy, custom})
        self.assertEqual(completed, [])
        for path in before:
            self.assertEqual((path / "run_state.json").read_bytes(), before[path])

    def test_unrelated_empty_malformed_invalid_and_nonmatching_states_are_ignored(self):
        self.runs.mkdir()
        for name, content in (("empty", None), ("malformed", "not JSON"), ("missing-fields", "{}")):
            path = self.runs / name
            path.mkdir()
            if content is not None:
                (path / "run_state.json").write_text(content, encoding="utf-8")
        invalid = self.persisted_run("invalid-state")
        raw = json.loads((invalid / "run_state.json").read_text(encoding="utf-8"))
        raw["items"][0]["status"] = "not-a-status"
        (invalid / "run_state.json").write_text(json.dumps(raw), encoding="utf-8")
        other = self.persisted_run("other-batch")
        state = RunStateStore(other / "run_state.json").load()
        state.batch_id = "sha256:other"
        RunStateStore(other / "run_state.json").save(state)
        self.assertEqual(find_matching_runs(self.runs, self.batch), ([], []))

    def test_resume_lists_actual_custom_and_legacy_names(self):
        for name in ("Example Batch - Hobbies", "Example 04 - Topic", "batch_20000101_120000"):
            self.persisted_run(name)
        self.launcher("3", "0", "6")
        self.execute.assert_not_called()
        for name in ("Example Batch - Hobbies", "Example 04 - Topic", "batch_20000101_120000"):
            self.assertTrue(any(name in line for line in self.output))

    def test_legacy_and_custom_runs_both_resume_without_renaming(self):
        for name in ("batch_20000101_120000", "Example Batch - Hobbies"):
            with self.subTest(name=name):
                run_dir = self.persisted_run(name)
                # Isolate each matching Batch ID so Resume selects this exact run.
                other = [path for path in self.runs.iterdir() if path != run_dir]
                for path in other:
                    state = RunStateStore(path / "run_state.json").load()
                    state.batch_id = "sha256:other"
                    RunStateStore(path / "run_state.json").save(state)
                self.launcher("3", "6")
                self.execute.assert_called_once_with(self.prompts.resolve(), run_dir, False)
                self.assertEqual(run_dir.name, name)

    def test_custom_names_do_not_change_semantic_batch_id_or_prompt_identity(self):
        captured = []
        def execute(prompts_path, run_dir, retry):
            batch = load_prompts_file(prompts_path)
            captured.append(initialize_run_state(batch, run_dir))
            return 0
        self.launcher("2", "Example Batch", "2", "Video 03", "6", execute=execute)
        self.assertEqual(captured[0].batch_id, captured[1].batch_id)
        self.assertEqual(captured[0].batch_id, self.batch.batch_id)
        self.assertEqual(captured[0].items, captured[1].items)
        self.assertEqual(self.prompts.read_text(encoding="utf-8"), "one\n")

    def test_assets_and_latest_completed_display_use_chosen_folder(self):
        adapter = OfflineAdapter()
        def execute(prompts_path, run_dir, retry):
            state, outcomes = execute_sequential_batch(load_prompts_file(prompts_path), run_dir, adapter, GenerationSettings(variants=1))
            return 0
        self.launcher("2", "Example Batch - Hobbies", "6", execute=execute)
        path = self.runs / "Example Batch - Hobbies"
        state = RunStateStore(path / "run_state.json").load()
        candidate = state.items[0].candidates[0]
        self.assertEqual(candidate.suggested_filename, "0001__prompt_0001_visual_001__v01.jpeg")
        self.assertEqual(Path(candidate.local_path).parent, path / "assets")
        self.assertTrue(Path(candidate.local_path).is_file())
        self.assertIn("Latest completed matching run: Example Batch - Hobbies", self.output)

    def test_custom_run_manual_retry_gate_and_paths_are_unchanged(self):
        run_dir = self.persisted_run("Example Batch - Hobbies")
        store = RunStateStore(run_dir / "run_state.json")
        state = store.load()
        state.items[0].status = "failed"
        state.items[0].attempt_count = 1
        state.items[0].error = "ordinary failure"
        store.save(state)
        before = store.path.read_bytes()
        self.launcher("3", "1", "6")
        self.execute.assert_called_once_with(self.prompts.resolve(), run_dir, True)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.runs.iterdir()], ["Example Batch - Hobbies"])

    def test_interrupted_download_reconciliation_remains_download_and_generation_free(self):
        adapter = OfflineAdapter()
        saved = {}
        real_save = RunStateStore.save
        def interrupt_after_candidate_commit(store, state):
            real_save(store, state)
            item = state.items[0]
            if item.download_status == "running" and item.candidates and all(c.download_status == "completed" for c in item.candidates):
                saved["state"] = state
                raise KeyboardInterrupt
        results = []
        calls = []
        def execute(prompts_path, run_dir, retry):
            calls.append((run_dir, retry))
            if len(calls) == 1:
                with patch.object(RunStateStore, "save", interrupt_after_candidate_commit):
                    execute_sequential_batch(self.batch, run_dir, adapter, GenerationSettings(variants=1))
            state, outcomes = execute_sequential_batch(self.batch, run_dir, adapter, GenerationSettings(variants=1))
            results.append((state, outcomes))
            return 0
        self.launcher("2", "Human readable batch", "1", "6", execute=execute)
        self.assertEqual(adapter.events, [("generate", 1), ("download", 1)])
        self.assertEqual(calls, [((self.runs / "Human readable batch").resolve(), False)] * 2)
        state, outcomes = results[0]
        self.assertEqual((outcomes[0].generation, outcomes[0].downloads), ("skipped_completed", "skipped_valid"))
        self.assertEqual(state.items[0].candidates, saved["state"].items[0].candidates)
        self.assertEqual(state.items[0].attempt_count, saved["state"].items[0].attempt_count)
        self.assertEqual(state.items[0].download_attempt_count, saved["state"].items[0].download_attempt_count)

    def test_keyboard_interrupt_during_name_prompt_creates_nothing(self):
        execute = MagicMock()
        result = run_launcher(self.root, execute, input_fn=MagicMock(side_effect=["2", KeyboardInterrupt]), output=lambda _: None)
        self.assertEqual(result, 130)
        self.assertFalse(self.runs.exists())
        execute.assert_not_called()

    def test_nonzero_initialization_result_keeps_existing_error_semantics(self):
        self.launcher("2", "Chosen name", "6", execute=MagicMock(return_value=2))
        self.execute.assert_called_once()
        self.assertTrue((self.runs / "Chosen name").is_dir())
        self.assertFalse((self.runs / "Chosen name" / "run_state.json").exists())
        self.assertTrue(any("Batch ended with an error" in line for line in self.output))


if __name__ == "__main__":
    unittest.main()
