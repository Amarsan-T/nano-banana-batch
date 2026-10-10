import argparse
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import FLOW_HOME_URL
from nano_batch.batch import load_prompts_file
from nano_batch.errors import (
    FLOW_GENERATION_NOT_SUBMITTED,
    FLOW_GENERIC_GENERATION_FAILED,
    FLOW_POLICY_REJECTED,
)
from nano_batch.models import FlowProjectReference
from nano_batch.launcher import (
    LauncherGenerationRetryRequired,
    create_unique_run_dir,
    find_latest_assets_dir,
    find_matching_runs,
    load_launcher_batch,
    open_assets_in_explorer,
    open_prompts_in_notepad,
    retry_required_generation_orders,
    run_launcher,
)
from nano_batch.runner import initialize_run_state
from nano_batch.state import RunStateStore


class LauncherTests(unittest.TestCase):
    def project(self, prompts: str | None = None) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        if prompts is not None:
            (root / "prompts.txt").write_text(prompts, encoding="utf-8")
        return temporary, root

    def persisted_run(
        self,
        root: Path,
        batch,
        name: str,
        *,
        complete: bool = False,
        modified_ns: int | None = None,
    ) -> Path:
        run_dir = root / "runs" / name
        state = initialize_run_state(batch, run_dir)
        if complete:
            for item in state.items:
                item.status = "completed"
                item.download_status = "completed"
            RunStateStore(run_dir / "run_state.json").save(state)
        if modified_ns is not None:
            os.utime(run_dir / "run_state.json", ns=(modified_ns, modified_ns))
        return run_dir

    @staticmethod
    def input_sequence(*choices: str):
        values = iter(choices)
        return lambda _prompt: next(values)

    def test_launcher_uses_same_prompt_parser_and_semantic_identity(self):
        _temporary, root = self.project(" first \n# ignored\nsecond  prompt\n")

        launcher_batch = load_launcher_batch(root / "prompts.txt")
        cli_batch = load_prompts_file(root / "prompts.txt")

        self.assertEqual(launcher_batch, cli_batch)
        self.assertEqual(launcher_batch.batch_id, cli_batch.batch_id)

    def test_launcher_comments_and_blanks_match_prompt_file_semantics(self):
        _temporary, root = self.project("\n # note\nfirst\n\t\nsecond\n")

        batch = load_launcher_batch(root / "prompts.txt")

        self.assertEqual([item.prompt for item in batch.items], ["first", "second"])
        self.assertEqual([item.order for item in batch.items], [1, 2])

    def test_launcher_displays_new_six_option_menu(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        result = run_launcher(
            root,
            MagicMock(),
            input_fn=self.input_sequence("6"),
            output=output,
        )

        self.assertEqual(result, 0)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertEqual(
            [line for line in lines if line[:1].isdigit()],
            [
                "1. Launch Chrome",
                "2. Start new batch",
                "3. Resume current prompts",
                "4. Open prompts.txt",
                "5. Open latest images",
                "6. Exit",
            ],
        )

    def test_launch_chrome_uses_injected_bootstrap_only_and_returns_to_menu(self):
        _temporary, root = self.project("one\n")
        launch_chrome = MagicMock(return_value=0)
        execute = MagicMock()
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            launch_chrome=launch_chrome,
            input_fn=self.input_sequence("1", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        launch_chrome.assert_called_once_with()
        execute.assert_not_called()
        self.assertTrue(
            any("Dedicated Chrome is ready" in call.args[0] for call in output.call_args_list)
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_launch_chrome_failure_reports_reason_and_returns_to_menu(self):
        _temporary, root = self.project("one\n")
        launch_chrome = MagicMock(side_effect=RuntimeError("Chrome executable missing"))
        execute = MagicMock()
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            launch_chrome=launch_chrome,
            input_fn=self.input_sequence("1", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        launch_chrome.assert_called_once_with()
        execute.assert_not_called()
        self.assertTrue(
            any(
                "Could not launch dedicated Chrome: Chrome executable missing"
                in call.args[0]
                for call in output.call_args_list
            )
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_missing_prompts_is_clean_and_does_not_create_run(self):
        _temporary, root = self.project()
        execute = MagicMock()
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertFalse((root / "runs").exists())
        self.assertTrue(any("prompts.txt is missing" in call.args[0] for call in output.call_args_list))

    def test_zero_accepted_prompts_does_not_create_run(self):
        _temporary, root = self.project("\n # ignored\n\t")
        execute = MagicMock()
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertFalse((root / "runs").exists())
        self.assertTrue(any("contains no prompts" in call.args[0] for call in output.call_args_list))

    def test_start_new_validates_then_reserves_run_and_delegates(self):
        _temporary, root = self.project("one\ntwo\n")
        calls = []

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry, run_dir.is_dir()))
            return 0

        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        expected = (root / "runs" / "batch_20260908_123456").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [((root / "prompts.txt").resolve(), expected, False, True)],
        )
        self.assertTrue(
            any("Batch completed successfully" in call.args[0] for call in output.call_args_list)
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_menu_refreshes_accepted_prompt_count_after_batch(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        def execute(_prompts_path, _run_dir, _retry):
            (root / "prompts.txt").write_text("one\ntwo\n", encoding="utf-8")
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        accepted = [
            call.args[0]
            for call in output.call_args_list
            if call.args[0].startswith("Accepted prompts:")
        ]
        self.assertEqual(accepted, ["Accepted prompts: 1", "Accepted prompts: 2"])

    def test_menu_refreshes_latest_completed_matching_run_after_batch(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        output = MagicMock()

        def execute(_prompts_path, run_dir, _retry):
            state = initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].download_status = "completed"
            RunStateStore(run_dir / "run_state.json").save(state)
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        self.assertTrue(
            any(
                call.args[0]
                == "Latest completed matching run: batch_20260908_123456"
                for call in output.call_args_list
            )
        )

    def test_automatic_run_directory_uses_local_timestamp_name(self):
        _temporary, root = self.project()

        created = create_unique_run_dir(
            root / "runs",
            now=datetime(2026, 9, 8, 3, 4, 5),
        )

        self.assertEqual(created.name, "batch_20260908_030405")
        self.assertTrue(created.is_dir())

    def test_run_directory_collisions_receive_deterministic_suffixes(self):
        _temporary, root = self.project()
        runs_dir = root / "runs"
        runs_dir.mkdir()
        base = runs_dir / "batch_20260908_030405"
        second = runs_dir / "batch_20260908_030405_02"
        base.mkdir()
        second.mkdir()

        created = create_unique_run_dir(
            runs_dir,
            now=datetime(2026, 9, 8, 3, 4, 5),
        )

        self.assertEqual(created.name, "batch_20260908_030405_03")

    def test_existing_run_directories_are_never_overwritten(self):
        _temporary, root = self.project()
        existing = root / "runs" / "batch_20260908_030405"
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")

        created = create_unique_run_dir(
            root / "runs",
            now=datetime(2026, 9, 8, 3, 4, 5),
        )

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertNotEqual(created, existing)

    def test_app_launcher_delegates_to_existing_run_prompts_file_command(self):
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )
        prompts_path = Path("example prompts.txt")
        run_dir = Path("example run")

        def invoke(_project_dir, callback, *, launch_chrome):
            return callback(prompts_path, run_dir, True)

        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_run_prompts_file",
            return_value=9,
        ) as command:
            result = app.cmd_launcher(args)

        self.assertEqual(result, 9)
        delegated = command.call_args.args[0]
        self.assertEqual(delegated.prompts_file, str(prompts_path))
        self.assertEqual(delegated.run_dir, str(run_dir))
        self.assertTrue(delegated.retry)
        self.assertFalse(delegated.preview)

    def test_app_launcher_delegates_chrome_option_to_existing_debug_command(self):
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )
        execute_callbacks = []

        def invoke(_project_dir, execute_prompts_file, *, launch_chrome):
            execute_callbacks.append(execute_prompts_file)
            return launch_chrome()

        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_open_flow_debug",
            return_value=0,
        ) as existing_command:
            result = app.cmd_launcher(args)

        self.assertEqual(result, 0)
        existing_command.assert_called_once()
        launch_args = existing_command.call_args.args[0]
        self.assertEqual(launch_args.url, FLOW_HOME_URL)
        self.assertEqual(launch_args.config, args.config)
        self.assertIsNone(args.url)
        self.assertEqual(len(execute_callbacks), 1)

    def test_app_launcher_translates_existing_runner_retry_requirement(self):
        _temporary, root = self.project("one\n")
        prompts_path = root / "prompts.txt"
        batch = load_prompts_file(prompts_path)
        run_dir = self.persisted_run(root, batch, "failed")
        state_path = run_dir / "run_state.json"
        state = RunStateStore(state_path).load()
        state.items[0].status = "failed"
        state.items[0].attempt_count = 1
        state.items[0].error = "ordinary generation failure"
        RunStateStore(state_path).save(state)
        before = state_path.read_bytes()
        signals = []
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )

        def invoke(_project_dir, callback, *, launch_chrome):
            try:
                callback(prompts_path, run_dir, False)
            except LauncherGenerationRetryRequired as exc:
                signals.append(exc)
                return 0
            self.fail("retry-required runner error was not translated")

        runner_error = ValueError(
            "Generation retry required for order(s) 1; rerun run-batch with --retry."
        )
        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_run_prompts_file",
            side_effect=runner_error,
        ) as command:
            result = app.cmd_launcher(args)

        self.assertEqual(result, 0)
        self.assertEqual([signal.orders for signal in signals], [(1,)])
        self.assertFalse(command.call_args.args[0].retry)
        self.assertEqual(state_path.read_bytes(), before)

    def test_launcher_does_not_require_retry_for_proven_legacy_pre_start_failure(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        state = initialize_run_state(batch, root / "runs" / "legacy-pre-start")
        item = state.items[0]
        item.status = "failed"
        item.attempt_count = 1
        item.error = (
            "FlowAutomationError: Image-mode preflight requires exactly one "
            "visible generation control; found 2."
        )
        item.download_status = "not_started"
        item.download_attempt_count = 0

        self.assertEqual(retry_required_generation_orders(state), ())

    def test_app_launcher_translates_newly_persisted_generation_failure(self):
        _temporary, root = self.project("one\n")
        prompts_path = root / "prompts.txt"
        run_dir = root / "runs" / "new"
        run_dir.mkdir(parents=True)
        batch = load_prompts_file(prompts_path)
        signals = []
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )

        def fail_after_persisting(_command_args):
            state = initialize_run_state(batch, run_dir)
            state.items[0].status = "failed"
            state.items[0].attempt_count = 1
            state.items[0].error = "ordinary generation failure"
            RunStateStore(run_dir / "run_state.json").save(state)
            raise RuntimeError("ordinary generation failure")

        def invoke(_project_dir, callback, *, launch_chrome):
            try:
                callback(prompts_path, run_dir, False)
            except LauncherGenerationRetryRequired as exc:
                signals.append(exc)
                return 0
            self.fail("newly persisted generation failure was not translated")

        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_run_prompts_file",
            side_effect=fail_after_persisting,
        ):
            result = app.cmd_launcher(args)

        self.assertEqual(result, 0)
        self.assertEqual([signal.orders for signal in signals], [(1,)])

    def test_app_launcher_translates_interrupted_running_retry_requirement(self):
        _temporary, root = self.project("one\n")
        prompts_path = root / "prompts.txt"
        batch = load_prompts_file(prompts_path)
        run_dir = self.persisted_run(root, batch, "running")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "running"
        state.items[0].attempt_count = 1
        RunStateStore(run_dir / "run_state.json").save(state)
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )

        signals = []

        def invoke(_project_dir, callback, *, launch_chrome):
            try:
                callback(prompts_path, run_dir, False)
            except LauncherGenerationRetryRequired as exc:
                signals.append(exc)
                return 0
            self.fail("interrupted retry-required state was not translated")

        runner_error = ValueError(
            "Generation retry required for order(s) 1; rerun run-batch with --retry."
        )
        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_run_prompts_file",
            side_effect=runner_error,
        ):
            result = app.cmd_launcher(args)

        self.assertEqual(result, 0)
        self.assertEqual([signal.orders for signal in signals], [(1,)])

    def test_app_launcher_does_not_misclassify_unrelated_exception(self):
        _temporary, root = self.project("one\n")
        prompts_path = root / "prompts.txt"
        batch = load_prompts_file(prompts_path)
        run_dir = self.persisted_run(root, batch, "failed")
        state_path = run_dir / "run_state.json"
        state = RunStateStore(state_path).load()
        state.items[0].status = "failed"
        RunStateStore(state_path).save(state)
        args = argparse.Namespace(
            config="config.json",
            url=None,
            profile_dir=None,
            debug_address=None,
            debug_port=None,
        )

        def invoke(_project_dir, callback, *, launch_chrome):
            return callback(prompts_path, run_dir, False)

        with patch.object(app, "run_launcher", side_effect=invoke), patch.object(
            app,
            "cmd_run_prompts_file",
            side_effect=ValueError("unrelated configuration error"),
        ), patch("sys.stderr"):
            result = app.cmd_launcher(args)

        self.assertEqual(result, 2)

    def test_resume_matching_uses_batch_id_not_prompt_count(self):
        _temporary, root = self.project("one\ntwo\n")
        current = load_prompts_file(root / "prompts.txt")
        matching = self.persisted_run(root, current, "matching")
        (root / "prompts.txt").write_text("changed\ntwo\n", encoding="utf-8")
        incompatible_batch = load_prompts_file(root / "prompts.txt")
        incompatible = self.persisted_run(root, incompatible_batch, "same-count")
        (root / "prompts.txt").write_text("one\ntwo\n", encoding="utf-8")

        incomplete, _completed = find_matching_runs(root / "runs", current)

        self.assertEqual([match.run_dir for match in incomplete], [matching.resolve()])
        self.assertNotIn(incompatible.resolve(), [match.run_dir for match in incomplete])

    def test_incompatible_batch_id_is_never_offered_for_resume(self):
        _temporary, root = self.project("current\n")
        current = load_prompts_file(root / "prompts.txt")
        (root / "prompts.txt").write_text("other\n", encoding="utf-8")
        other = load_prompts_file(root / "prompts.txt")
        self.persisted_run(root, other, "incompatible")
        (root / "prompts.txt").write_text("current\n", encoding="utf-8")
        execute = MagicMock()
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertTrue(any("No incomplete run matches" in call.args[0] for call in output.call_args_list))
        self.assertNotEqual(current.batch_id, other.batch_id)

    def test_one_incomplete_compatible_run_resumes_without_mutating_state(self):
        _temporary, root = self.project("one\ntwo\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "resume-me")
        state_path = run_dir / "run_state.json"
        before = state_path.read_bytes()
        execute = MagicMock(return_value=0)
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), run_dir.resolve(), False
        )
        self.assertEqual(state_path.read_bytes(), before)
        self.assertTrue(
            any("Batch completed successfully" in call.args[0] for call in output.call_args_list)
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_multiple_compatible_runs_are_sorted_newest_first(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        older = self.persisted_run(root, batch, "older", modified_ns=1_000_000_000)
        newer = self.persisted_run(root, batch, "newer", modified_ns=2_000_000_000)

        incomplete, _completed = find_matching_runs(root / "runs", batch)

        self.assertEqual(
            [match.run_dir for match in incomplete],
            [newer.resolve(), older.resolve()],
        )

    def test_multiple_resume_choices_allow_selection_and_cancel(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        older = self.persisted_run(root, batch, "older", modified_ns=1_000_000_000)
        self.persisted_run(root, batch, "newer", modified_ns=2_000_000_000)
        execute = MagicMock(return_value=0)

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "2", "6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), older.resolve(), False
        )

    def test_completed_matching_run_is_informational_not_incomplete(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        completed_run = self.persisted_run(root, batch, "done", complete=True)

        incomplete, completed = find_matching_runs(root / "runs", batch)

        self.assertEqual(incomplete, [])
        self.assertEqual([match.run_dir for match in completed], [completed_run.resolve()])

    def test_matching_run_inspection_is_read_only(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "read-only")
        state_path = run_dir / "run_state.json"
        before = state_path.read_bytes()

        find_matching_runs(root / "runs", batch)

        self.assertEqual(state_path.read_bytes(), before)

    def test_open_prompts_uses_notepad_with_safe_argument_list(self):
        _temporary, root = self.project()
        prompts_path = root / "folder with spaces" / "prompts.txt"

        with patch("nano_batch.launcher.subprocess.run") as process:
            open_prompts_in_notepad(prompts_path)

        process.assert_called_once_with(
            ["notepad.exe", str(prompts_path.resolve())],
            check=False,
        )

    def test_open_prompts_returns_to_refreshed_menu(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        with patch("nano_batch.launcher.open_prompts_in_notepad") as open_notepad:
            result = run_launcher(
                root,
                MagicMock(),
                input_fn=self.input_sequence("4", "6"),
                output=output,
            )

        self.assertEqual(result, 0)
        open_notepad.assert_called_once_with(root / "prompts.txt")
        accepted_lines = [
            call.args[0] for call in output.call_args_list if call.args[0].startswith("Accepted prompts:")
        ]
        self.assertEqual(accepted_lines, ["Accepted prompts: 1", "Accepted prompts: 1"])

    def test_open_prompts_is_available_after_successful_batch(self):
        _temporary, root = self.project("one\n")

        with patch("nano_batch.launcher.open_prompts_in_notepad") as open_notepad:
            result = run_launcher(
                root,
                MagicMock(return_value=0),
                input_fn=self.input_sequence("2", "", "4", "6"),
                output=lambda _line: None,
                now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
            )

        self.assertEqual(result, 0)
        open_notepad.assert_called_once_with(root / "prompts.txt")

    def test_open_latest_images_skips_newer_run_without_assets(self):
        _temporary, root = self.project()
        older = root / "runs" / "older"
        assets = older / "assets"
        assets.mkdir(parents=True)
        (assets / "image.jpeg").write_bytes(b"offline")
        newer = root / "runs" / "newer"
        newer.mkdir()
        os.utime(older, ns=(1_000_000_000, 1_000_000_000))
        os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

        self.assertEqual(find_latest_assets_dir(root / "runs"), assets.resolve())

    def test_open_latest_images_uses_explorer_with_safe_argument_list(self):
        _temporary, root = self.project()
        assets = root / "run with spaces" / "assets"
        assets.mkdir(parents=True)

        with patch("nano_batch.launcher.subprocess.Popen") as process:
            open_assets_in_explorer(assets)

        process.assert_called_once_with(
            ["explorer.exe", str(assets.resolve())],
            shell=False,
        )

    def test_open_latest_images_is_available_after_successful_batch(self):
        _temporary, root = self.project("one\n")
        opened = []

        def execute(_prompts_path, run_dir, _retry):
            assets_dir = run_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "result.jpeg").write_bytes(b"offline")
            return 0

        with patch(
            "nano_batch.launcher.open_assets_in_explorer",
            side_effect=lambda assets_dir: opened.append(assets_dir),
        ):
            result = run_launcher(
                root,
                execute,
                input_fn=self.input_sequence("2", "", "5", "6"),
                output=lambda _line: None,
                now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
            )

        expected = (
            root / "runs" / "batch_20260908_123456" / "assets"
        ).resolve()
        self.assertEqual(result, 0)
        self.assertEqual(opened, [expected])

    def test_no_assets_case_is_clean(self):
        _temporary, root = self.project("one\n")
        (root / "runs" / "empty").mkdir(parents=True)
        output = MagicMock()

        result = run_launcher(
            root,
            MagicMock(),
            input_fn=self.input_sequence("5", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        self.assertTrue(any("No generated images" in call.args[0] for call in output.call_args_list))

    def test_invalid_menu_selection_does_not_crash(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        result = run_launcher(
            root,
            MagicMock(),
            input_fn=self.input_sequence("invalid", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        self.assertTrue(any("Please choose" in call.args[0] for call in output.call_args_list))

    def test_nonzero_batch_result_reports_error_and_does_not_retry(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock(return_value=3)
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(),
            (root / "runs" / "batch_20260908_123456").resolve(),
            False,
        )
        self.assertTrue(
            any("Batch ended with an error" in call.args[0] for call in output.call_args_list)
        )
        self.assertFalse(
            any("Retry failed generation" in call.args[0] for call in output.call_args_list)
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_start_retry_requires_explicit_choice_and_reuses_exact_paths(self):
        _temporary, root = self.project("one\n")
        calls = []
        output = MagicMock()

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry))
            if not retry:
                raise LauncherGenerationRetryRequired((1,))
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        expected_prompts = (root / "prompts.txt").resolve()
        expected_run = (root / "runs" / "batch_20260908_123456").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                (expected_prompts, expected_run, False),
                (expected_prompts, expected_run, True),
            ],
        )
        self.assertEqual([path.name for path in (root / "runs").iterdir()], [expected_run.name])
        self.assertTrue(
            any("Generation retry required for Order 1" in call.args[0] for call in output.call_args_list)
        )
        self.assertTrue(
            any("Batch completed successfully" in call.args[0] for call in output.call_args_list)
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )

    def test_resume_retry_reuses_selected_run_and_existing_retry_flag(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "resume-me").resolve()
        calls = []

        def execute(prompts_path, selected_run, retry):
            calls.append((prompts_path, selected_run, retry))
            if not retry:
                raise LauncherGenerationRetryRequired((1,))
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "1", "6"),
            output=lambda _line: None,
        )

        prompts_path = (root / "prompts.txt").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [(prompts_path, run_dir, False), (prompts_path, run_dir, True)],
        )
        self.assertEqual([path.resolve() for path in (root / "runs").iterdir()], [run_dir])

    def test_persisted_failed_run_offers_retry_before_doomed_resume(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = root / "runs" / "managed-failed"
        state = initialize_run_state(batch, run_dir, require_flow_project=True)
        project = FlowProjectReference(
            project_id="00000000-0000-4000-8000-00000000000a",
            project_url=(
                "https://flow.google.com/project/"
                "00000000-0000-4000-8000-00000000000a"
            ),
        )
        state.flow_project = project
        state.items[0].status = "failed"
        state.items[0].attempt_count = 1
        state.items[0].error = "ordinary generation failure"
        store = RunStateStore(run_dir / "run_state.json")
        store.save(state)
        before = store.path.read_bytes()
        execute = MagicMock(return_value=0)
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "1", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), run_dir.resolve(), True
        )
        self.assertNotIn(
            unittest.mock.call((root / "prompts.txt").resolve(), run_dir.resolve(), False),
            execute.call_args_list,
        )
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.load().flow_project, project)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn("Generation retry required for Order 1.", lines)
        self.assertIn("1. Retry failed generation", lines)
        self.assertIn("2. Return to main menu", lines)
        self.assertIn("3. Exit", lines)

    def test_interrupted_running_run_uses_same_direct_retry_gate(self):
        _temporary, root = self.project("one\ntwo\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = root / "runs" / "managed-interrupted"
        state = initialize_run_state(batch, run_dir, require_flow_project=True)
        project = FlowProjectReference(
            project_id="00000000-0000-4000-8000-00000000000a",
            project_url=(
                "https://flow.google.com/project/"
                "00000000-0000-4000-8000-00000000000a"
            ),
        )
        state.flow_project = project
        state.items[0].status = "completed"
        state.items[0].download_status = "completed"
        state.items[1].status = "running"
        state.items[1].attempt_count = 3
        RunStateStore(run_dir / "run_state.json").save(state)
        execute = MagicMock(return_value=0)

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "1", "6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), run_dir.resolve(), True
        )
        self.assertEqual(RunStateStore(run_dir / "run_state.json").load().flow_project, project)

    def test_retry_required_return_to_menu_causes_no_production_or_chrome_mutation(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "failed")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "failed"
        RunStateStore(run_dir / "run_state.json").save(state)
        execute = MagicMock()
        launch_chrome = MagicMock()

        result = run_launcher(
            root,
            execute,
            launch_chrome=launch_chrome,
            input_fn=self.input_sequence("3", "2", "6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()
        launch_chrome.assert_not_called()

    def test_retry_required_exit_terminates_without_production(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "failed")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "failed"
        RunStateStore(run_dir / "run_state.json").save(state)
        execute = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "3"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_unconsumed_policy_retry_keeps_existing_automatic_resume_semantics(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "policy-auto")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "failed"
        state.items[0].generation_error_code = FLOW_POLICY_REJECTED
        state.items[0].automatic_policy_retry_count = 0
        RunStateStore(run_dir / "run_state.json").save(state)
        execute = MagicMock(return_value=0)

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), run_dir.resolve(), False
        )

    def test_remaining_generic_failure_retry_uses_normal_resume_without_input(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "generic-auto")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "failed"
        state.items[0].attempt_count = 2
        state.items[0].generation_error_code = FLOW_GENERIC_GENERATION_FAILED
        state.items[0].automatic_generic_failure_retry_count = 1
        RunStateStore(run_dir / "run_state.json").save(state)
        execute = MagicMock(return_value=0)

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(), run_dir.resolve(), False
        )

    def test_exhausted_generic_failure_retry_requires_explicit_retry(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "generic-exhausted")
        state = RunStateStore(run_dir / "run_state.json").load()
        state.items[0].status = "failed"
        state.items[0].attempt_count = 3
        state.items[0].generation_error_code = FLOW_GENERIC_GENERATION_FAILED
        state.items[0].automatic_generic_failure_retry_count = 2
        RunStateStore(run_dir / "run_state.json").save(state)

        self.assertEqual(retry_required_generation_orders(state), (1,))

    def test_retry_return_choice_returns_to_menu_without_retrying(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock(side_effect=LauncherGenerationRetryRequired((1,)))
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "2", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            (root / "prompts.txt").resolve(),
            (root / "runs" / "batch_20260908_123456").resolve(),
            False,
        )
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )
        self.assertFalse(
            any("Batch completed successfully" in call.args[0] for call in output.call_args_list)
        )

    def test_retry_required_again_is_reoffered_without_silent_loop_or_state_change(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        run_dir = self.persisted_run(root, batch, "failed")
        state_path = run_dir / "run_state.json"
        state = RunStateStore(state_path).load()
        state.items[0].status = "failed"
        state.items[0].attempt_count = 2
        state.items[0].error = "ordinary generation failure"
        RunStateStore(state_path).save(state)
        before = state_path.read_bytes()
        execute = MagicMock(side_effect=LauncherGenerationRetryRequired((1,)))
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("3", "1", "2", "6"),
            output=output,
        )

        self.assertEqual(result, 0)
        prompts_path = (root / "prompts.txt").resolve()
        self.assertEqual(
            execute.call_args_list,
            [
                unittest.mock.call(prompts_path, run_dir.resolve(), True),
            ],
        )
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(
            sum(
                "Generation retry required" in call.args[0]
                for call in output.call_args_list
            ),
            2,
        )

    def test_retry_success_refreshes_prompt_count_before_next_choice(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        def execute(_prompts_path, _run_dir, retry):
            if not retry:
                raise LauncherGenerationRetryRequired((1,))
            (root / "prompts.txt").write_text("one\ntwo\n", encoding="utf-8")
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        accepted = [
            call.args[0]
            for call in output.call_args_list
            if call.args[0].startswith("Accepted prompts:")
        ]
        self.assertEqual(accepted, ["Accepted prompts: 1", "Accepted prompts: 2"])

    def test_generation_retry_handling_returns_zero_instead_of_real_code_two_symptom(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        result = run_launcher(
            root,
            MagicMock(side_effect=LauncherGenerationRetryRequired((1,))),
            input_fn=self.input_sequence("2", "", "2", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        self.assertFalse(
            any("Launcher exited with code 2" in call.args[0] for call in output.call_args_list)
        )

    def test_retry_prompt_keyboard_interrupt_exits_cleanly(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock(side_effect=LauncherGenerationRetryRequired((1,)))
        output = MagicMock()
        user_input = MagicMock(side_effect=["2", "", KeyboardInterrupt])

        result = run_launcher(
            root,
            execute,
            input_fn=user_input,
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 130)
        execute.assert_called_once()
        self.assertTrue(any("Launcher cancelled" in call.args[0] for call in output.call_args_list))

    def test_user_exit_does_not_invoke_production(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("6"),
            output=lambda _line: None,
        )

        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_launcher_cli_startup_does_not_construct_flow_session(self):
        with patch("sys.argv", ["app.py", "launcher"]), patch.object(
            app,
            "run_launcher",
            return_value=0,
        ) as menu, patch.object(
            app,
            "_flow_session_from_args",
            side_effect=AssertionError("launcher navigation must not create Flow"),
        ):
            result = app.main()

        self.assertEqual(result, 0)
        menu.assert_called_once()

    def test_keyboard_interrupt_exits_cleanly(self):
        _temporary, root = self.project("one\n")
        output = MagicMock()

        result = run_launcher(
            root,
            MagicMock(),
            input_fn=MagicMock(side_effect=KeyboardInterrupt),
            output=output,
        )

        self.assertEqual(result, 130)
        self.assertTrue(any("Launcher cancelled" in call.args[0] for call in output.call_args_list))

    def test_batch_keyboard_interrupt_returns_to_recovery_then_main_menu(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock(side_effect=KeyboardInterrupt)
        output = MagicMock()

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "2", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        execute.assert_called_once()
        self.assertEqual(
            sum(call.args[0] == "Nano Banana Batch" for call in output.call_args_list),
            2,
        )
        self.assertTrue(
            any("Batch interrupted by user" in call.args[0] for call in output.call_args_list)
        )

    def test_interruption_resume_reuses_exact_run_without_automatic_retry(self):
        _temporary, root = self.project("one\n")
        calls = []

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry))
            if len(calls) == 1:
                raise KeyboardInterrupt
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=lambda _line: None,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        expected_prompts = (root / "prompts.txt").resolve()
        expected_run = (root / "runs" / "batch_20260908_123456").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                (expected_prompts, expected_run, False),
                (expected_prompts, expected_run, False),
            ],
        )

    def test_pre_start_interrupt_offers_resume_without_generation_retry(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        calls = []
        output = MagicMock()

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry))
            if len(calls) == 1:
                state = initialize_run_state(batch, run_dir)
                state.items[0].status = "pending"
                state.items[0].attempt_count = 0
                state.items[0].generation_error_code = (
                    FLOW_GENERATION_NOT_SUBMITTED
                )
                state.items[0].error = (
                    "FLOW_GENERATION_NOT_SUBMITTED: interrupted before Start"
                )
                RunStateStore(run_dir / "run_state.json").save(state)
                raise KeyboardInterrupt
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 0)
        self.assertEqual([retry for _path, _run, retry in calls], [False, False])
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn("Batch interrupted by user.", lines)
        self.assertIn("1. Resume current run", lines)
        self.assertNotIn("1. Retry failed generation", lines)
        self.assertFalse(
            any("Generation retry required" in line for line in lines)
        )

    def test_running_interrupt_offers_retry_gate_but_return_preserves_state_and_assets(self):
        _temporary, root = self.project("one\ntwo\n")
        batch = load_prompts_file(root / "prompts.txt")
        captured = {}
        output = MagicMock()

        def execute(_prompts_path, run_dir, retry):
            self.assertFalse(retry)
            state = initialize_run_state(batch, run_dir, require_flow_project=True)
            state.flow_project = FlowProjectReference(
                project_id="00000000-0000-4000-8000-000000000001",
                project_url=(
                    "https://flow.google.com/project/"
                    "00000000-0000-4000-8000-000000000001"
                ),
            )
            state.items[0].status = "completed"
            state.items[0].download_status = "completed"
            state.items[1].status = "running"
            state.items[1].attempt_count = 1
            store = RunStateStore(run_dir / "run_state.json")
            store.save(state)
            asset = run_dir / "assets" / "completed-order-1.jpeg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"preserved canonical asset")
            captured["state"] = store.path.read_bytes()
            captured["asset"] = asset
            raise KeyboardInterrupt

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "2", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        run_dir = root / "runs" / "batch_20260908_123456"
        self.assertEqual(result, 0)
        self.assertEqual((run_dir / "run_state.json").read_bytes(), captured["state"])
        self.assertEqual(captured["asset"].read_bytes(), b"preserved canonical asset")
        preserved = RunStateStore(run_dir / "run_state.json").load()
        self.assertEqual(preserved.items[0].status, "completed")
        self.assertEqual(preserved.items[1].status, "running")
        self.assertEqual(
            preserved.flow_project.project_id,
            "00000000-0000-4000-8000-000000000001",
        )
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn("Generation retry required for Order 2.", lines)
        self.assertIn("1. Retry failed generation", lines)

    def test_running_interrupt_retry_choice_never_issues_doomed_resume(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        calls = []

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry))
            if len(calls) == 1:
                state = initialize_run_state(batch, run_dir)
                state.items[0].status = "running"
                state.items[0].attempt_count = 1
                RunStateStore(run_dir / "run_state.json").save(state)
                raise KeyboardInterrupt
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=lambda _line: None,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        expected_prompts = (root / "prompts.txt").resolve()
        expected_run = (root / "runs" / "batch_20260908_123456").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                (expected_prompts, expected_run, False),
                (expected_prompts, expected_run, True),
            ],
        )

    def test_explicit_failed_interrupt_offers_retry_and_reuses_same_run(self):
        _temporary, root = self.project("one\n")
        batch = load_prompts_file(root / "prompts.txt")
        calls = []
        output = MagicMock()

        def execute(prompts_path, run_dir, retry):
            calls.append((prompts_path, run_dir, retry))
            if len(calls) == 1:
                state = initialize_run_state(batch, run_dir)
                state.items[0].status = "failed"
                state.items[0].attempt_count = 1
                state.items[0].error = "ordinary generation failure"
                RunStateStore(run_dir / "run_state.json").save(state)
                raise KeyboardInterrupt
            return 0

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "1", "6"),
            output=output,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        expected_prompts = (root / "prompts.txt").resolve()
        expected_run = (root / "runs" / "batch_20260908_123456").resolve()
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                (expected_prompts, expected_run, False),
                (expected_prompts, expected_run, True),
            ],
        )
        self.assertTrue(
            any("Generation retry required for Order 1." in call.args[0] for call in output.call_args_list)
        )

    def test_interruption_recovery_exit_terminates_launcher_cleanly(self):
        _temporary, root = self.project("one\n")
        execute = MagicMock(side_effect=KeyboardInterrupt)

        result = run_launcher(
            root,
            execute,
            input_fn=self.input_sequence("2", "", "3"),
            output=lambda _line: None,
            now_fn=lambda: datetime(2026, 9, 8, 12, 34, 56),
        )

        self.assertEqual(result, 130)
        execute.assert_called_once()

    def test_run_bat_uses_project_relative_paths_without_user_specific_path(self):
        script = (Path(__file__).resolve().parents[1] / "run.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn('cd /d "%~dp0"', script)
        self.assertIn('%~dp0.venv\\Scripts\\python.exe', script)
        self.assertIn('"%~dp0app.py" launcher', script)
        self.assertNotRegex(script, r"(?i)[a-z]:[\\/]+Users[\\/]")
        self.assertNotIn("Activate.ps1", script)

    def test_run_bat_quotes_paths_and_preserves_exit_code(self):
        script = (Path(__file__).resolve().parents[1] / "run.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn('"%PYTHON_EXE%" "%~dp0app.py" launcher', script)
        self.assertIn('set "LAUNCHER_EXIT=%ERRORLEVEL%"', script)
        self.assertIn("exit /b %LAUNCHER_EXIT%", script)
        self.assertIn("if not exist \"%PYTHON_EXE%\"", script)
        self.assertIn("ERROR: Missing .venv\\Scripts\\python.exe", script)
        self.assertNotIn("Terminate batch job", script)


if __name__ == "__main__":
    unittest.main()
