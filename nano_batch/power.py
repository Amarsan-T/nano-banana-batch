from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager
from typing import Callable, Iterator


ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000


class WindowsExecutionStateError(RuntimeError):
    """Raised when Windows cannot establish or clear the scoped power request."""


def _is_windows() -> bool:
    return os.name == "nt"


def _set_thread_execution_state(flags: int) -> int:
    return int(ctypes.windll.kernel32.SetThreadExecutionState(flags))


@contextmanager
def windows_execution_state_guard(
    *,
    enabled: bool = True,
    setter: Callable[[int], int] | None = None,
) -> Iterator[bool]:
    """Prevent system/display idle sleep only while a real Windows batch runs."""
    if not enabled or not _is_windows():
        yield False
        return

    set_state = setter or _set_thread_execution_state
    requested = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    if not set_state(requested):
        raise WindowsExecutionStateError(
            "Windows could not enable the scoped system/display sleep guard."
        )
    try:
        yield True
    finally:
        # ES_CONTINUOUS alone clears this thread's temporary requirements.
        restored = set_state(ES_CONTINUOUS)
        if not restored and sys.exc_info()[0] is None:
            raise WindowsExecutionStateError(
                "Windows could not clear the scoped system/display sleep guard."
            )
