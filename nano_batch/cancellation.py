from __future__ import annotations

import re
import signal
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import FrameType
from typing import Any, Callable, Iterator, TypeVar


_SAFE_PHASE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CURRENT_CANCELLATION: ContextVar[CancellationState | None] = ContextVar(
    "nano_batch_current_cancellation",
    default=None,
)
_T = TypeVar("_T")


def _safe_phase(value: str) -> str:
    return value if _SAFE_PHASE.fullmatch(value) else "unknown"


@dataclass
class CancellationState:
    """One batch invocation's privacy-safe, first-signal cancellation latch."""

    current_phase: str = "batch_setup"
    requested: bool = False
    received_phase: str | None = None
    cleanup_started: bool = False
    cleanup_completed: bool = False
    recovery_menu_reached: bool = False
    cleanup_error_type: str | None = None
    interrupt_signal_count: int = 0
    cancellation_latched_at_phase: str | None = None
    blocking_operation: str | None = None
    blocking_operation_started: bool = False
    blocking_operation_exited: bool = False
    keyboard_interrupt_propagated: bool = False

    def checkpoint(self, phase: str) -> None:
        safe_phase = _safe_phase(phase)
        if not self.requested:
            self.current_phase = safe_phase
            return
        raise self.interrupt()

    def request(self) -> KeyboardInterrupt:
        self.interrupt_signal_count += 1
        if not self.requested:
            self.requested = True
            self.received_phase = self.current_phase
            self.cancellation_latched_at_phase = self.current_phase
        return self.interrupt()

    def interrupt(self) -> KeyboardInterrupt:
        self.keyboard_interrupt_propagated = True
        exc = KeyboardInterrupt()
        exc.cancellation_requested = True
        exc.cancellation_received_phase = self.received_phase or self.current_phase
        exc.cancellation_state = self
        exc.cancellation_diagnostics = self.diagnostics()
        return exc

    def annotate(self, exc: KeyboardInterrupt) -> None:
        if not self.requested:
            self.requested = True
            self.received_phase = self.current_phase
            self.cancellation_latched_at_phase = self.current_phase
        if self.interrupt_signal_count == 0:
            self.interrupt_signal_count = 1
        self.keyboard_interrupt_propagated = True
        exc.cancellation_requested = True
        exc.cancellation_received_phase = self.received_phase or self.current_phase
        exc.cancellation_state = self
        exc.cancellation_diagnostics = self.diagnostics()

    def diagnostics(self) -> dict[str, Any]:
        """Return bounded, privacy-safe facts about the active cancellation."""
        return {
            "interrupt_signal_count": self.interrupt_signal_count,
            "interrupt_phase": self.received_phase or self.current_phase,
            "cancellation_latched_at_phase": self.cancellation_latched_at_phase,
            "blocking_operation": self.blocking_operation,
            "blocking_operation_started": self.blocking_operation_started,
            "blocking_operation_exited": self.blocking_operation_exited,
            "keyboard_interrupt_propagated": self.keyboard_interrupt_propagated,
            "recovery_menu_reached": self.recovery_menu_reached,
        }


def current_cancellation_state() -> CancellationState | None:
    return _CURRENT_CANCELLATION.get()


def cancellation_checkpoint(phase: str) -> None:
    state = current_cancellation_state()
    if state is not None:
        state.checkpoint(phase)


def cancellation_requested() -> bool:
    state = current_cancellation_state()
    return bool(state is not None and state.requested)


def annotate_keyboard_interrupt(exc: KeyboardInterrupt) -> None:
    state = current_cancellation_state()
    if state is not None:
        state.annotate(exc)


def mark_cleanup_started() -> None:
    state = current_cancellation_state()
    if state is not None:
        state.cleanup_started = True


def mark_cleanup_completed(*, error: BaseException | None = None) -> None:
    state = current_cancellation_state()
    if state is not None:
        state.cleanup_completed = True
        if error is not None:
            state.cleanup_error_type = type(error).__name__


def mark_recovery_menu_reached(exc: KeyboardInterrupt) -> None:
    state = getattr(exc, "cancellation_state", None)
    if isinstance(state, CancellationState):
        state.recovery_menu_reached = True
        exc.cancellation_diagnostics = state.diagnostics()


@contextmanager
def cancellation_blocking_operation(
    phase: str,
    operation: str,
) -> Iterator[None]:
    """Make a latched SIGINT authoritative across one blocking operation.

    Playwright can translate an interrupt into a transport exception, and a
    synchronous operation can also return normally after the signal handler has
    latched cancellation.  In both cases the original cancellation wins as soon
    as that one operation exits.  Non-cancellation behavior is unchanged.
    """

    state = current_cancellation_state()
    if state is None:
        yield
        return

    safe_phase = _safe_phase(phase)
    safe_operation = _safe_phase(operation)
    previous_phase = state.current_phase
    previous_operation = state.blocking_operation
    previous_started = state.blocking_operation_started
    previous_exited = state.blocking_operation_exited
    state.checkpoint(safe_phase)
    state.current_phase = safe_phase
    state.blocking_operation = safe_operation
    state.blocking_operation_started = True
    state.blocking_operation_exited = False
    try:
        yield
    except KeyboardInterrupt as exc:
        state.blocking_operation_exited = True
        state.annotate(exc)
        raise
    except BaseException as exc:
        state.blocking_operation_exited = True
        if state.requested:
            interrupt = state.interrupt()
            interrupt.cancellation_translated_error_type = type(exc).__name__
            interrupt.cancellation_diagnostics = state.diagnostics()
            raise interrupt from exc
        raise
    else:
        state.blocking_operation_exited = True
        state.checkpoint(safe_phase)
    finally:
        if not state.requested:
            state.current_phase = previous_phase
            state.blocking_operation = previous_operation
            state.blocking_operation_started = previous_started
            state.blocking_operation_exited = previous_exited


def interruptible_call(
    phase: str,
    operation: str,
    callback: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Invoke one call under the shared first-SIGINT propagation boundary."""

    with cancellation_blocking_operation(phase, operation):
        return callback(*args, **kwargs)


def format_cancellation_diagnostics(exc: KeyboardInterrupt) -> str | None:
    """Render one concise prompt-free cancellation trace for the launcher."""

    state = getattr(exc, "cancellation_state", None)
    if not isinstance(state, CancellationState):
        return None
    facts = state.diagnostics()
    return "\n".join(
        (
            "CTRL+C",
            f"  signal: {facts['interrupt_signal_count']}",
            f"  phase: {facts['interrupt_phase']}",
            f"  blocking operation: {facts['blocking_operation'] or 'none'}",
            "  cancellation latched: "
            + ("yes" if facts["cancellation_latched_at_phase"] else "no"),
            "  blocking call exited: "
            + ("yes" if facts["blocking_operation_exited"] else "no"),
            "  keyboard interrupt propagated: "
            + ("yes" if facts["keyboard_interrupt_propagated"] else "no"),
            "  recovery menu reached: "
            + ("yes" if facts["recovery_menu_reached"] else "no"),
        )
    )


@contextmanager
def authoritative_cancellation_scope() -> Iterator[CancellationState]:
    """Make the first SIGINT both latch cancellation and raise immediately."""

    existing = current_cancellation_state()
    if existing is not None:
        yield existing
        return

    state = CancellationState()
    token = _CURRENT_CANCELLATION.set(state)
    installed_handler = False
    previous_handler = None

    def handle_sigint(_signum: int, _frame: FrameType | None) -> None:
        raise state.request()

    try:
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, handle_sigint)
            installed_handler = True
        yield state
    finally:
        if installed_handler:
            signal.signal(signal.SIGINT, previous_handler)
        _CURRENT_CANCELLATION.reset(token)
