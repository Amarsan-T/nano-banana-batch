"""Private one-job worker. All browser execution stays on its main thread."""

from __future__ import annotations

import os
import sys
import threading
from contextlib import nullcontext
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

from .cancellation import authoritative_cancellation_scope, cancellation_checkpoint, interruptible_call
from .execution_lock import ExecutionBusy, ExecutionLockUnavailable, execution_ownership
from .gui_execution import StalePreview, authorization_digest, execution_fingerprint
from .preview import PreviewError, PreviewRequest, PreviewState, _paths, present_plan
from .progress import ProgressReporter, _error_code
from .worker_protocol import (
    MAX_FRAME, JobMode, JobRequest, StopRequest, decode_request, encode,
    message, preview_message, progress_message,
)
from .worker_transport import MessageBuffer


class StopBridge:
    """Control thread sets an Event only. Execution thread owns cancellation.

    No injected asynchronous exception can arrive after the operation finishes.
    A blocked SDK call can delay observation; acknowledgement means pending only.
    """

    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._finished = False

    def request(self) -> str:
        with self._lock:
            if self._finished:
                return "finished"
            duplicate = self.event.is_set()
            self.event.set()
            return "duplicate" if duplicate else "pending"

    def finish(self):
        with self._lock:
            try:
                cancellation_checkpoint("worker_completion")
            finally:
                self._finished = True


class PipeEmitter:
    def __init__(self, write, stop: StopBridge):
        self.buffer = MessageBuffer()
        self._write = write
        self._stop = stop
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._work, name="worker-output", daemon=True)
        self._thread.start()

    def offer(self, value):
        if not self.buffer.put(value):
            self._stop.request()

    def _work(self):
        try:
            while True:
                value = self.buffer.take()
                if value is None:
                    return
                self._write(encode(value))
                if value.kind == "terminal":
                    return
        except Exception:
            self._stop.request()
        finally:
            self._done.set()

    def finish(self, timeout=2.0) -> bool:
        # Only the worker's exit waits boundedly for terminal delivery. Neither
        # batch callbacks nor the Tk main thread ever wait for a pipe consumer.
        delivered = self._done.wait(timeout)
        self.buffer.close()
        return delivered


def run_job(request: JobRequest | StopRequest, offer, stop: StopBridge, *, adapter_factory=None):
    """Main-thread orchestration delegates all batch semantics to the runner."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Worker execution requires its main thread.")
    reporter = None
    total = completed = 0
    outcome, exit_code, code = "failed", 2, "OPERATION_FAILED"
    with authoritative_cancellation_scope(cooperative_stop=stop.event):
        try:
            cancellation_checkpoint("worker_dispatch")
            if isinstance(request, StopRequest):
                raise KeyboardInterrupt()
            for value in (request.prompt_file, request.run_dir, request.config_file):
                if not Path(value).is_absolute():
                    raise ValueError()
            run_dir = Path(request.run_dir)
            ownership = execution_ownership(run_dir) if request.mode == JobMode.EXECUTE else nullcontext()
            with ownership:
                cancellation_checkpoint("worker_validation")
                # Reuse the application's actual configuration and adapter
                # factory. Import here, never from the Preview-only Tk window.
                import app
                from .batch import PROMPT_WRAPPER_VARIANTS, load_prompts_file
                from .runner import execute_sequential_batch, plan_sequential_batch
                from .power import windows_execution_state_guard

                preview_request = PreviewRequest(request.prompt_file, request.run_dir, run_dir.exists())
                _paths(preview_request)
                before = execution_fingerprint(preview_request, Path(request.config_file)) if request.expected_snapshot else None
                batch = load_prompts_file(Path(request.prompt_file))
                config = app._load_config(request.config_file)
                settings = app._generation_settings(config, variants_override=PROMPT_WRAPPER_VARIANTS)
                view = present_plan(plan_sequential_batch(batch, run_dir, settings))
                if request.expected_snapshot:
                    after = execution_fingerprint(preview_request, Path(request.config_file))
                    if before != after or authorization_digest(after, view) != request.expected_snapshot:
                        raise StalePreview()
                total, completed = len(view.rows), view.count(PreviewState.COMPLETED)
                offer(preview_message(view))
                cancellation_checkpoint("worker_plan_ready")
                if request.mode == JobMode.PREVIEW:
                    outcome, exit_code, code = "preview_complete", 0, None
                elif view.count(PreviewState.BLOCKED):
                    outcome, exit_code, code = "blocked", 2, None
                else:
                    args = SimpleNamespace()
                    factory = adapter_factory or app._flow_session_from_args
                    adapter = factory(args, config)
                    reporter = ProgressReporter(tuple(item.order for item in batch.items),
                                                lambda snapshot: offer(progress_message(snapshot)))
                    with windows_execution_state_guard(enabled=isinstance(adapter, app.FlowSession)):
                        interruptible_call(
                            "worker_execution", "execute_sequential_batch", execute_sequential_batch,
                            batch, run_dir, adapter, settings,
                            manage_flow_project=isinstance(adapter, app.FlowSession), progress=reporter,
                        )
                    completed = total
                    outcome, exit_code, code = "completed", 0, None
        except KeyboardInterrupt:
            outcome, exit_code, code = "interrupted", 130, None
        except ExecutionBusy:
            outcome, exit_code, code = "blocked", 2, "OWNERSHIP_BUSY"
        except ExecutionLockUnavailable:
            outcome, exit_code, code = "blocked", 2, "OWNERSHIP_UNAVAILABLE"
        except StalePreview:
            outcome, exit_code, code = "blocked", 2, "STALE_PREVIEW"
        except PreviewError:
            outcome, exit_code, code = (
                ("blocked", 2, "STALE_PREVIEW") if isinstance(request, JobRequest) and request.expected_snapshot else
                ("failed", 2, "OPERATION_FAILED")
            )
        except Exception as exc:
            code = _error_code(exc)
            outcome, exit_code = (
                ("waiting", 3) if getattr(exc, "state_status", None) == "waiting_for_flow_limit_reset" else
                ("blocked", 2) if code in {"FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS", "CANDIDATE_UUID_OWNERSHIP_CONFLICT"} else
                ("failed", 2)
            )
        finally:
            if reporter is not None:
                completed = reporter.snapshot.counts["completed"]
            try:
                stop.finish()
            except KeyboardInterrupt:
                outcome, exit_code, code = "interrupted", 130, None
    return message("terminal", outcome=outcome, exit_code=exit_code, code=code,
                   completed=completed, total=total)


def serve(source, write, *, adapter_factory=None) -> int:
    """One bounded job slot and a control reader; all execution stays here."""
    stop = StopBridge()
    emitter = PipeEmitter(write, stop)
    jobs = Queue(maxsize=1)
    problem = threading.Event()
    problem_code = ["CHANNEL_LOST"]

    def receive():
        job_seen = False
        try:
            while True:
                frame = source.readline(MAX_FRAME + 1)
                if not frame:
                    raise EOFError()
                request = decode_request(frame)
                if isinstance(request, StopRequest):
                    emitter.offer(message("stop_ack", status=stop.request()))
                    if not job_seen:
                        jobs.put_nowait(request)
                        return
                elif job_seen:
                    raise ValueError()
                else:
                    job_seen = True
                    jobs.put_nowait(request)
        except Exception as exc:
            problem_code[0] = "CHANNEL_LOST" if isinstance(exc, EOFError) else "PROTOCOL_ERROR"
            problem.set()
            stop.request()
            if not job_seen:
                jobs.put_nowait(StopRequest())

    emitter.offer(message("ready"))
    threading.Thread(target=receive, name="worker-control", daemon=True).start()
    try:
        terminal = run_job(jobs.get(), emitter.offer, stop, adapter_factory=adapter_factory)
    except KeyboardInterrupt:
        stop.request()
        terminal = message("terminal", outcome="interrupted", exit_code=130, code=None, completed=0, total=0)
    except Exception:
        terminal = message("terminal", outcome="failed", exit_code=2, code="OPERATION_FAILED", completed=0, total=0)
    if problem.is_set():
        facts = dict(terminal.fields)
        if facts["outcome"] in {"completed", "preview_complete"}:
            facts.update(outcome="communication_lost", exit_code=2)
        terminal = message("terminal", **{**facts, "code": problem_code[0]})
    emitter.offer(terminal)
    emitter.finish()
    return dict(terminal.fields)["exit_code"]


class _SilentOutput:
    encoding = "utf-8"

    def write(self, text):
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def main() -> int:
    # A daemon control reader must not own Python's buffered stdin lock during
    # interpreter shutdown. Raw FileIO has no buffered-stream shutdown lock.
    import io
    source = io.FileIO(os.dup(sys.stdin.fileno()), "rb")
    # Keep the private pipe on its own noninheritable descriptor. Redirecting
    # Python print alone cannot contain native writes or sys.__stdout__.
    output_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(output_fd, False)
    sys.stdout = sys.stderr = _SilentOutput()
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)

    def write(frame):
        while frame:
            count = os.write(output_fd, frame)
            if not count:
                raise BrokenPipeError()
            frame = frame[count:]
    try:
        return serve(source, write)
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
