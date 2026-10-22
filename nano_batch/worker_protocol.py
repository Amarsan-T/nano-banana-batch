"""Private, versioned JSON-lines protocol. Only allowlisted diagnostic facts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum

from .preview import PreviewState, PreviewView
from .progress import ProgressPhase, ProgressSnapshot, _SAFE_CODES
from .setup_diagnostics import SETUP_FAILURE_CODES


VERSION = 1
MAX_FRAME = 8192
_MAX_NUMBER = 2**53 - 1
_CODES = _SAFE_CODES | SETUP_FAILURE_CODES | {
    "OPERATION_FAILED", "INVALID_INPUT", "PROTOCOL_ERROR", "CHANNEL_LOST",
    "WORKER_LOST", "OWNERSHIP_BUSY", "OWNERSHIP_UNAVAILABLE", "STALE_PREVIEW",
}
_COUNTS = {"pending", "active", "completed", "waiting", "failed_or_blocked", "stopped"}
_PREVIEW_COUNTS = {"pending", "completed", "download_only", "finalization_pending", "blocked", "waiting", "retry"}
_OUTCOMES = {"completed": 0, "preview_complete": 0, "waiting": 3, "blocked": 2, "interrupted": 130,
             "failed": 2, "communication_lost": 2}


class ProtocolError(ValueError):
    def __init__(self):
        super().__init__("Invalid private worker message.")


class JobMode(str, Enum):
    PREVIEW = "preview"
    EXECUTE = "execute"


@dataclass(frozen=True)
class JobRequest:
    mode: JobMode
    prompt_file: str = field(repr=False)
    run_dir: str = field(repr=False)
    config_file: str = field(repr=False)
    expected_snapshot: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class StopRequest:
    pass


@dataclass(frozen=True)
class WorkerMessage:
    kind: str
    fields: tuple[tuple[str, object], ...] = ()

    def as_dict(self) -> dict:
        return {"v": VERSION, "type": self.kind, **dict(self.fields)}


def _number(value, *, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not int or not 0 <= value <= _MAX_NUMBER:
        raise ProtocolError()


def _keys(data, expected):
    if set(data) != {"v", "type"} | expected:
        raise ProtocolError()


def _validate_message(data: dict) -> WorkerMessage:
    _header(data)
    kind = data["type"]
    if kind == "ready":
        _keys(data, set())
    elif kind == "preview":
        _keys(data, {"total"} | _PREVIEW_COUNTS)
        for name in {"total"} | _PREVIEW_COUNTS:
            _number(data[name])
        if sum(data[name] for name in _PREVIEW_COUNTS) != data["total"]:
            raise ProtocolError()
    elif kind == "progress":
        _keys(data, _COUNTS | {"total", "verified", "percent", "sequence", "elapsed_ms", "phase", "order", "variant", "attempt", "code"})
        for name in _COUNTS | {"total", "sequence", "elapsed_ms"}:
            _number(data[name])
        for name in ("order", "variant", "attempt", "percent"):
            _number(data[name], nullable=True)
        if data["phase"] not in {phase.value for phase in ProgressPhase} or type(data["verified"]) is not bool:
            raise ProtocolError()
        if data["variant"] not in (None, 1, 2) or data["order"] == 0 or data["attempt"] == 0:
            raise ProtocolError()
        if sum(data[name] for name in _COUNTS) != data["total"]:
            raise ProtocolError()
        expected = data["completed"] * 100 // data["total"] if data["verified"] and data["total"] else None
        if data["percent"] != expected:
            raise ProtocolError()
        _code(data["code"])
    elif kind == "stop_ack":
        _keys(data, {"status"})
        if data["status"] not in {"pending", "duplicate", "finished"}:
            raise ProtocolError()
    elif kind == "terminal":
        _keys(data, {"outcome", "exit_code", "code", "completed", "total"})
        for name in ("completed", "total", "exit_code"):
            _number(data[name])
        if data["outcome"] not in _OUTCOMES or data["exit_code"] != _OUTCOMES[data["outcome"]] or data["completed"] > data["total"]:
            raise ProtocolError()
        if data["outcome"] == "completed" and data["completed"] != data["total"]:
            raise ProtocolError()
        if data["outcome"] in {"completed", "preview_complete"} and data["code"] is not None:
            raise ProtocolError()
        _code(data["code"])
    else:
        raise ProtocolError()
    return WorkerMessage(kind, tuple((key, value) for key, value in data.items() if key not in {"v", "type"}))


def validate_message(data: dict) -> WorkerMessage:
    try:
        return _validate_message(data)
    except (TypeError, KeyError, ValueError, RecursionError):
        raise ProtocolError() from None


def _code(value):
    if value is not None and (type(value) is not str or value not in _CODES):
        raise ProtocolError()


def _header(data):
    if type(data) is not dict or type(data.get("v")) is not int or data["v"] != VERSION or type(data.get("type")) is not str:
        raise ProtocolError()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _decode(frame: bytes):
    if type(frame) is not bytes or len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ProtocolError()
    try:
        return json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def decode_message(frame: bytes) -> WorkerMessage:
    try:
        return validate_message(_decode(frame))
    except (TypeError, KeyError):
        raise ProtocolError() from None


def _decode_request(frame: bytes) -> JobRequest | StopRequest:
    data = _decode(frame)
    _header(data)
    if data["type"] == "stop":
        _keys(data, set())
        return StopRequest()
    optional = {"expected_snapshot"} if "expected_snapshot" in data else set()
    _keys(data, {"mode", "prompt_file", "run_dir", "config_file"} | optional)
    expected = data.get("expected_snapshot")
    if optional and (type(expected) is not str or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise ProtocolError()
    if data["type"] != "job" or data["mode"] not in {mode.value for mode in JobMode}:
        raise ProtocolError()
    for name in ("prompt_file", "run_dir", "config_file"):
        value = data[name]
        if type(value) is not str or not value or len(value) > 2048 or any(char in value for char in "\0\r\n"):
            raise ProtocolError()
    return JobRequest(JobMode(data["mode"]), data["prompt_file"], data["run_dir"], data["config_file"], expected)


def decode_request(frame: bytes) -> JobRequest | StopRequest:
    try:
        return _decode_request(frame)
    except (TypeError, KeyError, ValueError, RecursionError):
        raise ProtocolError() from None


def encode(value: WorkerMessage | JobRequest | StopRequest) -> bytes:
    if isinstance(value, WorkerMessage):
        data = validate_message(value.as_dict()).as_dict()
    elif isinstance(value, JobRequest):
        if type(value.mode) is not JobMode:
            raise ProtocolError()
        data = {"v": VERSION, "type": "job", "mode": value.mode.value,
                "prompt_file": value.prompt_file, "run_dir": value.run_dir, "config_file": value.config_file}
        if value.expected_snapshot is not None:
            data["expected_snapshot"] = value.expected_snapshot
    elif isinstance(value, StopRequest):
        data = {"v": VERSION, "type": "stop"}
    else:
        raise ProtocolError()
    frame = (json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    if len(frame) > MAX_FRAME:
        raise ProtocolError()
    if not isinstance(value, WorkerMessage):
        decode_request(frame)
    return frame


def message(kind: str, **fields) -> WorkerMessage:
    return validate_message({"v": VERSION, "type": kind, **fields})


def preview_message(view: PreviewView) -> WorkerMessage:
    names = ("pending", "completed", "download_only", "finalization_pending", "blocked", "waiting", "retry")
    return message("preview", total=len(view.rows), **{name: view.count(state) for name, state in zip(names, PreviewState)})


def progress_message(snapshot: ProgressSnapshot) -> WorkerMessage:
    event = snapshot.event
    return message("progress", **snapshot.counts, total=len(snapshot.orders),
                   verified=snapshot.verified, percent=snapshot.percent,
                   sequence=event.sequence, elapsed_ms=event.elapsed_ms, phase=event.phase.value,
                   order=event.order, variant=event.variant, attempt=event.attempt, code=event.code)
