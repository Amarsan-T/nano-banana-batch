"""GUI supervision and Preview binding. No widgets, browser or new batch engine."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from .batch import PROMPT_WRAPPER_VARIANTS
from .configuration import _generation_settings, _load_config
from .preview import (
    PreviewController, PreviewError, PreviewRequest, PreviewResult, PreviewView,
    _file_fingerprint, evaluate_preview, preview_fingerprint,
)
from .worker_process import WorkerProcess
from .worker_protocol import JobMode, JobRequest, message
from .worker_transport import MessageBuffer


class StalePreview(Exception):
    pass


def execution_fingerprint(request: PreviewRequest, config_file: Path) -> tuple:
    config_file = Path(config_file)
    identity = hashlib.sha256(str(config_file.resolve()).encode("utf-8")).digest()
    config = _file_fingerprint(config_file) if config_file.exists() else None
    return preview_fingerprint(request), identity, config


def authorization_digest(fingerprint: tuple, view: PreviewView) -> str:
    # Private change-detection binding, never a credential or displayed diagnostic.
    # The plan binds asset-dependent decisions as well as prompt/state/config bytes.
    return hashlib.sha256(repr((fingerprint, view)).encode("utf-8")).hexdigest()


def evaluate_execution_preview(request: PreviewRequest, config_file: Path) -> PreviewResult:
    before = execution_fingerprint(request, config_file)
    try:
        config = _load_config(str(config_file))
        settings = _generation_settings(config, variants_override=PROMPT_WRAPPER_VARIANTS)
    except Exception:
        raise PreviewError("configuration") from None
    result = evaluate_preview(request, settings)
    after = execution_fingerprint(request, config_file)
    if before != after:
        raise PreviewError("changed")
    return PreviewResult(result.view, after)


def execution_preview_controller(config_file: Path) -> PreviewController:
    return PreviewController(
        evaluator=lambda request: evaluate_execution_preview(request, config_file),
        fingerprint=lambda request: execution_fingerprint(request, config_file),
    )


def output_folder(run_dir: str) -> Path:
    """Only the selected run, preferring its nonredirected assets folder."""
    if not run_dir.strip():
        raise ValueError()
    run = Path(run_dir).resolve(strict=True)
    if not run.is_dir() or not (run / "run_state.json").is_file():
        raise ValueError()
    assets = run / "assets"
    if assets.exists():
        resolved = assets.resolve(strict=True)
        if resolved.parent != run or not resolved.is_dir():
            raise ValueError()
        return resolved
    return run


class ExecutionController:
    """One supervised invocation; Tk only polls values and requests Stop.

    The supervisor remains alive until the worker exits and its handles close.
    Communication loss is not evidence of exit or released OS ownership.
    """

    def __init__(self, executable: Path, *, worker_factory=WorkerProcess):
        self._executable = Path(executable)
        self._factory = worker_factory
        self._lock = threading.Lock()
        self._messages = MessageBuffer()
        self._active = False
        self._problem = False
        self._stop = threading.Event()
        self._thread = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def communication_lost(self) -> bool:
        with self._lock:
            return self._problem

    def start(self, request: PreviewRequest, config_file: Path, expected: str) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("Execution is already supervised.")
            self._active = True
            self._problem = False
            self._messages = MessageBuffer()
            self._stop = threading.Event()  # Each invocation owns its Stop source.
            self._thread = threading.Thread(
                target=self._work, args=(request, Path(config_file), expected, self._stop),
                name="gui-worker-supervisor", daemon=False,
            )
            try:
                self._thread.start()
            except Exception:
                self._active = False
                raise

    def request_stop(self) -> bool:
        with self._lock:
            if not self._active or self._stop.is_set():
                return False
            self._stop.set()
            return True

    def drain(self) -> tuple:
        with self._lock:
            values = []
            for _ in range(10):
                value = self._messages.take(0)
                if value is None:
                    break
                values.append(value)
            return tuple(values)

    def wait(self) -> None:
        """Emergency main-loop unwind only; normal GUI close never blocks Tk."""
        if self._thread is not None:
            self._thread.join()

    def _work(self, request, config_file, expected, stop):
        terminal = message("terminal", outcome="failed", exit_code=2, code="OPERATION_FAILED", completed=0, total=0)
        worker = None
        try:
            if stop.is_set():
                terminal = message("terminal", outcome="interrupted", exit_code=130, code=None, completed=0, total=0)
            else:
                result = evaluate_execution_preview(request, config_file)
                if authorization_digest(result.fingerprint, result.view) != expected:
                    raise StalePreview()
                if stop.is_set():
                    terminal = message("terminal", outcome="interrupted", exit_code=130, code=None, completed=0, total=len(result.view.rows))
                else:
                    worker = self._factory(self._executable)
                    worker.submit(JobRequest(
                        JobMode.EXECUTE, str(Path(request.prompt_file).absolute()),
                        str(Path(request.run_dir).absolute()), str(config_file.absolute()),
                        expected_snapshot=expected,
                    ))
                    terminal = self._supervise(worker, stop)
        except (StalePreview, PreviewError):
            terminal = message("terminal", outcome="blocked", exit_code=2, code="STALE_PREVIEW", completed=0, total=0)
        except (OSError, ValueError):
            terminal = message("terminal", outcome="failed", exit_code=2, code="INVALID_INPUT", completed=0, total=0)
        except Exception:
            pass  # Fixed failure only; never format private exceptions.
        finally:
            if worker is not None:
                # Even submit/transport exceptions must not abandon a live child.
                if worker.process.poll() is None or not worker.close():
                    with self._lock:
                        self._problem = True
                    worker.request_stop()
                    terminal = self._supervise(worker, stop, lost=True)
            with self._lock:
                self._messages.put(terminal)
                self._active = False

    def _supervise(self, worker, stop, *, lost=False):
        terminal = None
        def consume(value):
            nonlocal terminal, lost
            if value.kind == "terminal":
                terminal = value
                if dict(value.fields)["outcome"] == "communication_lost":
                    lost = True
            elif not self._messages.put(value):
                lost = True
        while True:
            if stop.is_set() or lost:
                worker.request_stop()
            value = worker.wait_message(timeout=0.1)
            if value is not None:
                consume(value)
            if lost:
                with self._lock:
                    self._problem = True
            if worker.process.poll() is not None and worker.close():
                # Exit can precede UI consumption of ready/preview/progress.
                # Drain the bounded final mailbox before deciding the outcome.
                for value in worker.drain():
                    consume(value)
                return terminal if terminal is not None and not lost else message(
                    "terminal", outcome="communication_lost", exit_code=2,
                    code="WORKER_LOST", completed=0, total=0,
                )
