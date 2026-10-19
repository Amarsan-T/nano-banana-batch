"""Real Tk widgets with synthetic inputs; no browser or existing run access."""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

import gui
from nano_batch.preview import (
    ERROR_MESSAGES, PreviewController, PreviewMessage, PreviewResult, PreviewRow,
    PreviewState, PreviewView,
)
from nano_batch.preview_gui import PreviewWindow


class FakeController:
    def __init__(self):
        self.revision = 0
        self.messages = []
        self.requests = []
        self.closed = False

    def invalidate(self):
        self.revision += 1
        self.messages.clear()
        return self.revision

    def request(self, request):
        self.requests.append(request)
        return self.invalidate()

    def drain(self):
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


def synthetic_view(total=2):
    return PreviewView(tuple(PreviewRow(i, PreviewState.PENDING, "Generate (planned)",
                                       "After generation", "v01: after generation")
                             for i in range(1, total + 1)))


class PreviewWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            if os.name == "nt":
                raise  # A broken supported Windows GUI must not become a skip.
            self.skipTest("Tk requires a graphical desktop on this platform")
        self.root.withdraw()
        self.controller = FakeController()
        self.window = PreviewWindow(self.root, controller=self.controller)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if not self.window._closed:
            self.window.close()

    def deliver(self, view):
        self.controller.messages.append(PreviewMessage(self.controller.revision, "ready", view))
        self.window._poll()

    def test_execution_requires_fresh_preview_and_stop_requires_active_invocation(self):
        for button in (self.window.start_button, self.window.stop_button):
            self.assertTrue(button.instate(["disabled"]))
            self.assertNotEqual(str(button.cget("command")), "")
            button.invoke()
        self.assertEqual(self.controller.requests, [])
        self.deliver(synthetic_view())
        self.assertTrue(self.window.start_button.instate(["disabled"]))
        self.assertTrue(self.window.stop_button.instate(["disabled"]))

    def test_file_picker_is_explicit_and_cancel_does_not_change_selection(self):
        with patch("nano_batch.preview_gui.filedialog.askopenfilename", return_value="synthetic \u732b file.txt"):
            self.window.choose_prompts()
        self.assertEqual(self.window.prompt_file.get(), "synthetic \u732b file.txt")
        revision = self.controller.revision
        with patch("nano_batch.preview_gui.filedialog.askopenfilename", return_value=""):
            self.window.choose_prompts()
        self.assertEqual(self.controller.revision, revision)

    def test_existing_run_picker_selects_read_only_mode(self):
        with patch("nano_batch.preview_gui.filedialog.askdirectory", return_value="synthetic \u0442\u0435\u0441\u0442 run") as dialog:
            self.window.choose_run()
        self.assertEqual(self.window.mode.get(), "Existing run")
        self.window.preview()
        request = self.controller.requests[-1]
        self.assertTrue(request.existing)
        self.assertEqual(request.run_dir, "synthetic \u0442\u0435\u0441\u0442 run")
        self.assertTrue(dialog.call_args.kwargs["mustexist"])

    def test_preview_is_dispatched_as_values_without_widgets(self):
        self.window.prompt_file.set("synthetic prompt file.txt")
        self.window.run_dir.set("synthetic new run")
        self.window.preview()
        request = self.controller.requests[-1]
        self.assertEqual(request.prompt_file, "synthetic prompt file.txt")
        self.assertFalse(request.existing)
        self.assertIsNone(self.window._view)

    def test_summary_and_rows_are_plan_facts_not_execution_progress(self):
        self.deliver(synthetic_view())
        self.assertIn("Total Orders: 2", self.window.summary.get())
        self.assertIn("Pending: 2", self.window.summary.get())
        self.assertEqual(len(self.window.table.get_children()), 2)
        self.assertIn("requires confirmation", self.window.status.get())

    def test_input_edits_clear_previously_displayed_results(self):
        for variable in (self.window.prompt_file, self.window.run_dir, self.window.mode):
            self.deliver(synthetic_view())
            variable.set(variable.get() + " synthetic change")
            self.assertIsNone(self.window._view)
            self.assertEqual(self.window.table.get_children(), ())
            self.assertIn("not yet validated", self.window.summary.get())

    def test_stale_or_error_message_clears_validated_counts_and_rows(self):
        for kind, code in (("stale", "changed"), ("error", "plan_invalid")):
            self.deliver(synthetic_view())
            self.controller.messages.append(PreviewMessage(1, kind, code=code))
            self.window._poll()
            self.assertEqual(self.window.table.get_children(), ())
            self.assertEqual(self.window.status.get(), ERROR_MESSAGES[code])

    def test_large_plans_are_paginated_with_bounded_widget_work(self):
        self.deliver(synthetic_view(5000))
        self.assertEqual(len(self.window.table.get_children()), 100)
        self.assertIn("1-100 of 5000", self.window.page_label.get())
        self.window.next_button.invoke()
        self.assertIn("101-200", self.window.page_label.get())
        self.assertEqual(len(self.window.table.get_children()), 100)
        self.window.previous_button.invoke()
        self.assertIn("1-100", self.window.page_label.get())

    def test_polling_retains_only_one_scheduled_callback(self):
        for _ in range(5):
            self.window._poll()
        pending = self.root.tk.call("after", "info")
        self.assertEqual(tuple(pending), (self.window._after,))

    def test_widgets_reject_updates_from_preview_thread(self):
        errors = []
        def wrong_thread():
            try:
                self.window._poll()
            except RuntimeError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=wrong_thread)
        worker.start()
        worker.join(2)
        self.assertEqual(errors, ["Preview widgets require the GUI thread."])

    def test_callback_exceptions_do_not_print_private_details(self):
        self.deliver(synthetic_view())
        with redirect_stderr(StringIO()) as errors:
            self.root.report_callback_exception(RuntimeError, RuntimeError("synthetic private browser data"), None)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(self.window.status.get(), ERROR_MESSAGES["unexpected"])
        self.assertIsNone(self.window._view)

    def test_real_event_loop_remains_responsive_during_slow_preview(self):
        entered, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
        def slow(_request):
            entered.set()
            release.wait(5)
            return PreviewResult(synthetic_view(), ())
        controller = PreviewController(evaluator=slow, fingerprint=lambda _r: ())
        self.addCleanup(controller._thread.join, 2)
        self.addCleanup(controller.close)
        self.addCleanup(release.set)
        self.window.controller = controller
        self.window.preview()
        self.assertTrue(entered.wait(2))
        self.root.after(0, heartbeat.set)
        self.root.update()
        self.assertTrue(heartbeat.is_set())
        self.assertIsNone(self.window._view)
        release.set()
        deadline = time.monotonic() + 5
        while self.window._view is None and time.monotonic() < deadline:
            self.root.update()
            threading.Event().wait(0.005)
        self.assertIsNotNone(self.window._view)
        self.assertEqual(len(self.window.table.get_children()), 2)

    def test_close_is_idempotent_and_closes_only_preview_resources(self):
        self.window.close()
        self.window.close()
        self.assertTrue(self.controller.closed)
        self.assertTrue(self.window._closed)

    def test_entry_point_import_is_passive_and_main_delegates_only_to_preview(self):
        with patch("nano_batch.preview_gui.main", return_value=0) as preview_main:
            self.assertEqual(gui.main(), 0)
        preview_main.assert_called_once_with()

    def test_startup_failure_reports_fixed_guidance_without_private_exception(self):
        with patch("nano_batch.preview_gui.tk.Tk", side_effect=tk.TclError("synthetic private desktop detail")), \
             redirect_stderr(StringIO()) as errors:
            self.assertEqual(gui.main(), 2)
        self.assertIn("GUI unavailable", errors.getvalue())
        self.assertNotIn("private", errors.getvalue())

    def test_real_entry_point_runs_and_closes_without_browser_import(self):
        script = (
            "import sys,gui\nimport nano_batch.preview_gui as ui\n"
            "original=ui.PreviewWindow\n"
            "class SyntheticWindow(original):\n"
            " def __init__(self,root):\n"
            "  root.withdraw()\n"
            "  super().__init__(root)\n"
            "  root.after(10,self.close)\n"
            "ui.PreviewWindow=SyntheticWindow\n"
            "assert gui.main()==0\n"
            "assert not any(n.startswith('playwright') or n=='nano_batch.adapters.flow' for n in sys.modules)\n"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
