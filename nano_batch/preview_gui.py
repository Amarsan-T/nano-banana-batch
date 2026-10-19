"""Tk/ttk presentation. Execution is supervised outside the Tk main thread."""

from __future__ import annotations

import sys
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .preview import ERROR_MESSAGES, PreviewController, PreviewRequest, PreviewState
from .gui_execution import (
    ExecutionController, authorization_digest, execution_preview_controller, output_folder,
)
from .worker_protocol import validate_message


_PHASES = {
    "validating": "Validating", "ready": "Ready", "preparing_project": "Preparing project",
    "generation_wait": "Generating", "generation_saved": "Generation saved; download pending",
    "generation_skipped": "Saved generation skipped", "automatic_retry_authorized": "Existing automatic retry",
    "download": "Downloading", "reconciling_download": "Recovering download",
    "candidate_saved": "Candidate saved", "verifying_order": "Verifying Order",
    "order_complete": "Order complete", "waiting_for_flow_limit_reset": "Waiting for Flow limit reset",
    "blocked": "Blocked", "failed": "Failure observed; cleanup pending", "interrupted": "Stop observed; cleanup pending",
    "orders_finalized": "Orders finalized; waiting for worker exit",
}
_OUTCOMES = {
    "completed": "Batch complete. Worker exit verified. Preview again before another invocation.",
    "waiting": "Waiting for Flow limit reset. Preview the same run later; no manual Retry is required.",
    "blocked": "Blocked. Inspect matching inputs and ownership with existing CLI diagnostics; no Retry was authorized.",
    "interrupted": "Stopped. Preview saved state before Resume; acknowledged generation without ownership stays blocked.",
    "failed": "Failed. Preview saved state and review the safe error code before recovery.",
    "communication_lost": "Worker communication lost. Exit observed; outcome is uncertain. Preview durable state before recovery.",
}
_GUIDANCE = {
    "OWNERSHIP_BUSY": "Another execution owns the browser or run. Wait for its cleanup; do not delete locks.",
    "OWNERSHIP_UNAVAILABLE": "OS execution ownership could not be acquired. Check local permissions; do not bypass the lock.",
    "INVALID_INPUT": "Worker startup or input validation failed. Check the selected inputs, configuration, Python environment and local permissions.",
    "FLOW_HIGH_DEMAND": "Flow reported high demand. This GUI does not authorize manual Retry; review the saved Order with the CLI.",
    "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS": "Failure attribution is ambiguous. Keep the Order blocked; do not resubmit to reconcile it.",
}


class PreviewWindow:
    PAGE_SIZE = 100

    def __init__(self, root: tk.Tk, *, controller: PreviewController | None = None,
                 execution: ExecutionController | None = None, config_file: Path | None = None):
        self.root = root
        self.config_file = config_file or Path(__file__).resolve().parents[1] / "config.json"
        self.controller = controller if controller is not None else execution_preview_controller(self.config_file)
        self.execution = execution if execution is not None else ExecutionController(Path(sys.executable))
        self._authorization = None
        self._preview_request = None
        self._terminal = None
        self._stop_requested = False
        self._close_when_done = False
        self._inputs = []
        self._owner = threading.get_ident()
        self._view = None
        self._page = 0
        self._closed = False
        self._after = None
        root.title("Nano Banana Batch")
        root.geometry("1080x750")
        root.minsize(760, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._callback_error

        self.prompt_file = tk.StringVar(root)
        self.run_dir = tk.StringVar(root)
        self.mode = tk.StringVar(root, "New run (not created)")
        self.status = tk.StringVar(root, "Select a prompt file and run folder, then Preview.")
        self.summary = tk.StringVar(root, "Orders: not yet validated")
        self.page_label = tk.StringVar(root, "No plan displayed")
        self.activity = tk.StringVar(root, "No active invocation")
        self.diagnostics = tk.StringVar(root, "Safe diagnostics: no events")

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="Preview is read-only and free. Start / Resume requires confirmation and may consume Flow credits.", wraplength=1000).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(frame, text="Prompt text file:").grid(row=1, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.prompt_file)
        entry.grid(row=1, column=1, sticky="ew", padx=8)
        chooser = ttk.Button(frame, text="Choose file...", command=self.choose_prompts)
        chooser.grid(row=1, column=2)
        self._inputs.extend((entry, chooser))
        ttk.Label(frame, text="Run selection:").grid(row=2, column=0, sticky="w", pady=8)
        self.mode_box = ttk.Combobox(frame, textvariable=self.mode, state="readonly",
                     values=("New run (not created)", "Existing run"))
        self.mode_box.grid(row=2, column=1, sticky="w", padx=8)
        chooser = ttk.Button(frame, text="Open existing run...", command=self.choose_run)
        chooser.grid(row=2, column=2)
        self._inputs.extend((self.mode_box, chooser))
        ttk.Label(frame, text="Run folder path:").grid(row=3, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.run_dir)
        entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=8)
        self._inputs.append(entry)
        ttk.Label(frame, text="Manually select Image mode | Nano Banana 2.1 (or a valid image model) | 16:9 | x1 | 1K Original size.\nKeep dedicated Chrome maximized, optionally behind other apps. Minimized Chrome is unsupported.", wraplength=1000).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=10)

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=3, sticky="w")
        self.preview_button = ttk.Button(actions, text="Preview", command=self.preview)
        self.preview_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="Start / Resume", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=10)
        self.stop_button = ttk.Button(actions, text="Stop safely", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.chrome_button = ttk.Button(actions, text="Launch Chrome (use run.bat)", state="disabled")
        self.chrome_button.pack(side="left", padx=10)
        self.output_button = ttk.Button(actions, text="Open output folder", command=self.open_output)
        self.output_button.pack(side="left")
        ttk.Label(frame, textvariable=self.summary, wraplength=1000).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=10)

        table = ttk.Frame(frame)
        table.grid(row=7, column=0, columnspan=3, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("order", "state", "generation", "downloads", "candidates")
        self.table = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for name, title, width in zip(columns, ("Order", "Preview state", "Generation plan", "Download plan", "Candidates"),
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
        ttk.Label(frame, textvariable=self.activity, wraplength=1000).grid(row=10, column=0, columnspan=3, sticky="w", pady=6)
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progress.grid(row=11, column=0, columnspan=3, sticky="ew")
        ttk.Label(frame, textvariable=self.diagnostics, wraplength=1000).grid(row=12, column=0, columnspan=3, sticky="w", pady=6)
        for variable in (self.prompt_file, self.run_dir, self.mode):
            variable.trace_add("write", self._inputs_changed)
        self._after = root.after(100, self._poll)

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("Preview widgets require the GUI thread.")

    def _clear(self) -> None:
        self._authorization = None
        self._view = None
        self._page = 0
        self.table.delete(*self.table.get_children())
        self.summary.set("Orders: not yet validated")
        self.page_label.set("No plan displayed")
        self.previous_button.state(["disabled"])
        self.next_button.state(["disabled"])
        self.start_button.state(["disabled"])
        if not self.execution.active:
            self.progress["value"] = 0
            self.activity.set("No validated execution snapshot")

    def _inputs_changed(self, *_args) -> None:
        self._assert_owner()
        self.controller.invalidate()
        self._clear()
        if not self.execution.active:
            self.status.set("Selection changed. Preview again before Start / Resume.")

    def choose_prompts(self) -> None:
        self._assert_owner()
        if self.execution.active:
            return
        selected = filedialog.askopenfilename(parent=self.root, title="Choose a UTF-8 prompt text file",
                                              filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        if selected:
            self.prompt_file.set(selected)

    def choose_run(self) -> None:
        self._assert_owner()
        if self.execution.active:
            return
        selected = filedialog.askdirectory(parent=self.root, title="Open an existing run for Preview", mustexist=True)
        if selected:
            self.mode.set("Existing run")
            self.run_dir.set(selected)

    def preview(self) -> None:
        self._assert_owner()
        if self.execution.active:
            return
        self._clear()
        self.status.set("Checking prompts, saved state and assets... Preview only.")
        self._preview_request = PreviewRequest(
            self.prompt_file.get(), self.run_dir.get(), self.mode.get() == "Existing run",
        )
        self.controller.request(self._preview_request)

    def _busy(self, active: bool) -> None:
        for widget in (*self._inputs, self.preview_button, self.output_button):
            widget.state(["disabled"] if active else ["!disabled"])
        self.mode_box.configure(state="disabled" if active else "readonly")
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"] if active and not self._stop_requested else ["disabled"])

    def start(self) -> None:
        self._assert_owner()
        if self.execution.active or self._authorization is None or self._preview_request is None:
            return
        selection, token = self._preview_request, self._authorization
        if not messagebox.askyesno("Confirm Start / Resume", "Generation consumes Google Flow credits. Existing automatic retries may make additional submissions.\n\nCompleted generations are preserved; manual Retry is never authorized here. Dedicated Chrome must already be running with manual login and settings selected.\n\nExecute this validated plan?", parent=self.root):
            return
        # Modal dialogs process Tk events. An input/staleness event during the
        # confirmation must invalidate it rather than silently choosing new input.
        if token != self._authorization or selection != self._preview_request or self.execution.active:
            self.status.set(ERROR_MESSAGES["changed"])
            return
        try:
            self.execution.start(selection, self.config_file, token)
        except Exception:
            self.status.set("Worker could not start. No execution was authorized; Preview again.")
            self._clear()
            return
        self.controller.invalidate()
        self._authorization = None
        self._terminal = None
        self._stop_requested = False
        self._busy(True)
        self.status.set("Revalidating Preview and acquiring execution ownership...")
        self.activity.set("Starting supervised worker; no completion claimed")

    def stop(self) -> None:
        self._assert_owner()
        if self.execution.active:
            self.execution.request_stop()
            self._stop_requested = True
            self.stop_button.state(["disabled"])
            self.status.set("Stop requested - waiting for the current operation and cleanup.")

    def open_output(self) -> None:
        self._assert_owner()
        if self.execution.active:
            return
        try:
            # Only an explicit user action opens the selected run's output.
            os.startfile(str(output_folder(self.run_dir.get())))
        except Exception:
            self.status.set("Output folder unavailable. Select the completed run; redirected asset folders are not opened.")

    def _worker_message(self, value) -> None:
        self._assert_owner()
        data = validate_message(value.as_dict()).as_dict()
        kind = data["type"]
        if kind == "terminal":
            self._terminal = data
            return
        if kind == "preview":
            self.activity.set(f"Total Orders: {data['total']} | Completed: {data['completed']} | Remaining: {data['total'] - data['completed']}")
        elif kind == "progress":
            if data["verified"]:
                self.progress["value"] = data["percent"] or 0
                self.activity.set(f"Order: {data['order'] or '-'} | {_PHASES[data['phase']]} | Completed: {data['completed']}/{data['total']} | Remaining: {data['total'] - data['completed']} | {data['percent'] or 0}%")
            self.diagnostics.set(f"Safe diagnostics: event={data['sequence']} phase={data['phase']} code={data['code'] or 'none'}")
        elif kind == "stop_ack":
            self.status.set("Stop requested - waiting for the current operation and cleanup.")

    def _finish_execution(self) -> None:
        data = self._terminal
        self._terminal = None
        self._busy(False)
        self._authorization = None
        self.progress["value"] = data["completed"] * 100 // data["total"] if data["total"] else 0
        if data["outcome"] == "communication_lost" or not data["total"]:
            self.activity.set("Completion counts unavailable; Preview durable state before recovery.")
        else:
            self.activity.set(f"Completed: {data['completed']}/{data['total']} | Remaining: {data['total'] - data['completed']} | {data['outcome']}")
        self.status.set(_OUTCOMES.get(data["outcome"], "Preview finished; no batch was executed."))
        if data["code"] in _GUIDANCE:
            self.status.set(_GUIDANCE[data["code"]])
        if data["code"] == "STALE_PREVIEW":
            self.status.set(ERROR_MESSAGES["changed"])
        self.diagnostics.set(f"Safe diagnostics: outcome={data['outcome']} exit={data['exit_code']} code={data['code'] or 'none'}")
        if self._close_when_done:
            self._destroy()

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
        if message is not None and not self.execution.active:
            self._clear()
            if message.kind == "ready" and message.view is not None:
                self._view = message.view
                counts = [f"{state.value}: {self._view.count(state)}" for state in PreviewState]
                self.summary.set(f"Preview snapshot | Total Orders: {len(self._view.rows)} | " + " | ".join(counts))
                completed = self._view.count(PreviewState.COMPLETED)
                total = len(self._view.rows)
                self.progress["value"] = completed * 100 // total if total else 0
                self.activity.set(f"Preview | Completed: {completed}/{total} | Remaining: {total - completed}")
                self.status.set("Preview is read-only. Start / Resume requires confirmation and fresh revalidation.")
                if message.fingerprint is not None and self._preview_request is not None and not self._view.count(PreviewState.BLOCKED):
                    self._authorization = authorization_digest(message.fingerprint, self._view)
                    self.start_button.state(["!disabled"])
                self._show_page()
            else:
                self.status.set(ERROR_MESSAGES.get(message.code, ERROR_MESSAGES["unexpected"]))
        for value in self.execution.drain():
            self._worker_message(value)
        if self.execution.active and self.execution.communication_lost:
            self.status.set("Worker communication lost. Stop pending; waiting for actual worker exit. Do not start another batch.")
        if self._terminal is not None and not self.execution.active:
            self._finish_execution()
        if not self._closed:
            self._after = self.root.after(100, self._poll)

    def _callback_error(self, *_private_exception_details) -> None:
        self._assert_owner()
        self.controller.invalidate()
        self._clear()
        if self.execution.active:
            self.stop()
        else:
            self.status.set(ERROR_MESSAGES["unexpected"])
        if not self._closed and self._after is None:
            self._after = self.root.after(100, self._poll)

    def close(self) -> None:
        self._assert_owner()
        if self._closed:
            return
        if self.execution.active:
            if not self._close_when_done and messagebox.askyesno("Batch is active", "Request a safe Stop and close after the worker exits?\n\nYes: keep supervising until cleanup finishes.\nNo: keep this window open and continue.\n\nA blocked browser operation can delay Stop; no process will be forcibly terminated.", parent=self.root):
                self._close_when_done = True
                self.stop()
            if self._close_when_done and not self.execution.active:
                self._destroy()
            return
        self._destroy()

    def _destroy(self) -> None:
        if not self._closed and not self.execution.active:
            self._closed = True
            self.controller.close()
            if self._after is not None:
                self.root.after_cancel(self._after)
            self.root.destroy()


def main() -> int:
    root = None
    window = None
    interrupted = False
    try:
        root = tk.Tk()
        window = PreviewWindow(root)
        while not window._closed:
            try:
                root.mainloop()
                if not window._closed:
                    break
            except KeyboardInterrupt:
                if not window.execution.active:
                    return 130
                interrupted = True
                window._close_when_done = True
                window.stop()
        return 130 if interrupted else 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        if sys.stderr is not None:
            print("GUI unavailable. Check that Python includes Tcl/Tk and an interactive desktop is available.", file=sys.stderr)
        return 2
    finally:
        if window is not None and not window._closed:
            if window.execution.active:
                window.execution.request_stop()
                window.execution.wait()
            try:
                window.close()
            except tk.TclError:
                window.controller.close()
        elif root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
