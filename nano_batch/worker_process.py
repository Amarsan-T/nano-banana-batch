"""Private subprocess supervisor, not wired to the Preview-only GUI."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from queue import Full, Queue

from .worker_protocol import (
    MAX_FRAME, JobRequest, StopRequest, decode_message, encode, message,
)
from .worker_transport import MessageBuffer


class WorkerProcess:
    """One explicitly requested job, no retries or automatic process termination.

    Pipe threads only transport bounded validated values. Future UI consumers
    drain on their main thread; they must launch the process away from that thread.
    """

    def __init__(self, executable: Path):
        executable = Path(executable)
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("An explicit Python executable is required.")
        self._messages = MessageBuffer()
        self._outgoing = Queue(maxsize=2)  # one job, one idempotent Stop
        self._lock = threading.Lock()
        self._job_sent = False
        self._stop_sent = False
        self._terminal = False
        self._finishing = False
        self._closed = False
        self._reader_done = threading.Event()
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [str(executable), "-B", "-u", "-m", "nano_batch.gui_worker"],
            cwd=str(Path(__file__).resolve().parents[1]), shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, creationflags=flags,
        )
        self._reader = threading.Thread(target=self._read, name="worker-protocol-reader", daemon=True)
        self._writer = threading.Thread(target=self._write, name="worker-protocol-writer", daemon=True)
        self._reader.start()
        self._writer.start()

    def submit(self, request: JobRequest):
        if type(request) is not JobRequest:
            raise ValueError("Worker submission requires a typed job request.")
        frame = encode(request)
        with self._lock:
            if self._job_sent or self._stop_sent or self._terminal or self.process.poll() is not None:
                raise ValueError("Worker accepts exactly one explicit job.")
            self._outgoing.put_nowait(frame)
            self._job_sent = True

    def request_stop(self) -> bool:
        with self._lock:
            if self._stop_sent or self._terminal or self._finishing or self.process.poll() is not None:
                return False
            self._outgoing.put_nowait(encode(StopRequest()))
            self._stop_sent = True
            return True

    def _lost(self):
        with self._lock:
            if self._terminal:
                return
            self._finishing = False
        self.request_stop()
        with self._lock:
            if not self._terminal:
                self._terminal = True
                self._messages.put(message("terminal", outcome="communication_lost", exit_code=2,
                                           code="WORKER_LOST", completed=0, total=0))

    def _read(self):
        terminal = None
        ready = False
        try:
            while True:
                frame = self.process.stdout.readline(MAX_FRAME + 1)
                if not frame:
                    break
                if terminal is not None:
                    raise ValueError()
                value = decode_message(frame)
                if (not ready and value.kind != "ready") or (ready and value.kind == "ready"):
                    raise ValueError()
                ready = True
                with self._lock:
                    if self._terminal:
                        break
                    if value.kind == "terminal":
                        terminal = value
                        self._finishing = True
                    elif not self._messages.put(value):
                        raise ValueError()
            # Wait only on the reader thread. A frame before a crash or mismatched
            # process exit is insufficient evidence of successful worker cleanup.
            if terminal is not None and self.process.wait() == dict(terminal.fields)["exit_code"]:
                with self._lock:
                    if not self._terminal:
                        self._terminal = True
                        self._messages.put(terminal)
        except Exception:
            self._lost()
        finally:
            self._lost()
            self._reader_done.set()

    def _write(self):
        try:
            while True:
                frame = self._outgoing.get()
                if frame is None:
                    return
                while frame:
                    count = os.write(self.process.stdin.fileno(), frame)
                    if not count:
                        raise BrokenPipeError()
                    frame = frame[count:]
        except Exception:
            self._lost()

    def drain(self):
        """Nonblocking, bounded consumption; no widget calls or raw output."""
        values = []
        for _ in range(10):
            value = self._messages.take(timeout=0)
            if value is None:
                break
            values.append(value)
        return tuple(values)

    def wait_message(self, timeout=0.1):
        """Supervisor thread only; bounded wait, never a Tk callback."""
        return self._messages.take(timeout)

    def close(self) -> bool:
        """Release handles only after exit. Active workers receive Stop, never kill.

        False means keep supervising; a blocked SDK operation may still own the
        browser/run. A terminal message alone does not establish process exit.
        """
        if self._closed:
            return True
        if self.process.poll() is None:
            self.request_stop()
            return False
        if not self._reader_done.is_set():
            return False
        try:
            self._outgoing.put_nowait(None)
        except Full:
            return False
        self.process.stdin.close()
        self.process.stdout.close()
        self._messages.close()
        self._closed = True
        return True
