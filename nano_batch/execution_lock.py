"""Process-held execution ownership. No runtime state or stale-lock deletion."""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from functools import wraps
from pathlib import Path


_HELD: ContextVar[tuple | None] = ContextVar("execution_ownership", default=None)
_SESSION = "dedicated-browser-v1"


class ExecutionBusy(RuntimeError):
    def __init__(self):
        super().__init__("Another Nano Banana Batch execution owns the browser or run. Wait for its cleanup; do not delete locks.")


class ExecutionLockUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Execution ownership could not be established. No execution was authorized.")


def run_lock_key(run_dir: Path) -> str:
    # resolve follows Windows junctions; normcase covers case aliases. The global
    # browser gate also serializes aliases not resolved to the same path spelling.
    canonical = os.path.normcase(str(Path(run_dir).resolve()))
    return "run-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def _os_lock(key: str):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateMutexW(None, False, "Global\\NanoBananaBatch-" + key)
        if not handle:
            raise ExecutionLockUnavailable()
        owned = False
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result == 0x102:
                raise ExecutionBusy()
            if result not in (0, 0x80):  # WAIT_OBJECT_0 / abandoned owner process
                raise ExecutionLockUnavailable()
            owned = True
            yield
        finally:
            if owned:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    elif os.name == "posix":
        import fcntl
        import stat
        import tempfile

        # Local, per-user advisory fallback. Files remain; presence is never an
        # ownership test. Do not unlink even after process death.
        directory = Path(tempfile.gettempdir()) / f"nano-banana-locks-{os.getuid()}"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ExecutionLockUnavailable()
        fd = os.open(directory / key, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ExecutionLockUnavailable()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExecutionBusy() from None
            yield
        finally:
            os.close(fd)
    else:
        raise ExecutionLockUnavailable()


@contextmanager
def execution_ownership(run_dir: Path | None = None):
    """Serialize all browser execution, plus the selected run, without creating it.

    Nested proven runner calls reuse ownership on the same thread. The coarse
    session gate intentionally covers all profiles/ports, not just a guessed CDP
    spelling. OS ownership ends after normal unwind or owner process/thread exit.
    """
    keys = (_SESSION,) if run_dir is None else (_SESSION, run_lock_key(run_dir))
    owner = (os.getpid(), threading.get_ident())
    inherited = _HELD.get()
    held = inherited[2] if inherited is not None and inherited[:2] == owner else frozenset()
    with ExitStack() as stack:
        try:
            for key in keys:
                if key not in held:
                    stack.enter_context(_os_lock(key))
        except OSError:
            raise ExecutionLockUnavailable() from None
        token = _HELD.set((*owner, held.union(keys)))
        try:
            yield
        finally:
            _HELD.reset(token)


def exclusive_run(function):
    """Runner mutation boundary; the established run_dir is the second argument."""
    @wraps(function)
    def owned(*args, **kwargs):
        run_dir = args[1] if len(args) > 1 else kwargs["run_dir"]
        with execution_ownership(run_dir):
            return function(*args, **kwargs)
    return owned


def exclusive_cli(function):
    """Hold browser ownership across the entire CLI command, including cleanup."""
    @wraps(function)
    def owned(args):
        if getattr(args, "preview", False) or getattr(args, "dry_run", False):
            return function(args)
        with execution_ownership():
            return function(args)
    return owned
