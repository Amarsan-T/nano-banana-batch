"""Tk/ttk presentation for read-only previews. No execution or browser controls."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .preview import ERROR_MESSAGES, PreviewController, PreviewRequest, PreviewState


class PreviewWindow:
    PAGE_SIZE = 100

    def __init__(self, root: tk.Tk, *, controller: PreviewController | None = None):
        self.root = root
        self.controller = controller if controller is not None else PreviewController()
        self._owner = threading.get_ident()
        self._view = None
        self._page = 0
        self._closed = False
        self._after = None
        root.title("Nano Banana Batch - Preview only")
        root.geometry("1080x610")
        root.minsize(760, 440)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._callback_error

        self.prompt_file = tk.StringVar(root)
        self.run_dir = tk.StringVar(root)
        self.mode = tk.StringVar(root, "New run (not created)")
        self.status = tk.StringVar(root, "Select a prompt file and run folder, then Preview.")
        self.summary = tk.StringVar(root, "Orders: not yet validated")
        self.page_label = tk.StringVar(root, "No plan displayed")

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="PREVIEW ONLY - No generation credits consumed. No browser interaction.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(frame, text="Prompt text file:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.prompt_file).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Choose file...", command=self.choose_prompts).grid(row=1, column=2)
        ttk.Label(frame, text="Run selection:").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Combobox(frame, textvariable=self.mode, state="readonly",
                     values=("New run (not created)", "Existing run (read only)")).grid(
                         row=2, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Open existing run...", command=self.choose_run).grid(row=2, column=2)
        ttk.Label(frame, text="Run folder path:").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.run_dir).grid(row=3, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Label(frame, text="Prompt-file defaults: Image mode | 16:9 | x1 | 1K Original size. Model selected manually in Chrome.").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=10)

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=3, sticky="w")
        self.preview_button = ttk.Button(actions, text="Preview", command=self.preview)
        self.preview_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="Start / Resume (not implemented)", state="disabled")
        self.start_button.pack(side="left", padx=10)
        self.stop_button = ttk.Button(actions, text="Stop (not implemented)", state="disabled")
        self.stop_button.pack(side="left")
        ttk.Label(frame, textvariable=self.summary, wraplength=1000).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=10)

        table = ttk.Frame(frame)
        table.grid(row=7, column=0, columnspan=3, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("order", "state", "generation", "downloads", "candidates")
        self.table = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for name, title, width in zip(columns, ("Order", "State", "Generation plan", "Download plan", "Candidates"),
                                      (55, 190, 220, 220, 240)):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=45, stretch=name != "order")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        pages = ttk.Frame(frame)
        pages.grid(row=8, column=0, columnspan=3, sticky="w", pady=8)
        self.previous_button = ttk.Button(pages, text="Previous", command=lambda: self._show_page(-1), state="disabled")
        self.previous_button.pack(side="left")
        ttk.Label(pages, textvariable=self.page_label).pack(side="left", padx=10)
        self.next_button = ttk.Button(pages, text="Next", command=lambda: self._show_page(1), state="disabled")
        self.next_button.pack(side="left")
        ttk.Label(frame, textvariable=self.status, wraplength=1000).grid(
            row=9, column=0, columnspan=3, sticky="w")
        for variable in (self.prompt_file, self.run_dir, self.mode):
            variable.trace_add("write", self._inputs_changed)
        self._after = root.after(100, self._poll)

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("Preview widgets require the GUI thread.")

    def _clear(self) -> None:
        self._view = None
        self._page = 0
        self.table.delete(*self.table.get_children())
        self.summary.set("Orders: not yet validated")
        self.page_label.set("No plan displayed")
        self.previous_button.state(["disabled"])
        self.next_button.state(["disabled"])

    def _inputs_changed(self, *_args) -> None:
        self._assert_owner()
        self.controller.invalidate()
        self._clear()
        self.status.set("Selection changed. Preview again; no execution is available.")

    def choose_prompts(self) -> None:
        self._assert_owner()
        selected = filedialog.askopenfilename(parent=self.root, title="Choose a UTF-8 prompt text file",
                                              filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        if selected:
            self.prompt_file.set(selected)

    def choose_run(self) -> None:
        self._assert_owner()
        selected = filedialog.askdirectory(parent=self.root, title="Open an existing run for Preview", mustexist=True)
        if selected:
            self.mode.set("Existing run (read only)")
            self.run_dir.set(selected)

    def preview(self) -> None:
        self._assert_owner()
        self._clear()
        self.status.set("Checking prompts, saved state and assets... Preview only.")
        self.controller.request(PreviewRequest(
            self.prompt_file.get(), self.run_dir.get(), self.mode.get() == "Existing run (read only)",
        ))

    def _show_page(self, delta: int = 0) -> None:
        self._assert_owner()
        if self._view is None:
            return
        rows = self._view.rows
        last_page = max(0, (len(rows) - 1) // self.PAGE_SIZE)
        self._page = max(0, min(last_page, self._page + delta))
        start = self._page * self.PAGE_SIZE
        self.table.delete(*self.table.get_children())
        for row in rows[start:start + self.PAGE_SIZE]:
            self.table.insert("", "end", values=(row.order, row.state.value, row.generation, row.downloads, row.candidates))
        self.page_label.set(f"Orders {start + 1}-{min(start + self.PAGE_SIZE, len(rows))} of {len(rows)}")
        self.previous_button.state(["!disabled"] if self._page else ["disabled"])
        self.next_button.state(["!disabled"] if self._page < last_page else ["disabled"])

    def _poll(self) -> None:
        self._assert_owner()
        if self._closed:
            return
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        message = self.controller.drain()
        if message is not None:
            self._clear()
            if message.kind == "ready" and message.view is not None:
                self._view = message.view
                counts = [f"{state.value}: {self._view.count(state)}" for state in PreviewState]
                self.summary.set(f"Total Orders: {len(self._view.rows)} | " + " | ".join(counts))
                self.status.set("Read-only snapshot; input/state changes invalidate it on the next background check. This plan authorizes no execution.")
                self._show_page()
            else:
                self.status.set(ERROR_MESSAGES.get(message.code, ERROR_MESSAGES["unexpected"]))
        self._after = self.root.after(100, self._poll)

    def _callback_error(self, *_private_exception_details) -> None:
        self._assert_owner()
        self.controller.invalidate()
        self._clear()
        self.status.set(ERROR_MESSAGES["unexpected"])

    def close(self) -> None:
        self._assert_owner()
        if not self._closed:
            self._closed = True
            self.controller.close()
            if self._after is not None:
                self.root.after_cancel(self._after)
            self.root.destroy()


def main() -> int:
    root = None
    window = None
    try:
        root = tk.Tk()
        window = PreviewWindow(root)
        root.mainloop()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        if sys.stderr is not None:
            print("Preview GUI unavailable. Check that Python includes Tcl/Tk and an interactive desktop is available.", file=sys.stderr)
        return 2
    finally:
        if window is not None and not window._closed:
            try:
                window.close()
            except tk.TclError:
                window.controller.close()
        elif root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
