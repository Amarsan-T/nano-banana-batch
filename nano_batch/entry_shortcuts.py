"""Widget-local path editing; no hooks, layout changes, logging or persistence."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


_VIRTUAL_KEYS = {0x41: "SelectAll", 0x43: "Copy", 0x56: "Paste",
                 0x58: "Cut", 0x59: "Redo", 0x5A: "Undo"}


def windows_shortcut(keycode: int, state: int) -> str | None:
    """Windows Tk %k is a virtual key, while %K/%A depend on the layout.

    Keep Shift/Caps/Num/Scroll Lock compatible. Exclude Alt/AltGr/Meta/Super
    combinations, including Tk's synthetic Alt/Meta masks.
    """
    if not state & 0x4 or state & (0x10 | 0x40 | 0x80 | 0x10000 | 0x20000):
        return None
    return _VIRTUAL_KEYS.get(keycode)


class EditingEntry(ttk.Entry):
    """Entry with native clipboard actions and bounded, in-memory Undo/Redo.

    Only the two GUI path fields use this class. StringVar traces still notify
    Preview on every change. Idle coalescing makes a native selection replacement
    (delete + insert) one edit; each new key/virtual action closes the prior edit.
    File-picker updates are undoable too. Nothing is written to disk.
    """

    HISTORY_LIMIT = 100
    HISTORY_CHARACTERS = 131072

    def __init__(self, master, *, textvariable: tk.StringVar, **kwargs):
        super().__init__(master, textvariable=textvariable, **kwargs)
        self._variable = textvariable
        self._last_text = textvariable.get()
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._pending: str | None = None
        self._idle = None
        self._restoring = False
        self._windows = self.tk.call("tk", "windowingsystem") == "win32"
        self._trace = textvariable.trace_add("write", self._changed)
        self.bind("<KeyPress>", self._key)
        for action in ("Copy", "Paste", "Cut", "Undo", "Redo", "SelectAll"):
            self.bind(f"<<{action}>>", lambda _event, name=action: self._action(name))
        self.bind("<Destroy>", self._dispose)

    def _remember(self, stack: list[str], text: str) -> None:
        stack.append(text)
        while len(stack) > self.HISTORY_LIMIT or sum(map(len, stack)) > self.HISTORY_CHARACTERS:
            del stack[0]

    def _changed(self, *_args) -> None:
        text = self._variable.get()
        previous, self._last_text = self._last_text, text
        if self._restoring or previous == text:
            return
        if self._pending is None:
            self._pending = previous
            self._idle = self.after_idle(self._finish_edit)

    def _finish_edit(self) -> None:
        if self._idle is not None:
            self.after_cancel(self._idle)
            self._idle = None
        before, self._pending = self._pending, None
        current = self._variable.get()
        if before is None or before == current:
            return
        self._redo.clear()
        if max(len(before), len(current)) > self.HISTORY_CHARACTERS:
            self._undo.clear()
        else:
            self._remember(self._undo, before)

    def _key(self, event):
        self._finish_edit()
        action = windows_shortcut(event.keycode, event.state) if self._windows else None
        if action is not None:
            return self._action(action)
        return None  # Ordinary Unicode input/navigation retains Tk's class bindings.

    def _action(self, action: str) -> str:
        self._finish_edit()
        if self.instate(["disabled"]) or (
                self.instate(["readonly"]) and action not in ("Copy", "SelectAll")):
            return "break"
        if action in ("Undo", "Redo"):
            source, destination = (self._undo, self._redo) if action == "Undo" else (self._redo, self._undo)
            if source:
                self._restore(source, destination)
        elif action == "SelectAll":
            self.selection_range(0, "end")
            self.icursor("end")
        else:
            # Native Tk clipboard handling includes empty/unavailable clipboard
            # behavior. Dispatch once, then stop the original class key binding.
            self.tk.call(f"ttk::entry::{action}", self._w)
            self._finish_edit()
        return "break"

    def _restore(self, source: list[str], destination: list[str]) -> None:
        current, target = self._variable.get(), source[-1]
        validation, invalid = self.cget("validate"), self.instate(["invalid"])
        accepted = False
        self._restoring = True
        try:
            self._variable.set(target)
            # TEntry does not validate textvariable writes automatically. Honor
            # any attached validator without disabling it or losing history on
            # rejection/error. These GUI fields currently validate in the planner.
            if not self.validate() or self._variable.get() != target:
                return
            accepted = True
            source.pop()
            self._remember(destination, current)
            self.selection_clear()
            self.icursor("end")
            self.xview("end")
        finally:
            try:
                if not accepted:
                    self._variable.set(current)
                    self.configure(validate=validation)
                    self.state(["invalid" if invalid else "!invalid"])
            finally:
                self._restoring = False

    def _dispose(self, _event) -> None:
        self._variable.trace_remove("write", self._trace)
        if self._idle is not None:
            self.after_cancel(self._idle)
            self._idle = None
        self._undo.clear()
        self._redo.clear()
        self._pending = None
        self._last_text = ""
