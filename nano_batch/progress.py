"""Transient, prompt-free observations. Never an authority for execution or Resume."""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Iterator, Literal, TextIO

from .cancellation import cancellation_checkpoint, cancellation_requested


class ProgressPhase(str, Enum):
    VALIDATING = "validating"
    READY = "ready"
    PROJECT = "preparing_project"
    GENERATION = "generation_wait"
    GENERATION_SAVED = "generation_saved"
    SKIP_GENERATION = "generation_skipped"
    RETRY = "automatic_retry_authorized"
    DOWNLOAD = "download"
    RECONCILE = "reconciling_download"
    CANDIDATE_SAVED = "candidate_saved"
    VERIFYING = "verifying_order"
    COMPLETE = "order_complete"
    WAITING = "waiting_for_flow_limit_reset"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    RETURNED = "orders_finalized"


Bucket = Literal["pending", "active", "completed", "waiting", "failed_or_blocked", "stopped"]
_BUCKETS = ("pending", "active", "completed", "waiting", "failed_or_blocked", "stopped")
_SAFE_CODES = frozenset({
    "FLOW_USAGE_LIMIT_REACHED", "FLOW_POLICY_REJECTED", "FLOW_UNUSUAL_ACTIVITY",
    "FLOW_GENERIC_GENERATION_FAILED", "FLOW_HIGH_DEMAND",
    "FLOW_GENERATION_NOT_SUBMITTED", "FLOW_PROJECT_IDENTITY_UNPROVEN",
    "CANDIDATE_CORRELATION_UNPROVEN", "CANDIDATE_UUID_OWNERSHIP_CONFLICT",
    "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS", "CANDIDATE_REACQUISITION_TIMEOUT",
    "PRODUCTION_WINDOW_NORMALIZATION_FAILED",
})


@dataclass(frozen=True)
class ProgressEvent:
    sequence: int
    elapsed_ms: int
    phase: ProgressPhase
    order: int | None = None
    variant: int | None = None
    attempt: int | None = None
    code: str | None = None


@dataclass(frozen=True)
class ProgressSnapshot:
    event: ProgressEvent
    orders: tuple[tuple[int, Bucket], ...]
    verified: bool = False

    @property
    def counts(self) -> dict[str, int]:
        return {bucket: sum(value == bucket for _, value in self.orders) for bucket in _BUCKETS}

    @property
    def percent(self) -> int | None:
        if not self.verified or not self.orders:
            return None
        return self.counts["completed"] * 100 // len(self.orders)


def reduce_progress(snapshot: ProgressSnapshot, event: ProgressEvent) -> ProgressSnapshot:
    """Reduce display facts only; never inspect state, files, or browser objects."""
    states = dict(snapshot.orders)
    if event.phase == ProgressPhase.COMPLETE and event.order in states:
        states[event.order] = "completed"
    elif event.phase in {ProgressPhase.INTERRUPTED, ProgressPhase.FAILED}:
        for order, bucket in states.items():
            if bucket == "active":
                states[order] = "stopped" if event.phase == ProgressPhase.INTERRUPTED else "failed_or_blocked"
    elif event.order in states and states[event.order] != "completed":
        states[event.order] = (
            "waiting" if event.phase == ProgressPhase.WAITING else
            "failed_or_blocked" if event.phase == ProgressPhase.BLOCKED else "active"
        )
    return ProgressSnapshot(
        event, tuple(states.items()), snapshot.verified and event.phase != ProgressPhase.VALIDATING,
    )


class ProgressReporter:
    """One invocation's bounded mailbox; record() never calls a consumer or does I/O.

    Terminal consumers run only at explicit outer flush points. A future GUI can
    drain snapshots instead. Neither consumer is allowed to control execution.
    """

    def __init__(self, orders: tuple[int, ...], output: Callable[[ProgressSnapshot], None] | None = None):
        self.snapshot = ProgressSnapshot(
            ProgressEvent(0, 0, ProgressPhase.VALIDATING), tuple((order, "pending") for order in orders),
        )
        self._started = time.monotonic()
        self._pending: deque[ProgressSnapshot] = deque(maxlen=64)
        self._output = output
        self.dropped_events = 0
        self.output_failed = False
        self.recording_failed = False

    def record(self, phase: ProgressPhase, *, order: int | None = None,
               variant: int | None = None, attempt: int | None = None, code: str | None = None) -> None:
        event = ProgressEvent(
            self.snapshot.event.sequence + 1, max(0, int((time.monotonic() - self._started) * 1000)),
            phase, order if type(order) is int else None,
            variant if type(variant) is int else None, attempt if type(attempt) is int else None,
            code if type(code) is str and code in _SAFE_CODES else "OPERATION_FAILED" if code else None,
        )
        self.snapshot = reduce_progress(self.snapshot, event)
        if len(self._pending) == self._pending.maxlen:
            self.dropped_events += 1
        self._pending.append(self.snapshot)

    def seed(self, orders: tuple[tuple[int, Bucket], ...]) -> None:
        self.snapshot = replace(self.snapshot, orders=orders, verified=True)
        self.record(ProgressPhase.READY)

    def drain(self) -> tuple[ProgressSnapshot, ...]:
        snapshots = tuple(self._pending)
        self._pending.clear()
        return snapshots

    def flush(self) -> None:
        if cancellation_requested():
            cancellation_checkpoint("progress_output")
        if self._output is None:
            return
        for snapshot in self.drain():
            try:
                self._output(snapshot)
            except Exception:
                # Never classify a display/pipe failure as a generation failure.
                # BaseException (especially KeyboardInterrupt) still propagates.
                self.output_failed = True
                self._output = None
                if cancellation_requested():
                    cancellation_checkpoint("progress_output")
                return
            if cancellation_requested():
                cancellation_checkpoint("progress_output")


def format_progress(snapshot: ProgressSnapshot) -> str:
    event = snapshot.event
    parts = ["PROGRESS", f"event={event.sequence}", f"elapsed_ms={event.elapsed_ms}", f"phase={event.phase.value}"]
    if event.order is not None:
        positions = {order: index for index, (order, _) in enumerate(snapshot.orders, 1)}
        parts.extend((f"order={event.order}", f"position={positions.get(event.order, 0)}/{len(snapshot.orders)}",
                      f"prompt_id=order-{event.order:04d}"))
    if snapshot.verified:
        parts.extend(f"{name}={count}" for name, count in snapshot.counts.items())
        parts.append(f"orders_complete={snapshot.counts['completed']}/{len(snapshot.orders)}")
        parts.append(f"percent={snapshot.percent}")
    else:
        parts.extend((f"total={len(snapshot.orders)}", "counts=unverified"))
    for name in ("variant", "attempt", "code"):
        value = getattr(event, name)
        if value is not None:
            parts.append(f"{name}={value}")
    return " ".join(parts)


def terminal_progress(stream: TextIO) -> Callable[[ProgressSnapshot], None]:
    """Plain append-only lines for PowerShell, redirected streams, and no TTY."""
    def write(snapshot: ProgressSnapshot) -> None:
        print(format_progress(snapshot), file=stream, flush=True)
    return write


_CURRENT: ContextVar[ProgressReporter | None] = ContextVar("nano_batch_progress", default=None)


def record_progress(phase: ProgressPhase, *, order: int | None = None,
                    variant: int | None = None, attempt: int | None = None, code: str | None = None) -> None:
    reporter = _CURRENT.get()
    if reporter is not None:
        try:
            reporter.record(phase, order=order, variant=variant, attempt=attempt, code=code)
        except Exception:
            reporter.recording_failed = True
            _CURRENT.set(None)


def flush_progress() -> None:
    reporter = _CURRENT.get()
    if reporter is not None:
        reporter.flush()


def seed_progress(orders: tuple[tuple[int, Bucket], ...]) -> None:
    reporter = _CURRENT.get()
    if reporter is not None:
        try:
            reporter.seed(orders)
        except Exception:
            reporter.recording_failed = True
            _CURRENT.set(None)


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if type(code) is str and code in _SAFE_CODES:
        return code
    # Some existing adapter exceptions expose only a fixed prefix. Retain that
    # prefix iff allowlisted; never retain the detail or invoke arbitrary __str__.
    message = exc.args[0] if exc.args else None
    prefix = message[:80].partition(":")[0] if type(message) is str else None
    return prefix if prefix in _SAFE_CODES else "OPERATION_FAILED"


@contextmanager
def progress_scope(reporter: ProgressReporter | None) -> Iterator[None]:
    token = _CURRENT.set(reporter)
    try:
        yield
    except KeyboardInterrupt:
        record_progress(ProgressPhase.INTERRUPTED)  # No fresh I/O during cancellation cleanup.
        raise
    except Exception as exc:
        if reporter is not None:
            try:
                code = _error_code(exc)
                record_progress(
                    ProgressPhase.INTERRUPTED if cancellation_requested() else
                    ProgressPhase.WAITING if getattr(exc, "state_status", None) == "waiting_for_flow_limit_reset" else
                    ProgressPhase.BLOCKED if code in {"FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS", "CANDIDATE_UUID_OWNERSHIP_CONFLICT"} else
                    ProgressPhase.FAILED,
                    order=reporter.snapshot.event.order, code=code,
                )
            except Exception:
                reporter.recording_failed = True
                _CURRENT.set(None)
        if not cancellation_requested():
            flush_progress()
        raise
    else:
        record_progress(ProgressPhase.INTERRUPTED if cancellation_requested() else ProgressPhase.RETURNED)
        if not cancellation_requested():
            flush_progress()
    finally:
        _CURRENT.reset(token)
