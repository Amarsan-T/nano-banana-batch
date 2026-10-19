"""Real OS locks, disposable synthetic paths, no browser or runtime artifacts."""

import os
import subprocess
import sys
import unittest
import threading
from contextvars import copy_context
from pathlib import Path
from unittest.mock import patch

from nano_batch.execution_lock import ExecutionBusy, execution_ownership, run_lock_key
from nano_batch.preview import PreviewRequest, evaluate_preview
from pending_download_fixtures import pending_download_test_directory


class ExecutionLockTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "synthetic run \u732b"

    def probe(self, path):
        code = (
            "import sys\nfrom pathlib import Path\n"
            "from nano_batch.execution_lock import execution_ownership,ExecutionBusy\n"
            "try:\n"
            " with execution_ownership(Path(sys.argv[1])): pass\n"
            "except ExecutionBusy: sys.exit(23)\n"
        )
        return subprocess.run([sys.executable, "-B", "-c", code, str(path)],
                              capture_output=True, timeout=15).returncode

    def holder(self, path):
        code = (
            "import sys\nfrom pathlib import Path\n"
            "from nano_batch.execution_lock import execution_ownership\n"
            "with execution_ownership(Path(sys.argv[1])):\n"
            " print('owned',flush=True)\n sys.stdin.readline()\n"
        )
        child = subprocess.Popen([sys.executable, "-B", "-c", code, str(path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.release, child)
        self.assertEqual(child.stdout.readline(), b"owned\r\n" if os.name == "nt" else b"owned\n")
        return child

    @staticmethod
    def release(child):
        if child.poll() is None:
            child.communicate(b"release\n", timeout=10)
        else:
            child.communicate(timeout=10)

    def test_lock_does_not_create_selected_run_directory(self):
        with execution_ownership(self.run):
            self.assertFalse(self.run.exists())
            self.assertEqual(self.probe(self.run), 23)
        self.assertFalse(self.run.exists())
        self.assertEqual(self.probe(self.run), 0)

    def test_browser_session_serializes_different_runs(self):
        with execution_ownership(self.run):
            self.assertEqual(self.probe(self.root / "other"), 23)

    def test_existing_and_prospective_run_use_same_key(self):
        key = run_lock_key(self.run)
        with execution_ownership(self.run):
            self.run.mkdir()
            self.assertEqual(run_lock_key(self.run), key)
            self.assertEqual(self.probe(self.run), 23)

    def test_nested_runner_ownership_reuses_but_does_not_release_outer_gate(self):
        with execution_ownership(self.run):
            with execution_ownership(self.run):
                pass
            self.assertEqual(self.probe(self.run), 23)
        self.assertEqual(self.probe(self.run), 0)

    def test_normal_process_exit_releases_locks_without_deletion(self):
        child = self.holder(self.run)
        with self.assertRaises(ExecutionBusy):
            with execution_ownership(self.run):
                pass
        self.release(child)
        with execution_ownership(self.run):
            pass

    def test_crashed_synthetic_process_releases_os_ownership(self):
        child = self.holder(self.run)
        child.kill()  # Fault injection only; never a worker Stop implementation.
        child.wait(timeout=10)
        with execution_ownership(self.run):
            pass

    def test_relative_dot_and_unicode_space_aliases(self):
        self.assertEqual(run_lock_key(self.run), run_lock_key(self.run.parent / "." / self.run.name))
        relative = Path(os.path.relpath(self.run, Path.cwd()))
        self.assertEqual(run_lock_key(self.run), run_lock_key(relative))

    def test_copied_thread_context_cannot_bypass_os_ownership(self):
        outcomes = []
        def compete():
            try:
                with execution_ownership(self.run):
                    outcomes.append("unsafe")
            except ExecutionBusy:
                outcomes.append("busy")
        with execution_ownership(self.run):
            thread = threading.Thread(target=copy_context().run, args=(compete,))
            thread.start()
            thread.join(5)
            self.assertEqual(outcomes, ["busy"])

    @unittest.skipUnless(os.name == "nt", "Windows case and junction semantics")
    def test_windows_case_and_junction_aliases(self):
        self.run.mkdir()
        self.assertEqual(run_lock_key(self.run), run_lock_key(Path(str(self.run).upper())))
        alias = self.root / "synthetic junction"
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.run)],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(alias.is_junction())
        self.assertEqual(run_lock_key(alias), run_lock_key(self.run))
        with execution_ownership(self.run):
            self.assertEqual(self.probe(alias), 23)

    def test_read_only_preview_works_while_other_process_owns_session(self):
        prompts = self.root / "synthetic.txt"
        prompts.write_text("Synthetic still life.\n", encoding="utf-8")
        child = self.holder(self.run)
        self.assertEqual(len(evaluate_preview(PreviewRequest(str(prompts), str(self.run))).view.rows), 1)
        self.assertFalse(self.run.exists())
        self.release(child)

    def test_mutating_cli_conflict_happens_before_adapter_or_state_calls(self):
        import app
        from types import SimpleNamespace
        child = self.holder(self.run)
        with patch.object(app, "_flow_session_from_args", side_effect=AssertionError("adapter forbidden")), \
             patch.object(app, "initialize_run_state", side_effect=AssertionError("state forbidden")):
            for function in (app.cmd_run, app.cmd_generate_one, app.cmd_download_one, app.cmd_run_prompts_file):
                with self.subTest(function=function.__name__), self.assertRaises(ExecutionBusy):
                    function(SimpleNamespace(preview=False, dry_run=False))
        self.release(child)

    def test_cli_preview_bypasses_lock_and_keeps_existing_exit_semantics(self):
        import app
        from types import SimpleNamespace
        child = self.holder(self.run)
        with patch.object(app, "load_prompts_file", return_value=SimpleNamespace(items=())), \
             patch.object(app, "_run_sequential_command", return_value=0) as preview:
            self.assertEqual(app.cmd_run_prompts_file(SimpleNamespace(preview=True, prompts_file="synthetic.txt")), 0)
            preview.assert_called_once()
        self.release(child)

    def test_cli_process_returns_existing_error_exit_code_when_busy(self):
        child = self.holder(self.run)
        completed = subprocess.run(
            [sys.executable, "-B", "app.py", "run-prompts-file", str(self.root / "missing-input.txt"),
             "--run-dir", str(self.run), "--config", str(self.root / "missing-config.json")],
            capture_output=True, timeout=15,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"Another Nano Banana Batch execution", completed.stderr)
        self.assertNotIn(str(self.root).encode(), completed.stderr)
        self.assertFalse(self.run.exists())
        self.release(child)
