"""Synthetic Tk fields only; never use the Windows clipboard or switch layouts."""

import os
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tkinter import ttk

from nano_batch.preview_gui import PreviewWindow
from nano_batch.entry_shortcuts import EditingEntry, windows_shortcut
import test_preview_gui as gui_helpers


class EntryShortcutRegressionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            if os.name == "nt":
                raise
            self.skipTest("Tk requires a graphical desktop")
        self.root.withdraw()
        self.controller = gui_helpers.FakeController()
        self.window = PreviewWindow(self.root, controller=self.controller)
        self.addCleanup(self.window.close)
        self.entries = [widget for widget in self.window._inputs
                        if widget.winfo_class() == "TEntry"]
        # Override clipboard primitives in this disposable Tcl interpreter. No
        # test reads, clears or replaces the user's actual Windows clipboard.
        self.clipboard = "synthetic pasted value"
        self.clipboard_calls = []
        self.root.tk.call("rename", "clipboard", "test_real_clipboard")
        self.root.tk.call("rename", "::tk::GetSelection", "::tk::test_real_GetSelection")
        self.root.tk.createcommand("clipboard", self.fake_clipboard)
        self.root.tk.createcommand("::tk::GetSelection", self.fake_selection)
        self.addCleanup(self.restore_clipboard_commands)

    def fake_clipboard(self, operation, *args):
        self.clipboard_calls.append(operation)
        if operation == "clear":
            self.clipboard = ""
        elif operation == "append":
            self.clipboard += args[-1]
        else:
            raise AssertionError("Unexpected clipboard operation")
        return ""

    def fake_selection(self, _widget, kind):
        self.assertEqual(kind, "CLIPBOARD")
        self.clipboard_calls.append("get")
        return self.clipboard

    def restore_clipboard_commands(self):
        for command, original in (("clipboard", "test_real_clipboard"),
                                  ("::tk::GetSelection", "::tk::test_real_GetSelection")):
            self.root.tk.deletecommand(command)
            self.root.tk.call("rename", original, command)

    def key(self, entry, keycode, *, russian=False, state=4):
        # Model Windows Tk %k independently from layout-dependent %K/%A. No
        # OS keyboard-layout activation or synthetic system-wide key injection.
        symbols = {0x41: "Cyrillic_ef", 0x43: "Cyrillic_es", 0x56: "Cyrillic_em",
                   0x58: "Cyrillic_che", 0x59: "Cyrillic_en", 0x5A: "Cyrillic_ya"}
        event = SimpleNamespace(keycode=keycode, state=state,
                                keysym=symbols.get(keycode, "Cyrillic_a") if russian else chr(keycode),
                                char="м" if russian else chr(keycode))
        with patch.object(entry, "_windows", True):
            return entry._key(event)

    def set_text(self, entry, text):
        self.root.setvar(entry.cget("textvariable"), text)
        self.root.update_idletasks()

    def test_both_path_fields_undo_the_last_edit(self):
        self.assertEqual(len(self.entries), 2)
        for entry, variable in zip(self.entries, (self.window.prompt_file, self.window.run_dir)):
            variable.set("synthetic тест path")
            self.root.update_idletasks()
            entry.insert("end", " changed")
            entry.event_generate("<<Undo>>")
            self.assertEqual(variable.get(), "synthetic тест path")

    def test_all_six_shortcuts_in_both_layouts_and_both_path_fields(self):
        for entry in self.entries:
            for russian in (False, True):
                with self.subTest(russian=russian, field=str(entry)):
                    self.set_text(entry, "тест path")
                    self.assertEqual(self.key(entry, 0x41, russian=russian), "break")
                    self.assertEqual((entry.index("sel.first"), entry.index("sel.last")), (0, 9))
                    self.clipboard_calls.clear()
                    self.assertEqual(self.key(entry, 0x43, russian=russian), "break")
                    self.assertEqual(self.clipboard, "тест path")
                    self.assertEqual(self.clipboard_calls, ["clear", "append"])
                    self.clipboard_calls.clear()
                    self.assertEqual(self.key(entry, 0x58, russian=russian), "break")
                    self.assertEqual(entry.get(), "")
                    self.assertEqual(self.clipboard_calls, ["clear", "append"])
                    self.assertEqual(self.key(entry, 0x5A, russian=russian), "break")
                    self.assertEqual(entry.get(), "тест path")
                    self.assertEqual(self.key(entry, 0x59, russian=russian), "break")
                    self.assertEqual(entry.get(), "")
                    self.clipboard_calls.clear()
                    self.assertEqual(self.key(entry, 0x56, russian=russian), "break")
                    self.assertEqual(entry.get(), "тест path")
                    self.assertEqual(self.clipboard_calls, ["get"])

    def test_virtual_paste_replaces_selection_once_and_one_undo_restores_it(self):
        entry = self.entries[0]
        self.set_text(entry, "old path")
        entry.selection_range(0, "end")
        self.clipboard = "new файл path"
        entry.event_generate("<<Paste>>")
        self.assertEqual(entry.get(), self.clipboard)
        self.assertEqual(self.clipboard_calls, ["get"])
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "old path")
        entry.event_generate("<<Redo>>")
        self.assertEqual(entry.get(), "new файл path")

    def test_registered_tk_key_binding_stops_class_dispatch_for_both_layouts(self):
        entry = self.entries[0]
        for keysym, character in (("v", "\x16"), ("Cyrillic_em", "м"), ("??", "\x16")):
            self.set_text(entry, "")
            self.clipboard_calls.clear()
            # Supply exactly the event substitutions a native Tk key binding
            # receives, without changing the focused application or OS layout.
            values = {"%#": 1, "%k": 86, "%s": 4, "%t": 0, "%A": character,
                      "%K": keysym, "%W": str(entry), "%T": 2, "%E": 1}
            script = entry.bind("<KeyPress>")
            for field in self.root._subst_format:
                script = script.replace(field, tk._stringify(values.get(field, "??")))
            self.assertEqual(self.root.tk.call("catch", script), 3)  # TCL_BREAK
            self.assertEqual(entry.get(), self.clipboard)
            self.assertEqual(self.clipboard_calls, ["get"])

    def test_picker_updates_are_undoable_and_do_not_open_files_during_undo(self):
        self.set_text(self.entries[0], "previous.txt")
        with patch("nano_batch.preview_gui.filedialog.askopenfilename", return_value="тест 猫.txt") as dialog:
            self.window.choose_prompts()
            self.entries[0].event_generate("<<Undo>>")
            self.assertEqual(self.window.prompt_file.get(), "previous.txt")
            self.entries[0].event_generate("<<Redo>>")
            self.assertEqual(self.window.prompt_file.get(), "тест 猫.txt")
        dialog.assert_called_once()

    def test_repeated_undo_redo_and_empty_history_are_safe(self):
        entry = self.entries[0]
        for text in ("one", "two два", "three"):
            self.set_text(entry, text)
        for expected in ("two два", "one", "", ""):
            self.key(entry, 0x5A, russian=True)
            self.assertEqual(entry.get(), expected)
        for expected in ("one", "two два", "three", "three"):
            self.key(entry, 0x59)
            self.assertEqual(entry.get(), expected)

    def test_edit_after_undo_discards_redo_branch(self):
        entry = self.entries[0]
        self.set_text(entry, "first")
        self.set_text(entry, "second")
        entry.event_generate("<<Undo>>")
        entry.insert("end", " alternative")
        entry.event_generate("<<Redo>>")
        self.assertEqual(entry.get(), "first alternative")

    def test_native_unicode_input_and_selection_replacement_still_work(self):
        entry = self.entries[0]
        for character in "test тест 猫":
            self.assertIsNone(self.key(entry, 0x54, state=0))
            self.root.tk.call("ttk::entry::Insert", entry, character)
        self.assertEqual(entry.get(), "test тест 猫")
        entry.selection_range(0, "end")
        self.key(entry, 0x54, state=0)
        self.root.tk.call("ttk::entry::Insert", entry, "replacement")
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "test тест 猫")

    def test_delete_and_backspace_are_undoable(self):
        entry = self.entries[0]
        self.set_text(entry, "файл.txt")
        entry.icursor("end")
        self.root.tk.call("ttk::entry::Backspace", entry)
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "файл.txt")
        entry.delete(0, 4)
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "файл.txt")

    def test_unicode_paths_and_tcl_metacharacters_are_data(self):
        entry = self.entries[0]
        original = "synthetic/папка 猫/[exit]; $env(test)/image.txt"
        self.set_text(entry, original)
        self.key(entry, 0x41)
        self.clipboard = original + " suffix"
        self.key(entry, 0x56, russian=True)
        self.assertEqual(entry.get(), original + " suffix")
        self.key(entry, 0x5A, russian=True)
        self.assertEqual(entry.get(), original)

    def test_every_modifying_action_invalidates_preview(self):
        entry = self.entries[0]
        self.set_text(entry, "original")
        for action in ("Paste", "Cut", "Undo", "Redo"):
            self.window._view = gui_helpers.synthetic_view()
            self.window._authorization = object()
            self.window.start_button.state(["!disabled"])
            revision = self.controller.revision
            entry.selection_range(0, "end")
            entry.event_generate(f"<<{action}>>")
            self.assertGreater(self.controller.revision, revision, action)
            self.assertIsNone(self.window._view)
            self.assertIsNone(self.window._authorization)
            self.assertTrue(self.window.start_button.instate(["disabled"]))

    def test_copy_and_select_all_do_not_invalidate_preview_or_change_text(self):
        entry = self.entries[0]
        self.set_text(entry, "unchanged")
        revision = self.controller.revision
        self.key(entry, 0x41)
        self.key(entry, 0x43)
        self.assertEqual(entry.get(), "unchanged")
        self.assertEqual(self.controller.revision, revision)

    def test_disabled_widgets_do_not_edit_select_or_access_clipboard(self):
        entry = self.entries[0]
        self.set_text(entry, "protected")
        entry.state(["disabled"])
        for key in (0x41, 0x43, 0x56, 0x58, 0x59, 0x5A):
            self.key(entry, key, russian=True)
        self.assertEqual(entry.get(), "protected")
        self.assertFalse(entry.selection_present())
        self.assertEqual(self.clipboard_calls, [])

    def test_readonly_widgets_allow_copy_selection_but_never_mutation(self):
        entry = self.entries[0]
        self.set_text(entry, "protected")
        entry.state(["readonly"])
        self.key(entry, 0x41, russian=True)
        self.key(entry, 0x43, russian=True)
        for key in (0x56, 0x58, 0x59, 0x5A):
            self.key(entry, key, russian=True)
        self.assertEqual(entry.get(), "protected")
        self.assertEqual(self.clipboard, "protected")
        self.assertEqual(self.clipboard_calls, ["clear", "append"])

    def test_execution_disables_both_fields_and_shortcut_mutations(self):
        self.window._busy(True)
        for entry in self.entries:
            self.assertTrue(entry.instate(["disabled"]))
            self.key(entry, 0x56)
            self.assertEqual(entry.get(), "")
        self.assertEqual(self.clipboard_calls, [])
        self.window._busy(False)

    def test_nonwindows_retains_native_key_dispatch(self):
        entry = self.entries[0]
        with patch.object(entry, "_windows", False):
            self.assertIsNone(entry._key(SimpleNamespace(keycode=86, state=4)))
        entry.event_generate("<<Paste>>")
        self.assertEqual(entry.get(), self.clipboard)

    def test_bindings_are_widget_local_not_class_global_or_dialog_hooks(self):
        entry = self.entries[0]
        plain = ttk.Entry(self.root)
        self.addCleanup(plain.destroy)
        self.assertNotEqual(entry.bind("<KeyPress>"), "")
        self.assertEqual(plain.bind("<KeyPress>"), "")
        for widget in (self.window.table, self.window.mode_box):
            self.assertEqual(widget.bind("<KeyPress>"), "")
        self.assertEqual(self.root.bind_all("<Control-Key-v>"), "")
        self.assertEqual(plain.bind_class("TEntry", "<<Undo>>"), "")
        self.assertIn("ttk::entry::Paste", plain.bind_class("TEntry", "<<Paste>>"))

    def test_clipboard_unavailable_is_safe_and_retains_text_and_history(self):
        entry = self.entries[0]
        self.set_text(entry, "keep")
        def unavailable(*_args):
            raise tk.TclError("synthetic unavailable clipboard")
        self.root.tk.createcommand("::tk::GetSelection", unavailable)
        entry.event_generate("<<Paste>>")
        self.assertEqual(entry.get(), "keep")
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "")

    def test_empty_clipboard_and_selection_do_not_create_history(self):
        entry = self.entries[0]
        self.clipboard = ""
        for action in ("Copy", "Cut", "Paste", "Undo", "Redo"):
            entry.event_generate(f"<<{action}>>")
        self.assertEqual(entry.get(), "")
        self.assertEqual(entry._undo, [])
        self.assertEqual(entry._redo, [])

    def test_history_limits_bound_memory_and_destroy_removes_trace_and_idle(self):
        entry = self.entries[0]
        variable = self.window.prompt_file
        for index in range(entry.HISTORY_LIMIT + 12):
            self.set_text(entry, str(index))
        self.assertEqual(len(entry._undo), entry.HISTORY_LIMIT)
        for index in range(5):
            self.set_text(entry, str(index) * (entry.HISTORY_CHARACTERS // 3))
        self.assertLessEqual(sum(map(len, entry._undo)), entry.HISTORY_CHARACTERS)
        self.set_text(entry, "x" * (entry.HISTORY_CHARACTERS + 1))
        self.assertEqual(entry._undo, [])
        variable.set("pending")
        trace, idle = entry._trace, entry._idle
        entry.destroy()
        self.assertNotIn(trace, str(variable.trace_info()))
        self.assertNotIn(idle, self.root.tk.call("after", "info"))
        self.assertEqual(entry._undo, [])
        self.assertEqual(entry._redo, [])

    def test_history_is_independent_between_fields(self):
        for entry, text in zip(self.entries, ("prompts файл.txt", "new папка")):
            self.set_text(entry, text)
        self.entries[0].event_generate("<<Undo>>")
        self.assertEqual(self.entries[0].get(), "")
        self.assertEqual(self.entries[1].get(), "new папка")

    def test_undo_honors_validator_and_preserves_validation_configuration(self):
        entry = self.entries[0]
        self.set_text(entry, "old")
        self.set_text(entry, "allowed")
        command = self.root.register(lambda proposed: proposed == "allowed")
        entry.configure(validate="key", validatecommand=(command, "%P"))
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "allowed")
        self.assertEqual(entry.cget("validate"), "key")
        self.assertFalse(entry.instate(["invalid"]))
        entry.configure(validate="none", validatecommand="")
        entry.event_generate("<<Undo>>")
        self.assertEqual(entry.get(), "old")

    def test_validator_accepts_unicode_undo_and_redo(self):
        entry = self.entries[0]
        for text in ("тест", "тест path"):
            self.set_text(entry, text)
        command = self.root.register(lambda proposed: bool(proposed))
        entry.configure(validate="key", validatecommand=(command, "%P"))
        for action, text in (("Undo", "тест"), ("Redo", "тест path")):
            entry.event_generate(f"<<{action}>>")
            self.assertEqual(entry.get(), text)
            self.assertEqual(entry.cget("validate"), "key")

    def test_validation_error_rolls_back_text_history_and_validation_state(self):
        entry = self.entries[0]
        self.set_text(entry, "old")
        self.set_text(entry, "current")
        history = entry._undo.copy()
        entry.configure(validate="key", validatecommand="error {synthetic validator failure}")
        with self.assertRaises(tk.TclError):
            entry._action("Undo")
        self.assertEqual(entry.get(), "current")
        self.assertEqual(entry._undo, history)
        self.assertEqual(entry.cget("validate"), "key")
        self.assertFalse(entry._restoring)

    def test_active_state_and_preview_invalidation_are_unchanged_by_undo(self):
        entry = self.entries[1]
        self.set_text(entry, "first")
        self.set_text(entry, "second")
        with patch.object(self.window.execution, "start") as start, \
                patch.object(self.window.execution, "request_stop") as stop:
            entry.event_generate("<<Undo>>")
            entry.event_generate("<<Redo>>")
        start.assert_not_called()
        stop.assert_not_called()
        self.assertIsNone(self.window._authorization)


class WindowsShortcutMappingTests(unittest.TestCase):
    def test_windows_virtual_keys_not_character_or_ascii_control_codes(self):
        expected = {0x41: "SelectAll", 0x43: "Copy", 0x56: "Paste",
                    0x58: "Cut", 0x59: "Redo", 0x5A: "Undo"}
        for key, action in expected.items():
            for modifier in (4, 5, 6, 7, 12, 36):
                self.assertEqual(windows_shortcut(key, modifier), action)
        for key in (1, 3, 22, 24, 25, 26, 0x42, 0x44):
            self.assertIsNone(windows_shortcut(key, 4))

    def test_altgr_system_combinations_and_noncontrol_keys_are_not_intercepted(self):
        for state in (0, 1, 2, 4 | 0x10, 4 | 0x40, 4 | 0x80, 4 | 0x10000, 4 | 0x20000):
            for key in (0x41, 0x43, 0x56, 0x58, 0x59, 0x5A):
                self.assertIsNone(windows_shortcut(key, state))


if __name__ == "__main__":
    unittest.main()
