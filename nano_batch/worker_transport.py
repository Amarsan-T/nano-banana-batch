"""Bounded worker diagnostics. Slow readers never block batch execution."""

from __future__ import annotations

import threading
from collections import deque

from .worker_protocol import WorkerMessage


class MessageBuffer:
    """Eight controls, one replaceable progress snapshot, one terminal result."""

    def __init__(self):
        self._condition = threading.Condition()
        self._controls: deque[WorkerMessage] = deque()
        self._progress = None
        self._terminal = None
        self._finished = False
        self._closed = False
        self.coalesced = 0

    def put(self, value: WorkerMessage) -> bool:
        with self._condition:
            if self._closed or self._finished:
                return False
            if value.kind == "terminal":
                self._terminal = value
                self._finished = True
                self._progress = None
            elif value.kind == "progress":
                self.coalesced += self._progress is not None
                self._progress = value
            else:
                if value.kind == "stop_ack":
                    self._controls = deque(item for item in self._controls if item.kind != "stop_ack")
                if len(self._controls) >= 8:
                    return False
                self._controls.append(value)
            self._condition.notify()
            return True

    def take(self, timeout: float | None = None) -> WorkerMessage | None:
        with self._condition:
            self._condition.wait_for(lambda: self._controls or self._progress or self._terminal or self._closed, timeout)
            if self._controls:
                return self._controls.popleft()
            if self._progress is not None:
                value, self._progress = self._progress, None
                return value
            if self._terminal is not None:
                value, self._terminal = self._terminal, None
                return value
            return None

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
