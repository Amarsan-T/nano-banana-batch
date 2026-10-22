"""Project-setup failures must remain bounded and never authorize submission."""

from contextlib import nullcontext
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from nano_batch.batch import load_prompts_file
from nano_batch.errors import PolicyGenerationRejected
from nano_batch.gui_worker import StopBridge, run_job
from nano_batch.progress import _error_code
from nano_batch.runner import initialize_run_state
from nano_batch.setup_diagnostics import (
    SETUP_FAILURE_CODES, SetupCategory, SetupStage, _SetupFailures,
    collect_setup_diagnostics, setup_code_details, setup_failure_code,
    setup_guidance, setup_stage,
)
from nano_batch.state import RunStateStore
from nano_batch.worker_protocol import (
    VERSION, JobMode, JobRequest, ProtocolError, decode_message, encode, message,
)
from pending_download_fixtures import pending_download_test_directory
from setup_diagnostics_fixtures import PRIVATE_DETAIL, SyntheticSetupSession


class SetupWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompts = self.root / "synthetic-input.txt"
        self.prompts.write_text("Synthetic harmless still life.\n", encoding="utf-8")
        self.config = self.root / "synthetic-config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.run = self.root / "synthetic run"
        self.stop = StopBridge()
        self.messages = []

    def execute(self, mode, *, factory=None, failure_hook=lambda: None):
        self.adapter = SyntheticSetupSession(self.root / "synthetic-profile", mode, failure_hook=failure_hook)
        request = JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config))
        with patch("nano_batch.power.windows_execution_state_guard", return_value=nullcontext()):
            return run_job(request, self.messages.append, self.stop,
                           adapter_factory=factory or (lambda *_: self.adapter)).as_dict()

    def check_failure(self, mode, code, *, project_present=True):
        result = self.execute(mode)
        self.assertEqual((result["outcome"], result["exit_code"], result["code"]), ("failed", 2, code))
        state = RunStateStore(self.run / "run_state.json").load()
        self.assertEqual(state.flow_project is not None, project_present)
        item = state.items[0]
        self.assertEqual((item.status, item.attempt_count, item.started_at, item.error), ("pending", 0, None, None))
        self.assertEqual((item.automatic_policy_retry_count, item.automatic_generic_failure_retry_count,
                          item.download_attempt_count), (0, 0, 0))
        self.assertEqual(item.candidates, [])
        self.assertEqual(item.output_files, [])
        self.assertEqual(self.adapter.calls["generate"], 0)
        rendered = repr(self.messages) + repr(result)
        for private in (PRIVATE_DETAIL, str(self.root), "Synthetic harmless", "00000000-0000-4000-8000-000000000001"):
            self.assertNotIn(private, rendered)

    def test_failure_before_project_creation(self):
        self.check_failure("project", "SETUP_PROJECT_OPERATION", project_present=False)

    def test_existing_project_reconnection_boundary_after_persistence(self):
        self.check_failure("reconnect", "SETUP_RECONNECT_OPERATION")

    def test_editor_readiness_boundary(self):
        self.check_failure("editor", "SETUP_EDITOR_OPERATION")

    def test_settings_verification_boundary(self):
        self.check_failure("settings", "SETUP_SETTINGS_INVALID_INPUT")

    def test_pre_start_cleanup_boundary(self):
        self.check_failure("cleanup", "SETUP_CLEANUP_OPERATION")

    def test_driver_startup_boundary(self):
        self.check_failure("driver", "SETUP_DRIVER_RESOURCE")

    def test_worker_initialization_failure_does_not_create_state(self):
        def factory(*_args): raise ValueError(PRIVATE_DETAIL)
        result = self.execute("unused", factory=factory)
        self.assertEqual((result["outcome"], result["exit_code"], result["code"]),
                         ("failed", 2, "SETUP_WORKER_INVALID_INPUT"))
        self.assertFalse(self.run.exists())

    def test_existing_project_failure_preserves_exact_saved_state_and_does_not_recreate(self):
        from setup_diagnostics_fixtures import PROJECT
        state = initialize_run_state(load_prompts_file(self.prompts), self.run, require_flow_project=True)
        state.flow_project = PROJECT
        store = RunStateStore(self.run / "run_state.json")
        store.save(state)
        before, stamp = store.path.read_bytes(), store.path.stat().st_mtime_ns
        self.check_failure("editor", "SETUP_EDITOR_OPERATION")
        self.assertEqual(self.adapter.calls["create"], 0)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.path.stat().st_mtime_ns, stamp)

    def test_state_persistence_error_is_observed_without_repair_or_another_create(self):
        original = RunStateStore.save
        calls = []
        def save(store, state):
            calls.append(1)
            if len(calls) == 2: raise PermissionError(PRIVATE_DETAIL)
            return original(store, state)
        with patch.object(RunStateStore, "save", save):
            self.check_failure("unused", "SETUP_PROJECT_IO", project_present=False)
        self.assertEqual(self.adapter.calls["create"], 1)
        self.assertEqual(len(calls), 2)

    def test_cancellation_wins_over_each_setup_error_without_another_operation(self):
        for index, mode in enumerate(("project", "driver", "reconnect", "editor", "settings", "cleanup")):
            with self.subTest(mode=mode):
                self.run = self.root / f"cancel-{index}"
                self.stop = StopBridge()
                result = self.execute(mode, failure_hook=self.stop.request)
                self.assertEqual((result["outcome"], result["exit_code"], result["code"]), ("interrupted", 130, None))
                self.assertEqual(self.adapter.calls["generate"], 0)
                saved = RunStateStore(self.run / "run_state.json").load()
                self.assertEqual(saved.items[0].attempt_count, 0)

    def test_existing_coded_failure_keeps_original_retry_semantics(self):
        def coded_failure(*_args): raise PolicyGenerationRejected(PRIVATE_DETAIL)
        with patch.object(SyntheticSetupSession, "_wait_for_authenticated_image_editor", coded_failure):
            result = self.execute("unused")
        self.assertEqual(result["code"], "FLOW_POLICY_REJECTED")
        item = RunStateStore(self.run / "run_state.json").load().items[0]
        self.assertEqual((item.attempt_count, item.automatic_policy_retry_count), (0, 0))
        self.assertEqual(self.adapter.calls["generate"], 0)

    def test_unknown_error_after_setup_remains_an_ordinary_generation_failure(self):
        result = self.execute("after_setup")
        self.assertEqual(result["code"], "OPERATION_FAILED")
        self.assertEqual(self.adapter.calls["generate"], 1)
        item = RunStateStore(self.run / "run_state.json").load().items[0]
        self.assertEqual((item.status, item.attempt_count, item.automatic_generic_failure_retry_count), ("failed", 1, 0))

    def test_reporting_failure_cannot_change_saved_state_or_outcome(self):
        with patch.object(_SetupFailures, "record", side_effect=RuntimeError(PRIVATE_DETAIL)):
            result = self.execute("editor")
        self.assertEqual((result["outcome"], result["exit_code"], result["code"]), ("failed", 2, "OPERATION_FAILED"))
        saved = RunStateStore(self.run / "run_state.json").load()
        self.assertEqual((saved.items[0].status, saved.items[0].attempt_count), ("pending", 0))
        self.assertEqual(self.adapter.calls["generate"], 0)

    def test_code_renderer_failure_keeps_generic_failure(self):
        with patch("nano_batch.setup_diagnostics._category", side_effect=RuntimeError(PRIVATE_DETAIL)):
            result = self.execute("settings")
        self.assertEqual((result["outcome"], result["exit_code"], result["code"]), ("failed", 2, "OPERATION_FAILED"))
        self.assertEqual(self.adapter.calls["generate"], 0)

    def test_private_exception_is_never_formatted(self):
        class PrivateError(Exception):
            def __str__(self): raise AssertionError("Do not format exception")
            def __repr__(self): raise AssertionError("Do not represent exception")
        def factory(*_args): raise PrivateError(PRIVATE_DETAIL)
        result = self.execute("unused", factory=factory)
        self.assertEqual(result["code"], "SETUP_WORKER_OPERATION")
        self.assertNotIn(PRIVATE_DETAIL, repr(result) + repr(self.messages))


class SetupObservationTests(unittest.TestCase):
    def test_finite_codes_round_trip_without_new_fields_or_protocol_version(self):
        self.assertEqual(VERSION, 1)
        self.assertEqual(len(SETUP_FAILURE_CODES), 28)
        for code in SETUP_FAILURE_CODES:
            with self.subTest(code=code):
                original = message("terminal", outcome="failed", exit_code=2, code=code, completed=0, total=1)
                decoded = decode_message(encode(original)).as_dict()
                self.assertEqual(set(decoded), {"v", "type", "outcome", "exit_code", "code", "completed", "total"})
                self.assertEqual(decoded["code"], code)
                self.assertIsNotNone(setup_guidance(code))

    def test_unknown_codes_and_extra_diagnostic_fields_still_fail_closed(self):
        with self.assertRaises(ProtocolError):
            message("terminal", outcome="failed", exit_code=2, code="SETUP_PRIVATE_UNKNOWN", completed=0, total=1)
        with self.assertRaises(ProtocolError):
            message("terminal", outcome="failed", exit_code=2, code="OPERATION_FAILED", completed=0, total=1, stage="private")
        for value in (None, [], PRIVATE_DETAIL):
            self.assertIsNone(setup_code_details(value))
            self.assertIsNone(setup_guidance(value))

    def test_cli_error_classification_does_not_trust_setup_prefixes(self):
        for code in SETUP_FAILURE_CODES:
            self.assertEqual(_error_code(RuntimeError(code + ": " + PRIVATE_DETAIL)), "OPERATION_FAILED")

    def test_nested_stage_preserves_same_exception_identity_and_innermost_boundary(self):
        error = RuntimeError(PRIVATE_DETAIL)
        with collect_setup_diagnostics():
            try:
                with setup_stage(SetupStage.PROJECT, requires_active=False):
                    with setup_stage(SetupStage.EDITOR): raise error
            except RuntimeError as caught:
                self.assertIs(caught, error)
                self.assertEqual(setup_failure_code(caught), "SETUP_EDITOR_OPERATION")
        self.assertIsNone(setup_failure_code(error))

    def test_nested_steps_cannot_classify_errors_outside_project_scope(self):
        error = RuntimeError(PRIVATE_DETAIL)
        with collect_setup_diagnostics():
            try:
                with setup_stage(SetupStage.CLEANUP): raise error
            except RuntimeError:
                self.assertIsNone(setup_failure_code(error))

    def test_recovered_or_previous_exception_cannot_label_a_later_error(self):
        error = RuntimeError(PRIVATE_DETAIL)
        with collect_setup_diagnostics():
            try:
                with setup_stage(SetupStage.PROJECT, requires_active=False): raise error
            except RuntimeError: pass
            self.assertIsNone(setup_failure_code(RuntimeError(PRIVATE_DETAIL)))

    def test_cleanup_failure_supersedes_original_without_changing_python_exception_chain(self):
        primary, cleanup = RuntimeError("synthetic-primary"), PermissionError("synthetic-cleanup")
        with collect_setup_diagnostics():
            try:
                with setup_stage(SetupStage.PROJECT, requires_active=False):
                    try:
                        with setup_stage(SetupStage.EDITOR): raise primary
                    finally:
                        with setup_stage(SetupStage.CLEANUP): raise cleanup
            except PermissionError as caught:
                self.assertIs(caught, cleanup)
                self.assertIs(caught.__context__, primary)
                self.assertEqual(setup_failure_code(caught), "SETUP_CLEANUP_IO")

    def test_categories_are_fixed_and_bounded_without_reading_error_text(self):
        cases = ((ValueError, "INVALID_INPUT"), (TypeError, "INVALID_INPUT"),
                 (ImportError, "RESOURCE"), (FileNotFoundError, "RESOURCE"),
                 (PermissionError, "IO"), (RuntimeError, "OPERATION"))
        for kind, expected in cases:
            with collect_setup_diagnostics(), self.subTest(kind=kind.__name__):
                error = kind(PRIVATE_DETAIL)
                self.assertEqual(setup_failure_code(error, worker_initializing=True), "SETUP_WORKER_" + expected)

    def test_observations_are_not_shared_with_another_thread_or_invocation(self):
        error = RuntimeError(PRIVATE_DETAIL)
        with collect_setup_diagnostics():
            try:
                with setup_stage(SetupStage.PROJECT, requires_active=False): raise error
            except RuntimeError: pass
            values = []
            thread = threading.Thread(target=lambda: values.append(setup_failure_code(error)))
            thread.start(); thread.join()
            self.assertEqual(values, [None])
        with collect_setup_diagnostics():
            self.assertIsNone(setup_failure_code(error))

    def test_keyboard_interrupt_is_not_recorded_or_replaced(self):
        first = KeyboardInterrupt()
        with collect_setup_diagnostics(), self.assertRaises(KeyboardInterrupt) as caught:
            with setup_stage(SetupStage.PROJECT, requires_active=False): raise first
        self.assertIs(caught.exception, first)

    def test_no_collection_scope_leaves_cli_calls_unchanged(self):
        error = RuntimeError(PRIVATE_DETAIL)
        with self.assertRaises(RuntimeError) as caught:
            with setup_stage(SetupStage.PROJECT, requires_active=False): raise error
        self.assertIs(caught.exception, error)
        self.assertIsNone(setup_failure_code(error))


class SetupGuiTests(unittest.TestCase):
    def test_actionable_fixed_message_and_safe_stage_category(self):
        import tkinter as tk
        from nano_batch.preview_gui import PreviewWindow
        from test_preview_gui import FakeController
        root = tk.Tk(); root.withdraw()
        window = PreviewWindow(root, controller=FakeController())
        try:
            for code in SETUP_FAILURE_CODES:
                window._worker_message(message("terminal", outcome="failed", exit_code=2, code=code, completed=0, total=1))
                window._finish_execution()
                stage, category = setup_code_details(code)
                self.assertEqual(window.status.get(), setup_guidance(code))
                rendered = window.diagnostics.get()
                self.assertIn("stage=" + stage.value, rendered)
                self.assertIn("category=" + category.value, rendered)
                self.assertIn("code=" + code, rendered)
                self.assertTrue(window.start_button.instate(["disabled"]))
                self.assertNotIn(PRIVATE_DETAIL, rendered)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
