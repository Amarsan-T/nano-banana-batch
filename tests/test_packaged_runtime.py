"""Packaging boundaries using disposable files, mock processes and real Tk only."""

import json
import importlib.util
import ntpath
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch import runtime_paths as paths
from nano_batch.packaged_tools import CompanionAction
from nano_batch.packaged_cli import external_program_environment
from pending_download_fixtures import pending_download_test_directory


class PackagedPathTests(unittest.TestCase):
    def setUp(self):
        directory = pending_download_test_directory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "data \u732b folder"
        self.environment = patch.dict(os.environ, {"NANO_BANANA_BATCH_DATA_DIR": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_source_defaults_and_relative_configuration_are_unchanged(self):
        with patch.object(paths, "frozen", return_value=False):
            self.assertEqual(paths.config_path(), Path("config.json"))
            self.assertEqual(paths.config_path(gui=True), paths.source_root() / "config.json")
            self.assertEqual(paths.runs_directory(), Path("runs"))
            self.assertEqual(paths.runtime_path("relative", source_default="source", packaged_default="package"), Path("relative"))
            args = app.build_parser().parse_args(["open-flow-debug"])
            self.assertEqual(args.config, "config.json")
        self.assertFalse(self.home.exists())

    def test_frozen_defaults_are_absolute_without_creating_anything(self):
        with patch.object(paths, "frozen", return_value=True):
            self.assertEqual(paths.config_path(), self.home / "config.json")
            self.assertEqual(paths.runs_directory(), self.home / "runs")
            self.assertEqual(paths.launcher_directory(), self.home)
            args = app.build_parser().parse_args(["open-flow-debug"])
            self.assertEqual(Path(args.config), self.home / "config.json")
            adapter = app._flow_session_from_args(args, {})
            self.assertEqual(adapter.profile_dir, self.home / "chrome-profile")
            self.assertEqual(adapter.diagnostics_path, self.home / "diagnostics/flow_inspect.png")
            self.assertEqual(adapter.debug_address, "127.0.0.1")
        self.assertFalse(self.home.exists())

    def test_frozen_config_paths_and_explicit_arguments_have_distinct_bases(self):
        with patch.object(paths, "frozen", return_value=True):
            values = dict(source_default="unused", packaged_default="default", config_file=self.root / "settings/config.json")
            self.assertEqual(paths.runtime_path("profile", **values), self.root / "settings/profile")
            self.assertEqual(paths.runtime_path("profile", from_argument=True, **values), Path.cwd() / "profile")
            self.assertEqual(paths.runtime_path(str(self.root), **values), self.root)

    def test_invalid_explicit_null_is_not_silently_replaced_by_a_default(self):
        for packaged in (False, True):
            with patch.object(paths, "frozen", return_value=packaged):
                with self.assertRaises(TypeError):
                    paths.runtime_path(None, source_default="source", packaged_default="packaged")
                self.assertEqual(paths.runtime_path("", source_default="source", packaged_default="packaged"), self.home if packaged else Path("."))

    def test_invalid_data_home_never_falls_back_to_current_directory(self):
        for value in ("", "relative", "bad\0value", str(Path(self.root.anchor))):
            with self.subTest(value=value), patch.object(paths.os, "environ", {"NANO_BANANA_BATCH_DATA_DIR": value}):
                with self.assertRaises(ValueError):
                    paths.data_home()

    def test_setup_is_explicit_idempotent_and_preserves_config_and_runs(self):
        with patch.object(paths, "frozen", return_value=True):
            home = paths.prepare_data_home()
            self.assertEqual(home, self.home)
            config = home / "config.json"
            parsed = json.loads(config.read_text())
            self.assertEqual(parsed["generation"]["variants"], 1)
            self.assertEqual(Path(parsed["browser_profile_dir"]), home / "chrome-profile")
            self.assertFalse((home / "chrome-profile").exists())
            self.assertFalse((home / "diagnostics").exists())
            config.write_text('{"existing":"preserve"}', encoding="utf-8")
            marker = home / "runs" / "keep.txt"
            marker.write_text("preserve")
            paths.prepare_data_home()
            self.assertEqual(config.read_text(), '{"existing":"preserve"}')
            self.assertEqual(marker.read_text(), "preserve")

    def test_source_worker_command_is_unchanged(self):
        with patch.object(paths, "frozen", return_value=False):
            command, cwd = paths.worker_launch(Path(sys.executable))
        self.assertEqual(command, [sys.executable, "-B", "-u", "-m", "nano_batch.gui_worker"])
        self.assertEqual(cwd, paths.source_root())

    def test_launcher_respects_source_entry_location_and_frozen_data_home(self):
        for packaged, expected in ((False, self.root), (True, self.home)):
            with self.subTest(packaged=packaged), patch.object(paths, "frozen", return_value=packaged), \
                 patch.object(app, "__file__", str(self.root / "app.py")), \
                 patch.object(app, "run_launcher", return_value=0) as launch:
                args = app.build_parser().parse_args(["launcher"])
                self.assertEqual(app.cmd_launcher(args), 0)
                self.assertEqual(launch.call_args.args[0], expected)

    def test_frozen_worker_never_reinvokes_the_gui_or_python_flags(self):
        gui = self.root / "NanoBananaBatch.exe"
        gui.write_bytes(b"synthetic")
        worker = self.root / "NanoBananaBatchWorker.exe"
        worker.write_bytes(b"synthetic")
        with patch.object(paths, "frozen", return_value=True):
            command, cwd = paths.worker_launch(gui)
            self.assertEqual(command, [str(worker)])
            self.assertEqual(cwd, self.root)
            worker.unlink()
            with self.assertRaises(ValueError):
                paths.worker_launch(gui)

    def test_missing_component_is_not_searched_on_path(self):
        gui = self.root / "NanoBananaBatch.exe"
        gui.write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            paths.sibling_executable(gui, "NanoBananaBatchCLI.exe")


class CompanionTests(unittest.TestCase):
    setUp = PackagedPathTests.setUp
    def test_companion_uses_fixed_arguments_private_streams_and_off_thread_launch(self):
        gui = self.root / "NanoBananaBatch.exe"
        cli = self.root / "NanoBananaBatchCLI.exe"
        gui.write_bytes(b"synthetic"); cli.write_bytes(b"synthetic")
        owner = threading.get_ident()
        calls = []
        def run(*args, **kwargs):
            calls.append((threading.get_ident(), args, kwargs))
            return subprocess.CompletedProcess(args[0], 0)
        companion = CompanionAction()
        with patch("nano_batch.packaged_tools.sys.executable", str(gui)), patch("nano_batch.packaged_tools.subprocess.run", side_effect=run):
            companion.start("chrome", self.home / "config.json")
            companion.wait()
        self.assertTrue(companion.drain())
        self.assertFalse(companion.active)
        thread, args, kwargs = calls[0]
        self.assertNotEqual(thread, owner)
        self.assertEqual(args[0], [str(cli), "open-flow-debug", "--config", str(self.home / "config.json"), "--url", "https://flow.google.com/"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual((kwargs["stdin"], kwargs["stdout"], kwargs["stderr"]), (subprocess.DEVNULL,) * 3)

    def test_unknown_action_never_starts_a_process(self):
        with patch("subprocess.run") as run, self.assertRaises(ValueError):
            CompanionAction().start("execute", "private")
        run.assert_not_called()

    def test_launch_failure_is_a_boolean_without_private_error_output(self):
        gui = self.root / "NanoBananaBatch.exe"
        cli = self.root / "NanoBananaBatchCLI.exe"
        gui.write_bytes(b"synthetic"); cli.write_bytes(b"synthetic")
        companion = CompanionAction()
        with patch("nano_batch.packaged_tools.sys.executable", str(gui)), patch("subprocess.run", side_effect=OSError("synthetic-private")):
            companion.start("output", self.root)
            with self.assertRaises(ValueError):
                companion.start("chrome", self.root)
            companion.wait()
        self.assertIs(companion.drain(), False)
        self.assertIsNone(companion.drain())


@unittest.skipUnless(os.name == "nt", "Windows frozen DLL search contract")
class DllEnvironmentTests(unittest.TestCase):
    setUp = PackagedPathTests.setUp

    def test_dll_and_path_restored_after_success_and_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                bundle = self.root / "bundle"
                old_dll = str(bundle)
                kernel = MagicMock()
                def get_directory(size, buffer):
                    if buffer is not None:
                        buffer.value = old_dll
                    return len(old_dll)
                kernel.GetDllDirectoryW.side_effect = get_directory
                kernel.SetDllDirectoryW.return_value = 1
                original_path = os.pathsep.join((str(bundle / "native"), str(self.root / "system")))
                with patch("ctypes.WinDLL", return_value=kernel), patch.object(sys, "frozen", True, create=True), \
                     patch.object(sys, "_MEIPASS", str(bundle), create=True), patch.dict(os.environ, {"PATH": original_path}):
                    try:
                        with external_program_environment():
                            self.assertEqual(os.environ["PATH"], str(self.root / "system"))
                            if failure:
                                raise RuntimeError("synthetic")
                    except RuntimeError:
                        self.assertTrue(failure)
                    self.assertEqual(os.environ["PATH"], original_path)
                    self.assertEqual([c.args for c in kernel.SetDllDirectoryW.call_args_list], [(None,), (old_dll,)])

    def test_source_mode_never_changes_dll_configuration(self):
        with patch.object(sys, "frozen", False, create=True), patch("ctypes.WinDLL") as dll:
            with external_program_environment():
                pass
        dll.assert_not_called()

    def test_unavailable_dll_reset_fails_before_external_work(self):
        kernel = MagicMock()
        kernel.GetDllDirectoryW.return_value = 0
        kernel.SetDllDirectoryW.return_value = 0
        with patch("ctypes.WinDLL", return_value=kernel), patch.object(sys, "frozen", True, create=True):
            with self.assertRaises(OSError), external_program_environment():
                self.fail("External action must not run after failed environment reset.")


class ArtifactScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        file = Path(__file__).resolve().parents[1] / "packaging/windows/audit_bundle.py"
        spec = importlib.util.spec_from_file_location("stage5b_audit", file)
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def test_exact_public_stdlib_documentation_is_allowed(self):
        self.audit.check_content(ntpath.splitroot.__doc__.encode("utf-8"))

    def test_local_home_path_and_secret_are_rejected_without_echoing(self):
        values = [("C:" + "/" + "Users" + "/PrivateExample/file").encode(), b"ghp_" + b"a" * 32]
        for value in values:
            with self.assertRaises(ValueError) as raised:
                self.audit.check_content(value)
            self.assertNotIn(value.decode(), str(raised.exception))

    def test_nested_compiled_constants_are_scanned(self):
        private = "C:" + "/" + "Users" + "/PrivateExample/file"
        code = compile("def nested():\n return " + repr(private), "synthetic.py", "exec")
        with self.assertRaises(ValueError):
            self.audit.check_code(code)


class PackagedGuiTests(unittest.TestCase):
    setUp_paths = PackagedPathTests.setUp

    def setUp(self):
        self.setUp_paths()
        import tkinter as tk
        from nano_batch.preview_gui import PreviewWindow
        from test_preview_gui import FakeController
        self.root_widget = tk.Tk()
        self.root_widget.withdraw()
        self.controller = FakeController()
        with patch("nano_batch.preview_gui.frozen", return_value=True):
            self.window = PreviewWindow(self.root_widget, controller=self.controller,
                                        config_file=self.home / "config.json")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window._companion = None
        self.window.close()

    def test_packaged_window_startup_remains_read_only_and_marked_internal(self):
        self.assertIn("INTERNAL TEST BUILD", self.root_widget.title())
        self.assertFalse(self.home.exists())
        self.assertTrue(self.window.start_button.instate(["disabled"]))
        self.assertTrue(self.window.stop_button.instate(["disabled"]))
        self.assertFalse(self.window.chrome_button.instate(["disabled"]))

    def test_setup_requires_confirmation_and_invalidates_preview(self):
        with patch("nano_batch.preview_gui.frozen", return_value=True), patch("nano_batch.preview_gui.messagebox.askyesno", return_value=False), patch("nano_batch.preview_gui.prepare_data_home") as prepare:
            self.window.prepare_data()
            prepare.assert_not_called()
        self.window._authorization = "synthetic"
        with patch("nano_batch.preview_gui.frozen", return_value=True), patch("nano_batch.preview_gui.messagebox.askyesno", return_value=True), patch("nano_batch.preview_gui.prepare_data_home") as prepare:
            self.window.prepare_data()
            prepare.assert_called_once_with()
        self.assertIsNone(self.window._authorization)
        self.assertTrue(self.window.start_button.instate(["disabled"]))

    def test_chrome_action_waits_off_ui_and_never_authorizes_execution(self):
        action = MagicMock(active=False)
        action.start.side_effect = lambda *_: setattr(action, "active", True)
        action.drain.side_effect = lambda: (setattr(action, "active", False) or True)
        self.window._companion = action
        with patch("nano_batch.preview_gui.frozen", return_value=True), patch("nano_batch.preview_gui.messagebox.askyesno", return_value=True):
            self.window.launch_chrome()
            self.assertTrue(self.window.start_button.instate(["disabled"]))
            self.assertTrue(self.window.stop_button.instate(["disabled"]))
            self.window.close()
            self.assertFalse(self.window._closed)
            self.window._poll()
        self.assertTrue(self.window._closed)
        action.start.assert_called_once_with("chrome", self.home / "config.json")

    def test_private_setup_exception_is_not_displayed(self):
        with patch("nano_batch.preview_gui.frozen", return_value=True), patch("nano_batch.preview_gui.messagebox.askyesno", return_value=True), patch("nano_batch.preview_gui.prepare_data_home", side_effect=ValueError("synthetic-private")):
            self.window.prepare_data()
        self.assertNotIn("synthetic-private", self.window.status.get())


if __name__ == "__main__":
    unittest.main()
