"""Stage 4D uses disposable inputs and fake adapters/subprocesses only."""

import io
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from nano_batch.batch import load_prompts_file
from nano_batch.gui_execution import (
    ExecutionController, authorization_digest, evaluate_execution_preview,
    execution_fingerprint, output_folder,
)
from nano_batch.gui_worker import StopBridge, run_job
from nano_batch.preview import PreviewError, PreviewMessage, PreviewRequest, PreviewState
from nano_batch.preview_gui import PreviewWindow
from nano_batch.state import RunStateStore
from nano_batch.worker_process import WorkerProcess
from nano_batch.worker_protocol import JobMode, JobRequest, ProtocolError, decode_request, encode, message
from pending_download_fixtures import pending_download_test_directory
from test_gui_worker import SyntheticAdapter
import test_preview_gui as preview_tests
import test_worker_process as process_tests


def until(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(0.005)
    raise AssertionError("Synthetic event did not arrive")


class GuiExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompts = self.root / "synthetic \u732b prompts.txt"
        self.prompts.write_text("Synthetic private still life.\n", encoding="utf-8")
        self.run = self.root / "synthetic run"
        self.config = self.root / "synthetic-config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.adapter = SyntheticAdapter()
        self.stop = StopBridge()

    def preview(self):
        request = PreviewRequest(str(self.prompts), str(self.run), self.run.exists())
        result = evaluate_execution_preview(request, self.config)
        return request, authorization_digest(result.fingerprint, result.view)

    def execute(self, token):
        request = JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config), token)
        return run_job(request, lambda _value: None, self.stop,
                       adapter_factory=lambda *_args: self.adapter).as_dict()

    def test_fresh_confirmed_binding_runs_existing_engine(self):
        _, token = self.preview()
        result = self.execute(token)
        self.assertEqual((result["outcome"], result["completed"]), ("completed", 1))
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_skip_only_resume_has_no_generation_or_download_calls(self):
        self.execute(self.preview()[1])
        self.stop = StopBridge()
        self.assertEqual(self.execute(self.preview()[1])["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_download_only_resume_preserves_saved_ownership(self):
        def interrupt():
            self.stop.request()
            from nano_batch.cancellation import cancellation_checkpoint
            cancellation_checkpoint("generation_cleanup")
        # Modern ownership permits checkpointing before adapter cleanup.
        from nano_batch.models import FlowProjectReference
        from nano_batch.runner import initialize_run_state
        store = RunStateStore(self.run / "run_state.json")
        state = initialize_run_state(load_prompts_file(self.prompts), self.run, require_flow_project=True)
        project = "00000000-0000-4000-8000-000000000999"
        state.flow_project = FlowProjectReference(project, f"https://flow.google.com/project/{project}")
        store.save(state)
        self.adapter.after_checkpoint = interrupt
        self.assertEqual(self.execute(self.preview()[1])["exit_code"], 130)
        self.stop = StopBridge()
        self.adapter.after_checkpoint = lambda: None
        self.assertEqual(self.execute(self.preview()[1])["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_changed_prompt_is_rejected_inside_worker_without_adapter_calls(self):
        _, token = self.preview()
        stamp = self.prompts.stat()
        self.prompts.write_bytes(self.prompts.read_bytes().replace(b"private", b"changed"))
        os.utime(self.prompts, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        result = self.execute(token)
        self.assertEqual((result["outcome"], result["code"]), ("blocked", "STALE_PREVIEW"))
        self.assertEqual(self.adapter.generations, 0)
        self.assertFalse(self.run.exists())

    def test_changed_saved_state_is_rejected_before_execution(self):
        self.execute(self.preview()[1])
        self.stop = StopBridge()
        _, token = self.preview()
        state_path = self.run / "run_state.json"
        state_path.write_bytes(state_path.read_bytes() + b"\n")
        before = state_path.read_bytes()
        self.assertEqual(self.execute(token)["code"], "STALE_PREVIEW")
        self.assertEqual(before, state_path.read_bytes())
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_changed_configuration_is_rejected_before_adapter_creation(self):
        _, token = self.preview()
        self.config.write_text('{"generation":{"result_timeout_seconds":301}}', encoding="utf-8")
        self.assertEqual(self.execute(token)["code"], "STALE_PREVIEW")
        self.assertFalse(self.run.exists())

    def test_configuration_creation_invalidates_missing_config_preview(self):
        self.config.unlink()
        _, token = self.preview()
        self.config.write_text("{}", encoding="utf-8")
        self.assertEqual(self.execute(token)["code"], "STALE_PREVIEW")

    def test_prospective_run_appearing_without_state_invalidates_authorization(self):
        _, token = self.preview()
        self.run.mkdir()
        self.assertEqual(self.execute(token)["code"], "STALE_PREVIEW")
        self.assertEqual(list(self.run.iterdir()), [])
        self.assertEqual(self.adapter.generations, 0)

    def test_asset_changes_cannot_silently_authorize_a_different_plan(self):
        self.execute(self.preview()[1])
        self.stop = StopBridge()
        _, token = self.preview()
        asset = next((self.run / "assets").glob("*.jpeg"))
        asset.write_bytes(b"synthetic corrupt image")
        result = self.execute(token)
        self.assertNotEqual(result["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))
        self.assertEqual(asset.read_bytes(), b"synthetic corrupt image")

    def test_blocked_ambiguous_order_never_authorizes_retry(self):
        from nano_batch.runner import initialize_run_state
        state = initialize_run_state(load_prompts_file(self.prompts), self.run)
        state.items[0].status = "running"
        state.items[0].attempt_count = 1
        store = RunStateStore(self.run / "run_state.json")
        store.save(state)
        request, token = self.preview()
        self.assertEqual(evaluate_execution_preview(request, self.config).view.count(PreviewState.BLOCKED), 1)
        before = store.path.read_bytes()
        self.assertEqual(self.execute(token)["outcome"], "blocked")
        self.assertEqual(before, store.path.read_bytes())
        self.assertEqual(self.adapter.generations, 0)

    def test_invalid_configuration_has_fixed_private_safe_guidance(self):
        self.config.write_text('{"generation":"synthetic private config"}', encoding="utf-8")
        with self.assertRaises(PreviewError) as caught:
            self.preview()
        self.assertEqual(caught.exception.code, "configuration")
        self.assertNotIn("private", str(caught.exception))

    def test_preview_config_and_plan_evaluation_is_read_only(self):
        from test_gui_preview import tree_snapshot
        before = tree_snapshot(self.root)
        with patch.object(Path, "mkdir", side_effect=AssertionError("write forbidden")), \
             patch.object(RunStateStore, "save", side_effect=AssertionError("save forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
             patch("socket.socket", side_effect=AssertionError("network forbidden")):
            request, token = self.preview()
            self.assertEqual(len(token), 64)
            self.assertEqual(execution_fingerprint(request, self.config), evaluate_execution_preview(request, self.config).fingerprint)
        self.assertEqual(before, tree_snapshot(self.root))

    def test_private_snapshot_is_optional_bounded_and_not_repr_diagnostics(self):
        request = JobRequest(JobMode.EXECUTE, "synthetic", "synthetic", "synthetic", "a" * 64)
        self.assertEqual(decode_request(encode(request)), request)
        self.assertNotIn("a" * 64, repr(request))
        for token in ("private", "A" * 64, 123, "a" * 65):
            with self.assertRaises(ProtocolError):
                encode(replace(request, expected_snapshot=token))

    def test_output_folder_never_follows_redirected_assets(self):
        self.run.mkdir()
        (self.run / "run_state.json").write_text("{}", encoding="utf-8")
        self.assertEqual(output_folder(str(self.run)), self.run.resolve())
        assets = self.run / "assets"
        assets.mkdir()
        self.assertEqual(output_folder(str(self.run)), assets.resolve())
        assets.rmdir()
        outside = self.root / "synthetic unrelated folder"
        outside.mkdir()
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(assets), str(outside)],
                                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
        else:
            assets.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            output_folder(str(self.run))

    def test_empty_or_unvalidated_output_selection_is_not_opened(self):
        for selection in ("", str(self.root), str(self.root / "absent")):
            with self.assertRaises((ValueError, OSError)):
                output_folder(selection)

    def factory(self, script):
        original = subprocess.Popen
        def construct(executable):
            def command(_args, **kwargs):
                return original([str(executable), "-B", "-c", script], **kwargs)
            with patch("nano_batch.worker_process.subprocess.Popen", side_effect=command):
                return WorkerProcess(executable)
        return construct

    def controller(self, script):
        controller = ExecutionController(Path(sys.executable), worker_factory=self.factory(script))
        self.addCleanup(controller.wait)
        self.addCleanup(controller.request_stop)
        return controller

    def terminal(self, controller):
        values = []
        def read():
            values.extend(controller.drain())
            return next((v for v in values if v.kind == "terminal"), None)
        value = until(read)
        self.assertFalse(controller.active)
        return value.as_dict()

    def test_supervisor_rejects_stale_input_without_spawning_worker(self):
        request, token = self.preview()
        self.prompts.write_text("Synthetic changed.\n", encoding="utf-8")
        factory = unittest.mock.Mock(side_effect=AssertionError("worker forbidden"))
        controller = ExecutionController(Path(sys.executable), worker_factory=factory)
        self.addCleanup(controller.wait)
        controller.start(request, self.config, token)
        self.assertEqual(self.terminal(controller)["code"], "STALE_PREVIEW")
        factory.assert_not_called()

    def test_worker_startup_permissions_are_not_mislabeled_as_stale_preview(self):
        controller = ExecutionController(Path(sys.executable), worker_factory=lambda _exe: (_ for _ in ()).throw(PermissionError("synthetic private")))
        self.addCleanup(controller.wait)
        controller.start(*self._start_args())
        result = self.terminal(controller)
        self.assertEqual((result["outcome"], result["code"]), ("failed", "INVALID_INPUT"))
        self.assertNotIn("private", repr(result))

    def test_submit_failure_still_stops_and_reaps_worker(self):
        factory = self.factory(process_tests.WorkerProcessTests.fake_adapter_script(None))
        children = []
        def broken(executable):
            child = factory(executable)
            child.submit = lambda _request: (_ for _ in ()).throw(BrokenPipeError())
            children.append(child)
            return child
        controller = ExecutionController(Path(sys.executable), worker_factory=broken)
        self.addCleanup(controller.wait)
        self.addCleanup(controller.request_stop)
        controller.start(*self._start_args())
        self.assertEqual(self.terminal(controller)["outcome"], "communication_lost")
        self.assertEqual(children[0].process.poll(), 130)
        self.assertTrue(children[0].close())

    def test_supervisor_normal_fake_execution_and_late_stop(self):
        script = process_tests.WorkerProcessTests.fake_adapter_script(None)
        controller = self.controller(script)
        controller.start(*self._start_args())
        self.assertEqual(self.terminal(controller)["outcome"], "completed")
        self.assertFalse(controller.request_stop())
        controller.start(*self._start_args())
        self.assertEqual(self.terminal(controller)["outcome"], "completed")

    def test_stop_during_startup_returns_without_creating_worker_or_run(self):
        request, token = self.preview()
        result = evaluate_execution_preview(request, self.config)
        entered, release = threading.Event(), threading.Event()
        factory = unittest.mock.Mock(side_effect=AssertionError("worker forbidden"))
        controller = ExecutionController(Path(sys.executable), worker_factory=factory)
        self.addCleanup(controller.wait)
        self.addCleanup(release.set)
        def delayed(*_args):
            entered.set()
            self.assertTrue(release.wait(5))
            return result
        with patch("nano_batch.gui_execution.evaluate_execution_preview", side_effect=delayed):
            controller.start(request, self.config, token)
            self.assertTrue(entered.wait(2))
            self.assertTrue(controller.request_stop())
            self.assertFalse(controller.request_stop())
            release.set()
            self.assertEqual(self.terminal(controller)["exit_code"], 130)
        factory.assert_not_called()
        self.assertFalse(self.run.exists())

    def test_worker_rechecks_binding_after_supervisor_validation(self):
        script = process_tests.WorkerProcessTests.fake_adapter_script(None)
        original_factory = self.factory(script)
        def change_then_spawn(executable):
            self.prompts.write_text("Synthetic changed after confirmation.\n", encoding="utf-8")
            return original_factory(executable)
        controller = ExecutionController(Path(sys.executable), worker_factory=change_then_spawn)
        self.addCleanup(controller.wait)
        self.addCleanup(controller.request_stop)
        controller.start(*self._start_args())
        self.assertEqual(self.terminal(controller)["code"], "STALE_PREVIEW")
        self.assertFalse(self.run.exists())

    def test_existing_cli_owner_blocks_gui_before_adapter_or_mutation(self):
        from nano_batch.execution_lock import execution_ownership
        controller = self.controller(process_tests.WorkerProcessTests.fake_adapter_script(None))
        with execution_ownership(self.run):
            controller.start(*self._start_args())
            self.assertEqual(self.terminal(controller)["code"], "OWNERSHIP_BUSY")
        self.assertFalse(self.run.exists())

    def _start_args(self):
        request, token = self.preview()
        return request, self.config, token

    def test_supervisor_crash_corruption_and_incompatible_exit_are_not_success(self):
        ready = encode(message("ready"))
        done = encode(message("terminal", outcome="completed", exit_code=0, code=None, completed=1, total=1))
        for script in ("import os; os._exit(7)",
                       "import os; os.write(1,b'synthetic private output\\n')",
                       f"import os; os.write(1,{ready!r}+{done!r}); os._exit(7)"):
            controller = self.controller(script)
            controller.start(*self._start_args())
            result = self.terminal(controller)
            self.assertEqual(result["outcome"], "communication_lost")
            self.assertNotIn("private", repr(result))

    def test_lost_channel_does_not_release_invocation_before_process_exit(self):
        release = self.root / "synthetic release"
        script = (
            "import os,time\nfrom pathlib import Path\n"
            "os.write(1,b'synthetic malformed frame\\n')\n"
            f"release=Path({str(release)!r})\n"
            "deadline=time.monotonic()+10\n"
            "while not release.exists() and time.monotonic()<deadline: time.sleep(.005)\n"
        )
        controller = self.controller(script)
        controller.start(*self._start_args())
        try:
            until(lambda: controller.communication_lost)
            self.assertTrue(controller.active)
            self.assertFalse(any(v.kind == "terminal" for v in controller.drain()))
            controller.request_stop()
            self.assertFalse(controller.request_stop())
            with self.assertRaises(RuntimeError):
                controller.start(*self._start_args())
        finally:
            release.touch()
        self.assertEqual(self.terminal(controller)["outcome"], "communication_lost")


class FakeExecution:
    def __init__(self):
        self.active = self.communication_lost = False
        self.starts = []
        self.stops = 0
        self.messages = []

    def start(self, *args):
        self.active = True
        self.starts.append(args)

    def request_stop(self):
        self.stops += 1
        return True

    def drain(self):
        values, self.messages = self.messages, []
        return values


class ExecutingWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            if os.name == "nt":
                raise
            self.skipTest("Tk requires a graphical desktop")
        self.root.withdraw()
        self.preview = preview_tests.FakeController()
        self.execution = FakeExecution()
        self.window = PreviewWindow(self.root, controller=self.preview, execution=self.execution,
                                    config_file=Path("synthetic-config.json"))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.execution.active = False
        self.window.close()

    def ready(self, state=PreviewState.PENDING):
        self.window.prompt_file.set("synthetic prompts.txt")
        self.window.run_dir.set("synthetic run")
        self.window.preview()
        view = replace(preview_tests.synthetic_view().rows[0], state=state)
        from nano_batch.preview import PreviewView
        self.preview.messages.append(PreviewMessage(self.preview.revision, "ready", PreviewView((view,)), fingerprint=("synthetic",)))
        self.window._poll()

    def start(self):
        self.ready()
        with patch("nano_batch.preview_gui.messagebox.askyesno", return_value=True) as confirm:
            self.window.start()
        return confirm

    def test_fresh_preview_confirmed_start_and_conflicting_actions_disabled(self):
        confirm = self.start()
        self.assertEqual(len(self.execution.starts), 1)
        self.assertIn("credits", confirm.call_args.args[1])
        for widget in (*self.window._inputs, self.window.preview_button, self.window.start_button):
            self.assertTrue(widget.instate(["disabled"]))
        self.assertFalse(self.window.stop_button.instate(["disabled"]))
        self.assertTrue(self.window.chrome_button.instate(["disabled"]))
        self.assertEqual(str(self.window.chrome_button.cget("command")), "")

    def test_declined_confirmation_never_starts_worker(self):
        self.ready()
        with patch("nano_batch.preview_gui.messagebox.askyesno", return_value=False):
            self.window.start()
        self.assertEqual(self.execution.starts, [])

    def test_selection_change_during_confirmation_rejects_stale_authorization(self):
        self.ready()
        def changed(*_args, **_kwargs):
            self.window.prompt_file.set("synthetic changed input")
            return True
        with patch("nano_batch.preview_gui.messagebox.askyesno", side_effect=changed):
            self.window.start()
        self.assertEqual(self.execution.starts, [])
        self.assertIn("changed", self.window.status.get())

    def test_blocked_or_unfingerprinted_preview_cannot_start(self):
        self.ready(PreviewState.BLOCKED)
        self.assertTrue(self.window.start_button.instate(["disabled"]))
        with patch("nano_batch.preview_gui.messagebox.askyesno", side_effect=AssertionError("confirmation forbidden")):
            self.window.start()
        self.assertEqual(self.execution.starts, [])

    def test_stop_stays_pending_and_widgets_keep_responding(self):
        self.start()
        self.window.stop()
        self.window.stop_button.invoke()
        self.assertEqual(self.execution.stops, 1)
        self.assertIn("waiting", self.window.status.get())
        heartbeat = []
        self.root.after(0, lambda: heartbeat.append(True))
        self.root.update()
        self.assertEqual(heartbeat, [True])
        self.assertTrue(self.window.start_button.instate(["disabled"]))

    def test_full_order_count_is_not_worker_success(self):
        from nano_batch.progress import ProgressReporter, ProgressPhase
        from nano_batch.worker_protocol import progress_message
        self.start()
        reporter = ProgressReporter((1,))
        reporter.seed(((1, "completed"),))
        reporter.record(ProgressPhase.RETURNED)
        self.execution.messages.append(progress_message(reporter.snapshot))
        self.window._poll()
        self.assertEqual(self.window.progress["value"], 100)
        self.assertNotIn("Batch complete.", self.window.status.get())
        self.assertTrue(self.window.start_button.instate(["disabled"]))

    def test_terminal_requires_release_and_consumes_previous_preview(self):
        self.start()
        self.execution.messages.append(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
        self.window._poll()
        self.assertNotIn("Batch complete.", self.window.status.get())
        self.execution.active = False
        self.window._poll()
        self.assertIn("Worker exit verified", self.window.status.get())
        self.assertTrue(self.window.start_button.instate(["disabled"]))
        self.assertTrue(self.window.stop_button.instate(["disabled"]))

    def test_window_close_can_keep_running_or_stop_and_wait_without_abandonment(self):
        self.start()
        with patch("nano_batch.preview_gui.messagebox.askyesno", return_value=False):
            self.window.close()
        self.assertFalse(self.window._closed)
        self.assertEqual(self.execution.stops, 0)
        with patch("nano_batch.preview_gui.messagebox.askyesno", return_value=True):
            self.window.close()
        self.assertFalse(self.window._closed)
        self.assertEqual(self.execution.stops, 1)
        self.execution.active = False
        self.execution.messages.append(message("terminal", outcome="interrupted", exit_code=130, code=None, total=1, completed=0))
        self.window._poll()
        self.assertTrue(self.window._closed)

    def test_completion_during_close_confirmation_still_closes_after_exit(self):
        self.start()
        def completed(*_args, **_kwargs):
            self.execution.active = False
            self.execution.messages.append(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
            self.window._poll()  # Modal dialogs dispatch Tk events.
            return True
        with patch("nano_batch.preview_gui.messagebox.askyesno", side_effect=completed):
            self.window.close()
        self.assertTrue(self.window._closed)

    def test_waiting_blocked_failure_and_lost_outcomes_are_safe_and_actionable(self):
        for outcome, code, exit_code in (("waiting", "FLOW_USAGE_LIMIT_REACHED", 3),
                                        ("blocked", "OWNERSHIP_BUSY", 2),
                                        ("failed", "FLOW_HIGH_DEMAND", 2),
                                        ("communication_lost", "WORKER_LOST", 2)):
            self.execution.messages.append(message("terminal", outcome=outcome, exit_code=exit_code, code=code, total=1, completed=0))
            self.window._poll()
            rendered = self.window.status.get() + self.window.diagnostics.get()
            self.assertIn(code, rendered)
            self.assertNotIn("synthetic", rendered)
            self.assertNotIn("Batch complete.", rendered)

    def test_active_callback_error_requests_stop_and_preserves_polling(self):
        self.start()
        self.root.after_cancel(self.window._after)
        self.window._after = None
        self.window._callback_error(RuntimeError, RuntimeError("synthetic private"), None)
        self.assertEqual(self.execution.stops, 1)
        self.assertIsNotNone(self.window._after)
        self.assertNotIn("private", self.window.status.get())

    def test_output_action_is_explicit_and_scoped_not_automatic(self):
        with patch("nano_batch.preview_gui.output_folder", return_value=Path("synthetic output")), \
             patch("nano_batch.preview_gui.os.startfile", create=True) as open_folder:
            self.ready()
            open_folder.assert_not_called()
            self.window.open_output()
            open_folder.assert_called_once_with(str(Path("synthetic output")))

    def test_ui_message_application_requires_gui_thread(self):
        errors = []
        def wrong_thread():
            try:
                self.window.start()
            except RuntimeError:
                errors.append(True)
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(2)
        self.assertEqual(errors, [True])

    def test_untrusted_diagnostic_fields_are_rejected_without_display(self):
        from nano_batch.worker_protocol import WorkerMessage
        before = self.window.diagnostics.get()
        bad = WorkerMessage("ready", (("prompt", "synthetic private prompt"),))
        with self.assertRaises(ProtocolError):
            self.window._worker_message(bad)
        self.assertEqual(self.window.diagnostics.get(), before)
        self.assertNotIn("private", self.window.status.get())


class GuiMainCancellationTests(unittest.TestCase):
    def test_console_ctrlc_during_active_invocation_preserves_exit_130(self):
        from types import SimpleNamespace
        import nano_batch.preview_gui as ui
        execution = SimpleNamespace(active=True)
        window = SimpleNamespace(_closed=False, _close_when_done=False, execution=execution, stop=unittest.mock.Mock())
        calls = []
        def loop():
            calls.append(True)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            execution.active = False
            window._closed = True
        root = SimpleNamespace(mainloop=loop, destroy=unittest.mock.Mock())
        with patch.object(ui.tk, "Tk", return_value=root), patch.object(ui, "PreviewWindow", return_value=window):
            self.assertEqual(ui.main(), 130)
        self.assertTrue(window._close_when_done)
        window.stop.assert_called_once_with()
        self.assertEqual(len(calls), 2)

    def test_console_ctrlc_before_execution_preserves_exit_130(self):
        from types import SimpleNamespace
        import nano_batch.preview_gui as ui
        execution = SimpleNamespace(active=False)
        window = SimpleNamespace(_closed=False, execution=execution, close=unittest.mock.Mock())
        root = SimpleNamespace(mainloop=unittest.mock.Mock(side_effect=KeyboardInterrupt()))
        with patch.object(ui.tk, "Tk", return_value=root), patch.object(ui, "PreviewWindow", return_value=window):
            self.assertEqual(ui.main(), 130)
        window.close.assert_called_once_with()
