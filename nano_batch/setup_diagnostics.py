"""Transient setup observations. No I/O, exception text, or execution authority."""

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from functools import wraps


class SetupStage(str, Enum):
    WORKER = "worker_initialization"
    PROJECT = "project_preparation"
    DRIVER = "driver_startup"
    RECONNECT = "project_reconnection"
    EDITOR = "editor_readiness"
    SETTINGS = "settings_verification"
    CLEANUP = "pre_start_cleanup"


class SetupCategory(str, Enum):
    INVALID_INPUT = "invalid_input"
    RESOURCE = "resource_unavailable"
    IO = "io_error"
    OPERATION = "operation_failed"


# Exact finite values in the existing code field; never derive text from an error.
SETUP_CODE_DETAILS = {
    f"SETUP_{stage.name}_{category.name}": (stage, category)
    for stage in SetupStage for category in SetupCategory
}
SETUP_FAILURE_CODES = frozenset(SETUP_CODE_DETAILS)
_GUIDANCE = {
    SetupStage.WORKER: "Worker setup failed. Check configuration and the complete application folder.",
    SetupStage.PROJECT: "Project setup failed. Keep the run unchanged and record the diagnostic code before recovery.",
    SetupStage.DRIVER: "Browser driver setup failed. Check the complete application folder; keep the run unchanged.",
    SetupStage.RECONNECT: "Project reconnection failed. Check dedicated Chrome and one Flow tab; keep the run unchanged.",
    SetupStage.EDITOR: "Flow editor readiness failed. Check manual sign-in and the saved project; keep the run unchanged.",
    SetupStage.SETTINGS: "Flow settings could not be verified. Check Image mode, 16:9 and x1 manually; Preview checks local data only.",
    SetupStage.CLEANUP: "Setup cleanup failed. Keep the run unchanged and record the diagnostic code before recovery.",
}


def setup_code_details(code):
    return SETUP_CODE_DETAILS.get(code) if type(code) is str else None


def setup_guidance(code):
    details = setup_code_details(code)
    return _GUIDANCE[details[0]] if details else None


def _category(error):
    if isinstance(error, (ImportError, FileNotFoundError)):
        return SetupCategory.RESOURCE
    if isinstance(error, (TypeError, ValueError)):
        return SetupCategory.INVALID_INPUT
    if isinstance(error, OSError):
        return SetupCategory.IO
    return SetupCategory.OPERATION


class _SetupFailures:
    def __init__(self):
        self.depth = 0
        self.error = self.stage = None

    def record(self, stage, error):
        # Keep the innermost boundary for this exact escaping exception. A new
        # cleanup exception supersedes the previous one just as normal Python does.
        if self.error is not error:
            self.error, self.stage = error, stage


_CURRENT = ContextVar("setup_diagnostics", default=None)


@contextmanager
def collect_setup_diagnostics():
    token = None
    try:
        token = _CURRENT.set(_SetupFailures())
    except Exception:
        pass  # Observations cannot make an operation fail.
    try:
        yield
    finally:
        if token is not None:
            try:
                _CURRENT.reset(token)
            except Exception:
                pass


@contextmanager
def setup_stage(stage, *, requires_active=True):
    observation = None
    try:
        current = _CURRENT.get()
        if type(stage) is SetupStage and current is not None and (current.depth or not requires_active):
            current.depth += 1
            observation = current
    except Exception:
        pass
    try:
        yield
    except Exception as error:
        if observation is not None:
            try:
                observation.record(stage, error)
            except Exception:
                pass
        raise  # Same exception, traceback, cancellation and retry semantics.
    finally:
        if observation is not None:
            observation.depth -= 1


def setup_step(stage, *, requires_active=True):
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            with setup_stage(stage, requires_active=requires_active):
                return function(*args, **kwargs)
        return observed
    return decorate


def setup_failure_code(error, *, worker_initializing=False):
    """Only the worker's generic-error fallback uses this; no exception mutation."""
    try:
        observation = _CURRENT.get()
        stage = observation.stage if observation is not None and observation.error is error else (
            SetupStage.WORKER if worker_initializing else None
        )
        return f"SETUP_{stage.name}_{_category(error).name}" if type(stage) is SetupStage else None
    except Exception:
        return None
