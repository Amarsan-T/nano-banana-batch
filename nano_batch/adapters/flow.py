from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import socket
import statistics
import subprocess
import time
import warnings
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from ..cancellation import (
    annotate_keyboard_interrupt,
    cancellation_blocking_operation,
    cancellation_checkpoint,
    cancellation_requested,
    interruptible_call,
    mark_cleanup_completed,
    mark_cleanup_started,
)
from ..errors import (
    CANDIDATE_CORRELATION_UNPROVEN,
    FLOW_PROJECT_CREATE_CONTROL_UNPROVEN,
    FLOW_PROJECT_CREATION_FAILED,
    FLOW_PROJECT_IDENTITY_UNPROVEN,
    FLOW_PROJECT_REOPEN_FAILED,
    FLOW_GENERATION_NOT_SUBMITTED,
    FLOW_USAGE_LIMIT_REACHED,
    GENERATION_INTERRUPT_EVIDENCE_SCHEMA,
    GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT,
    GENERATION_SUBMISSION_NOT_SUBMITTED,
    GenericGenerationFailure,
    HighDemandGenerationFailure,
    GenerationPreflightError,
    PolicyGenerationRejected,
    WAITING_FOR_FLOW_LIMIT_RESET,
    TemporaryGenerationBlock,
    UnusualActivityGenerationFailure,
)
from ..models import (
    BatchItem,
    BrowserDownloadResult,
    CandidateRecord,
    DiscoveredCandidate,
    FlowProjectReference,
    GenerationResult,
    GenerationSettings,
)


class FlowAutomationError(RuntimeError):
    """Raised when the visible Flow UI cannot be handled confidently."""


class FlowFailureAttributionAmbiguous(FlowAutomationError):
    """Terminal attribution uncertainty, not a confirmed generation failure."""

    def __init__(self, detail: str = (
        "visible failure evidence could not be distinguished from historical content."
    )) -> None:
        super().__init__(
            f"FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS: {detail} Generation ownership "
            "is unresolved; explicit manual Retry is required."
        )


class FlowProfileLockedError(FlowAutomationError):
    """Raised when another Chrome process owns the dedicated profile."""


class FlowDebugUnavailableError(FlowAutomationError):
    """Raised when the local Chrome debugging endpoint cannot be reached."""


class FlowTabNotFoundError(FlowAutomationError):
    """Raised when attached Chrome has no configured Flow project tab."""


class FlowTabAmbiguousError(FlowAutomationError):
    """Raised when more than one tab could be the configured Flow project."""


class FlowUsageLimitReached(TemporaryGenerationBlock, FlowAutomationError):
    """Raised only from the current submitted generation's visible failure."""

    def __init__(self) -> None:
        super().__init__(
            "The current submitted Flow generation reports that the usage limit "
            "has been reached; "
            "wait until Flow usage becomes available and run the same command again.",
            code=FLOW_USAGE_LIMIT_REACHED,
            state_status=WAITING_FOR_FLOW_LIMIT_RESET,
            attempt_activated=True,
        )


class FlowPolicyRejected(PolicyGenerationRejected, FlowAutomationError):
    """Raised only for the exact current-attempt Flow policy rejection card."""

    def __init__(self) -> None:
        PolicyGenerationRejected.__init__(
            self,
            "Flow rejected the current generation because it might violate its "
            "policies. This generation was not charged. Edit the prompt or retry "
            "after resolving the issue.",
        )


class FlowUnusualActivity(UnusualActivityGenerationFailure, FlowAutomationError):
    """Raised only for the exact current-attempt unusual-activity card."""

    def __init__(self) -> None:
        UnusualActivityGenerationFailure.__init__(
            self,
            "Flow rejected the current generation for unusual activity. This "
            "generation was not charged.",
        )


class FlowGenericGenerationFailed(GenericGenerationFailure, FlowAutomationError):
    """Raised only for the exact current-attempt generic generation failure."""

    def __init__(self) -> None:
        GenericGenerationFailure.__init__(
            self,
            "Flow reported that the current image failed to generate. This "
            "generation was not charged.",
        )


class FlowHighDemand(HighDemandGenerationFailure, FlowAutomationError):
    """Raised only for the exact current-attempt High Demand failure card."""

    def __init__(self) -> None:
        HighDemandGenerationFailure.__init__(
            self,
            "Flow is experiencing high demand. This generation was not charged. "
            "Choose Retry to try the same Order again manually.",
        )


class FlowGenerationPreflightError(GenerationPreflightError, FlowAutomationError):
    """A Flow UI/configuration failure proven to occur before Start activation."""

    def __init__(self, message: str) -> None:
        GenerationPreflightError.__init__(
            self,
            message,
            code=FLOW_GENERATION_NOT_SUBMITTED,
        )


class _IntentionalCancellationTransportNoiseFilter(logging.Filter):
    """Drop only asyncio's known closed-transport warning during Ctrl+C cleanup."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == "asyncio"
            and record.levelno == logging.WARNING
            and record.getMessage() == "socket.send() raised exception."
        )


DEFAULT_FLOW_URL = "https://flow.google.com"
FLOW_HOME_URL = "https://flow.google.com/"
DEFAULT_DEBUG_ADDRESS = "127.0.0.1"
DEFAULT_DEBUG_PORT = 9222
_PROFILE_LOCK_MARKERS = ("SingletonLock", "SingletonCookie", "SingletonSocket")
_REQUIRED_BACKGROUND_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion",
)
_CALCULATE_NATIVE_WIN_OCCLUSION = "calculatenativewinocclusion"
_TRACE_ALLOWED_HOSTS = {"flow.google.com", "flow-content.google"}
_TRACE_EVENT_LIMIT = 250
_PROJECT_UI_RECON_MAX_CANDIDATES = 50
_PROJECT_UI_RECON_MAX_DIALOG_CONTROLS = 25
_DOWNLOAD_LAYOUT_MAX_CANDIDATES = 10
_MODEL_MENU_RECON_MAX_CONTROLS = 30
_CANDIDATE_HISTORY_SEARCH_MAX_STEPS = 80
_CANDIDATE_HISTORY_SEARCH_TIMEOUT_SECONDS = 15.0
_CANDIDATE_HISTORY_RENDER_SETTLE_MS = 125
_PROJECT_IDENTITY_POLL_ATTEMPTS = 120
_PROJECT_IDENTITY_POLL_INTERVAL_MS = 100
_EDITOR_READINESS_TIMEOUT_SECONDS = 20.0
_EDITOR_READINESS_POLL_INTERVAL_MS = 350
_PRODUCTION_WINDOW_VERIFY_ATTEMPTS = 20
_PRODUCTION_WINDOW_VERIFY_INTERVAL_MS = 100
_POST_WINDOW_RECOVERY_SETTLE_TIMEOUT_SECONDS = 5.0
_POST_WINDOW_RECOVERY_SETTLE_INTERVAL_MS = 150
_COMPOSER_GENERATION_SETTLE_TIMEOUT_MS = 4_000
_COMPOSER_GENERATION_SETTLE_INTERVAL_MS = 150
_COMPOSER_GENERATION_CONTROL_LIMIT = 30
_GENERATION_SUBMIT_GEOMETRY_SAMPLE_COUNT = 4
_GENERATION_SUBMIT_GEOMETRY_SAMPLE_INTERVAL_MS = 100
_GENERATION_SUBMIT_GEOMETRY_TIMEOUT_MS = 2_000
_GENERATION_START_ACK_TIMEOUT_MS = 5_000
_GENERATION_START_ACK_POLL_INTERVAL_MS = 100
_POST_START_ACK_TRACE_LIMIT = 16
_LATENCY_SAMPLE_LIMIT = 128
_ACK_SLOW_POLL_LIMIT = 5
_OBSERVER_READ_STAGES = (
    "state_lookup", "retained_container_scan", "container_connectivity",
    "style_read", "geometry_read", "visibility_test", "failure_detect",
    "aria_label_read", "inner_text_read", "text_content_read", "normalization",
    "signature_match", "failure_record", "return_payload",
)

# No generation endpoint is verified by this repository's existing code/evidence.
# Only an independently verified, exact sanitized pattern may enter this table.
_GENERATION_NETWORK_VERIFIED_ENDPOINTS: frozenset[tuple[str, str]] = frozenset()
_GENERATION_NETWORK_RPC_NAMES = frozenset({"generateImages", "batchGenerateImages"})
_GENERATION_NETWORK_PATH_WORDS = frozenset({"v1", "v1beta", "v1beta1", "projects", "images", "media", "flowMedia"})
_GENERATION_NETWORK_SAFE_MIME_TYPES = frozenset({
    "application/json", "text/plain", "text/html", "text/event-stream",
    "application/octet-stream", "application/protobuf", "application/x-protobuf",
    "application/grpc", "application/grpc-web", "application/grpc-web+proto",
    "application/grpc-web-text", "image/jpeg", "image/png", "image/webp",
})


def _generation_network_endpoint_fingerprint(url: Any) -> str | None:
    """Hash transient host/path only; never retain or return the canonical input."""
    if not isinstance(url, str) or len(url) > 8000:
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host:
        return None
    # Preserve path case, escaping, dynamic segments, and the empty path exactly.
    return hashlib.sha256((host + "\n" + parts.path).encode("utf-8")).hexdigest()[:12]


def _generation_network_endpoint(url: Any) -> tuple[str, str, str] | None:
    """Never retain a URL: fixed public words only, unknown segments/hosts redacted."""
    if not isinstance(url, str) or len(url) > 8000:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    safe_host = host if host in {"flow.google.com", "labs.google"} else (
        "*.googleapis.com" if host.endswith(".googleapis.com") else "[other-host]"
    )
    words = []
    rpc = None
    segments = parts.path.split("/")[1:]
    for segment in segments[:8]:
        owner, colon, name = segment.rpartition(":")
        if colon and name in _GENERATION_NETWORK_RPC_NAMES:
            rpc = name
            words.append((owner if owner in _GENERATION_NETWORK_PATH_WORDS else "[redacted]") + ":" + name)
        else:
            words.append(segment if segment in _GENERATION_NETWORK_PATH_WORDS else "[redacted]")
    if len(segments) > 8:
        words.append("[remaining-path]")
    path = "/" + "/".join(words)
    verified = (host, path) in _GENERATION_NETWORK_VERIFIED_ENDPOINTS
    evidence = "verified_endpoint" if verified else "image_generation_rpc_semantics_unverified" if rpc else "post_fetch_xhr_only"
    return safe_host, path, evidence


class _GenerationNetworkTiming:
    """Attempt-local metadata sink; handlers never issue RPCs or retain CDP payloads."""

    def __init__(self, cdp, clock=time.perf_counter):
        self.cdp, self.clock = cdp, clock
        self.started = clock()
        self.events: list[dict[str, Any]] = []
        self.event_count = 0
        self.truncated = False
        self.accepting = True
        self.listening = False
        self.network_enabled = False
        self.metrics: dict[str, float] = {}
        self.status = "armed"
        self._requests: dict[str, dict[str, Any]] = {}
        self._listeners: list[tuple[str, Any]] = []
        # Ordinary function, not a bound built-in, for Playwright callback wrapping.
        def handler(event):
            self.record(event)
        self.handler = handler
        def response_handler(event):
            self.record_response(event)
        def finish_handler(event):
            self.record_response(event, finished=True)
        self.response_handler, self.finish_handler = response_handler, finish_handler

    def record(self, event: Any) -> None:
        if not self.accepting or not isinstance(event, dict):
            return
        received = self.clock()
        if received - self.started > 30 or self.event_count >= 1000:
            self.truncated = True
            self.accepting = False
            return
        self.event_count += 1
        request_id = event.get("requestId")
        pairable = isinstance(request_id, str) and 0 < len(request_id) <= 256
        if pairable:
            # Redirect hops can reuse an ID, including a non-candidate GET.
            # Never attribute that hop's response to the previous POST.
            self._requests.pop(request_id, None)
        request = event.get("request")
        if not isinstance(request, dict) or request.get("method") != "POST" or event.get("type") not in ("Fetch", "XHR"):
            return
        endpoint = _generation_network_endpoint(request.get("url"))
        if endpoint is None:
            return
        initiator = event.get("initiator")
        initiator = initiator.get("type") if isinstance(initiator, dict) else None
        entry = {
            "ordinal": self.event_count, "method": "POST", "resource_type": event["type"],
            "initiator_type": initiator if initiator in ("parser", "script", "preload", "preflight", "other") else "unknown",
            "host": endpoint[0], "endpoint_fp": _generation_network_endpoint_fingerprint(request.get("url")),
            "identification_evidence": endpoint[2],
            "body_present": request.get("hasPostData") if isinstance(request.get("hasPostData"), bool) else None,
            "payload_length": None,  # Never obtain a body/header merely to measure it.
            "cdp_timestamp_s": _latency_number(event.get("timestamp")),
            "python_received": received,
            "response_cdp_timestamp_s": None, "python_response_received": None,
            "finish_cdp_timestamp_s": None, "python_finish_received": None,
            "http_status": None, "mime_type": None, "encoded_response_size": None,
        }
        self.events.append(entry)
        priority = {"verified_endpoint": 0, "image_generation_rpc_semantics_unverified": 1, "post_fetch_xhr_only": 2}
        self.events.sort(key=lambda item: (priority[item["identification_evidence"]], item["ordinal"]))
        if len(self.events) > 32:
            self.truncated = True
            del self.events[32:]
        retained = {id(item) for item in self.events}
        self._requests = {key: item for key, item in self._requests.items() if id(item) in retained}
        if id(entry) in retained and pairable:
            self._requests[request_id] = entry

    def record_response(self, event: Any, *, finished: bool = False) -> None:
        if not self.accepting or not isinstance(event, dict):
            return
        received = self.clock()
        if received - self.started > 30:
            return
        request_id = event.get("requestId")
        if not isinstance(request_id, str) or len(request_id) > 256:
            return
        entry = self._requests.get(request_id)
        if entry is None:
            return
        if finished:
            if entry["python_finish_received"] is None:
                entry["finish_cdp_timestamp_s"] = _latency_number(event.get("timestamp"))
                entry["python_finish_received"] = received
                size = event.get("encodedDataLength")
                if isinstance(size, (int, float)) and not isinstance(size, bool) and math.isfinite(size) and size >= 0:
                    entry["encoded_response_size"] = size
            return
        response = event.get("response")
        if not isinstance(response, dict) or entry["python_response_received"] is not None:
            return
        entry["response_cdp_timestamp_s"] = _latency_number(event.get("timestamp"))
        entry["python_response_received"] = received
        status = response.get("status")
        if isinstance(status, (int, float)) and not isinstance(status, bool) and math.isfinite(status) and 100 <= status <= 599 and int(status) == status:
            entry["http_status"] = int(status)
        mime = response.get("mimeType")
        mime = mime.split(";", 1)[0].strip().lower() if isinstance(mime, str) and len(mime) <= 256 else None
        entry["mime_type"] = mime if mime in _GENERATION_NETWORK_SAFE_MIME_TYPES else None

    def arm(self) -> None:
        for name, handler in (("Network.requestWillBeSent", self.handler),
                              ("Network.responseReceived", self.response_handler),
                              ("Network.loadingFinished", self.finish_handler)):
            self.cdp.on(name, handler)
            self._listeners.append((name, handler))
            self.listening = True
        interruptible_call("pre_start_network_diagnostic", "network_enable", self.cdp.send,
                           "Network.enable", {"maxPostDataSize": 0, "maxTotalBufferSize": 0, "maxResourceBufferSize": 0})
        self.network_enabled = True
        performance_enabled = False
        try:
            interruptible_call("pre_start_network_diagnostic", "performance_enable", self.cdp.send,
                               "Performance.enable", {"timeDomain": "timeTicks"})
            performance_enabled = True
            requested = self.clock()
            raw = interruptible_call("pre_start_network_diagnostic", "performance_clock_sample", self.cdp.send,
                                     "Performance.getMetrics")
            returned = self.clock()
            metrics = raw.get("metrics") if isinstance(raw, dict) else None
            if isinstance(metrics, list):
                for metric in metrics[:100]:
                    if isinstance(metric, dict) and metric.get("name") in {"Timestamp", "NavigationStart"}:
                        value = _latency_number(metric.get("value"))
                        if value is not None:
                            self.metrics[metric["name"]] = value * 1000
            self.metrics.update(python_midpoint=(requested + returned) / 2, uncertainty_ms=(returned - requested) * 1000)
        except Exception:
            cancellation_checkpoint("pre_start_network_diagnostic")
            self.status = "performance_clock_unavailable"
        finally:
            if performance_enabled and not cancellation_requested():
                try:
                    interruptible_call("pre_start_network_diagnostic", "performance_disable", self.cdp.send, "Performance.disable")
                except Exception:
                    cancellation_checkpoint("pre_start_network_diagnostic")
                    self.status = "performance_disable_failed"

    def stop(self, *, skip_rpc: bool = False) -> bool:
        self.accepting = False
        self._requests.clear()
        remaining = []
        for name, handler in self._listeners:
            try:
                self.cdp.remove_listener(name, handler)
            except Exception:
                self.status = "listener_removal_failed"
                remaining.append((name, handler))
        self._listeners = remaining
        self.listening = bool(remaining)
        if skip_rpc or cancellation_requested():
            # Domain teardown is abandoned to driver disconnect, not deferred to
            # a later finally block that could start a fresh blocking RPC.
            self.network_enabled = False
        if self.network_enabled and not skip_rpc and not cancellation_requested():
            try:
                interruptible_call("network_diagnostic_cleanup", "network_disable", self.cdp.send, "Network.disable")
                self.network_enabled = False
            except Exception:
                cancellation_checkpoint("network_diagnostic_cleanup")
                self.status = "network_disable_failed"
        return not self.listening

    def snapshot(self, raw: Any, recorder: Any, sentinel: dict[str, Any]) -> dict[str, Any]:
        # Chromium Performance Timestamp/NavigationStart and Network timestamp
        # use TimeTicks seconds. Subtract this document's NavigationStart, then
        # validate against the existing performance.now()/Python RPC bracket.
        # The RPC widths + measured residual are empirical uncertainty, not a
        # transport guarantee; wallTime/Date.now are deliberately not mixed in.
        raw = raw if isinstance(raw, dict) else {}
        pointer = _latency_number(raw.get("pointerdown_at_ms"))
        anchor = recorder._browser_anchor if recorder else None
        uncertainty = None
        reason = "clock_sample_or_pointerdown_unavailable"
        navigation = self.metrics.get("NavigationStart")
        stamp = self.metrics.get("Timestamp")
        if pointer is not None and anchor and navigation is not None and stamp is not None and stamp >= navigation:
            budget = self.metrics["uncertainty_ms"] + anchor["uncertainty_ms"] + 2
            expected = anchor["browser_now_ms"] + (self.metrics["python_midpoint"] - anchor["python_midpoint"]) * 1000
            residual = abs(stamp - navigation - expected)
            uncertainty = round(budget + residual, 3)
            reason = "aligned" if residual <= budget and uncertainty <= 50 else "clock_alignment_inconsistent_or_too_uncertain"
        if raw.get("capture_expired") is True:
            reason = "sentinel_capture_expired"
        aligned = reason == "aligned"
        pointer_python = anchor["python_midpoint"] + (pointer - anchor["browser_now_ms"]) / 1000 if aligned else None
        window_end = _latency_number(raw.get("read_at_ms"))
        window_end = window_end - pointer if aligned and window_end is not None else None
        longest_start = sentinel.get("longest_longtask_start_ms")
        if not isinstance(longest_start, (int, float)) or isinstance(longest_start, bool) or not math.isfinite(longest_start):
            longest_start = None
        longest_duration = _latency_number(sentinel.get("longest_longtask_ms"))
        if longest_start is None and sentinel.get("longtask_count") == 1:
            longest_start = _latency_number(sentinel.get("first_longtask_start_ms"))
        candidates = []
        for event in self.events:
            relative = event["cdp_timestamp_s"] * 1000 - navigation - pointer if aligned and event["cdp_timestamp_s"] is not None else None
            if relative is not None and (relative + uncertainty < 0 or window_end is not None and relative - uncertainty > window_end):
                continue
            received = (event["python_received"] - pointer_python) * 1000 if pointer_python is not None else None
            relation = "unavailable"
            if relative is not None and longest_start is not None and longest_duration is not None:
                end = longest_start + longest_duration
                relation = "before_longtask" if relative + uncertainty < longest_start else "after_longtask" if relative - uncertainty > end else (
                    "during_longtask" if relative - uncertainty >= longest_start and relative + uncertainty <= end else "boundary_uncertain")
            safe = {key: event[key] for key in ("ordinal", "method", "resource_type", "initiator_type", "host", "endpoint_fp",
                    "identification_evidence", "body_present", "payload_length", "cdp_timestamp_s", "http_status", "mime_type", "encoded_response_size")}
            safe.update(request_start_ms=round(relative, 3) if relative is not None else None,
                        python_event_receive_ms=round(received, 3) if received is not None and received >= 0 else None,
                        timing_relation=relation)
            for phase in ("response", "finish"):
                stamp = event[phase + "_cdp_timestamp_s"]
                phase_time = stamp * 1000 - navigation - pointer if aligned and stamp is not None else None
                receipt = event["python_" + phase + "_received"]
                receipt_time = (receipt - pointer_python) * 1000 if pointer_python is not None and receipt is not None else None
                safe[phase + "_received_ms" if phase == "response" else "loading_finished_ms"] = round(phase_time, 3) if phase_time is not None else None
                safe["python_" + phase + "_event_receive_ms"] = round(receipt_time, 3) if receipt_time is not None and receipt_time >= 0 else None
            candidates.append(safe)
        verified = [event for event in candidates if event["identification_evidence"] == "verified_endpoint"]
        exact = verified[0] if len(verified) == 1 and not self.truncated and verified[0]["request_start_ms"] is not None and verified[0]["request_start_ms"] >= uncertainty else None
        ack = recorder._marks.get("ack_ms") if recorder else None
        ack_relative = (recorder._origin - pointer_python) * 1000 + ack if pointer_python is not None and ack is not None else None
        hint = "unresolved_no_verified_generation_request"
        if exact:
            hint = {"before_longtask": "request_observed_before_recorded_longtask", "during_longtask": "request_observed_during_recorded_longtask",
                    "after_longtask": "request_observed_after_recorded_longtask"}.get(exact["timing_relation"], "request_observed_timing_relation_unresolved")
        return {"schema": "nano-banana-generation-network-timing/v1", "status": self.status,
                "capture_scope": "post_pointerdown_to_existing_post_ack_sentinel_read", "event_limit": 1000, "record_limit": 32,
                "candidate_limit": 10, "truncated": self.truncated, "candidate_count": len(candidates), "candidates": candidates[:10],
                "exact_generation_request": exact, "generation_request_start_relative_to_pointerdown_ms": exact["request_start_ms"] if exact else None,
                "clock_method": "cdp_monotonic_minus_navigation_start_minus_browser_pointerdown",
                "alignment_reason": reason, "alignment_uncertainty_ms": uncertainty,
                "window_end_ms": round(window_end, 3) if window_end is not None else None,
                "pointerdown_ms": 0 if pointer is not None else None, "click_ms": sentinel.get("click_ms"),
                "first_longtask_start_ms": sentinel.get("first_longtask_start_ms"), "longest_longtask_ms": longest_duration,
                "longest_longtask_start_ms": longest_start, "relation_scope": "recorded_longest_task_with_paired_start_and_duration",
                "observer_evaluate_start_ms": sentinel.get("observer_evaluate_start_ms"),
                "ack_ms": round(ack_relative, 3) if ack_relative is not None and ack_relative >= 0 else None,
                "python_clock_alignment_uncertainty_ms": anchor["uncertainty_ms"] if anchor else None,
                "conclusion_hint": hint, "server_receipt_or_work_start_measured": False}


# Fixed names only. No arguments, return values, selectors, or error messages retained.
_ACK_CALL_BUDGETS = {
    "cancellation_checkpoint": "synchronous_no_wait",
    "failure_observation": "nested_calls_no_shared_deadline",
    "progress_observation": "nested_calls_no_shared_deadline",
    "candidate_observation": "nested_calls_no_shared_deadline",
    "composer_resolution": "nested_calls_no_shared_deadline",
    "start_state_read": "locator_resolution_500ms_js_execution_not_bounded",
    "prompt_state_read": "locator_resolution_1000ms_js_execution_not_bounded",
    "poll_wait": "existing_100ms_wait_transport_not_bounded",
    "observer_read": "page_evaluate_no_execution_deadline",
    "observer_page_evaluate": "page_evaluate_no_execution_deadline",
    "_progress_signals": "page_evaluate_no_execution_deadline",
    "progress_page_evaluate": "page_evaluate_no_execution_deadline",
    "discover_candidates": "nested_calls_no_shared_deadline",
    "_asset_snapshot": "page_evaluate_no_execution_deadline",
    "candidate_snapshot_page_evaluate": "page_evaluate_no_execution_deadline",
    "_current_generation_submit_buttons": "nested_calls_no_shared_deadline",
    "_read_active_composer_generation_resolution": "page_evaluate_no_execution_deadline",
    "composer_page_evaluate": "page_evaluate_no_execution_deadline",
    "_prompt_input": "nested_locator_calls_inherited_timeout",
    "prompt_textboxes_all": "immediate_locator_count_rpc_not_bounded",
    "prompt_fallback_all": "immediate_locator_count_rpc_not_bounded",
    "prompt_is_visible": "immediate_visibility_rpc_not_bounded",
    "prompt_is_enabled": "inherited_locator_timeout",
    "prompt_get_attribute": "inherited_locator_timeout",
    "composer_buttons_all": "immediate_locator_count_rpc_not_bounded",
    "_live_control_facts": "locator_resolution_500ms_js_execution_not_bounded",
    "live_control_evaluate": "locator_resolution_500ms_js_execution_not_bounded",
    "_prompt_control_post_start_state": "locator_resolution_1000ms_js_execution_not_bounded",
    "prompt_state_evaluate": "locator_resolution_1000ms_js_execution_not_bounded",
}
_ACK_ROOT_STAGES = frozenset({
    "cancellation_checkpoint", "failure_observation", "progress_observation",
    "candidate_observation", "composer_resolution", "start_state_read",
    "prompt_state_read", "poll_wait",
})


class _AckPollProfiler:
    """Opt-in inclusive call spans, bounded percentiles, and five slowest polls."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.origin = float(clock())
        self.calls: dict[str, dict[str, Any]] = {}
        self.samples: dict[str, deque[float]] = {}
        self.stack: list[dict[str, Any]] = []
        self.current_poll: dict[str, Any] | None = None
        self.poll_count = 0
        self.pending_start: float | None = None
        self.pending_stages: dict[str, float] = {}
        self.slowest_polls: list[dict[str, Any]] = []
        self.poll_total_ms = 0.0

    @contextmanager
    def span(self, name: str):
        if name not in _ACK_CALL_BUDGETS:
            yield
            return
        started = float(self.clock())
        frame = {"name": name, "child_elapsed_ms": 0.0}
        self.stack.append(frame)
        if name == "cancellation_checkpoint":
            self.pending_start = started
        try:
            yield
        finally:
            duration = max(0.0, (float(self.clock()) - started) * 1000)
            self.stack.pop()
            if self.stack:
                self.stack[-1]["child_elapsed_ms"] += duration
            self_duration = max(0.0, duration - frame["child_elapsed_ms"])
            poll = self.current_poll["poll"] if self.current_poll else self.poll_count + 1
            stats = self.calls.setdefault(name, {
                "total_calls": 0, "total_elapsed_ms": 0.0,
                "min_ms": duration, "max_ms": duration, "max_poll": poll,
                "last_call_duration_ms": duration, "budget": _ACK_CALL_BUDGETS[name],
                "total_self_elapsed_ms": 0.0, "max_self_ms": 0.0,
                "timeout_ms": (
                    500 if "500ms" in _ACK_CALL_BUDGETS[name] else
                    1000 if "1000ms" in _ACK_CALL_BUDGETS[name] else
                    100 if name == "poll_wait" else None
                ),
                "inherited_library_default_ms": (
                    30_000 if _ACK_CALL_BUDGETS[name] == "inherited_locator_timeout" else None
                ),
            })
            stats["total_calls"] += 1
            stats["total_elapsed_ms"] += duration
            stats["total_self_elapsed_ms"] += self_duration
            stats["max_self_ms"] = max(stats["max_self_ms"], self_duration)
            stats["min_ms"] = min(stats["min_ms"], duration)
            if duration >= stats["max_ms"]:
                stats["max_ms"], stats["max_poll"] = duration, poll
            stats["last_call_duration_ms"] = duration
            self.samples.setdefault(name, deque(maxlen=_LATENCY_SAMPLE_LIMIT)).append(duration)
            if name in _ACK_ROOT_STAGES:
                stages = self.current_poll["stages"] if self.current_poll else self.pending_stages
                stages[name] = stages.get(name, 0.0) + duration
            if self.current_poll is not None:
                leaf = self.current_poll.get("dominant_nested_helper")
                if leaf is None or self_duration > self.current_poll["dominant_nested_helper_ms"]:
                    self.current_poll["dominant_nested_helper"] = name
                    self.current_poll["dominant_nested_helper_ms"] = self_duration

    def begin_poll(self, origin: float) -> None:
        self.origin = origin
        self.poll_count += 1
        self.current_poll = {
            "poll": self.poll_count,
            "poll_start_ms": max(0.0, ((self.pending_start if self.pending_start is not None
                                      else float(self.clock())) - origin) * 1000),
            "stages": self.pending_stages,
        }
        self.pending_start, self.pending_stages = None, {}

    def finish_poll(self) -> None:
        if self.current_poll is None:
            return
        poll = self.current_poll
        poll["poll_end_ms"] = max(0.0, (float(self.clock()) - self.origin) * 1000)
        poll["poll_duration_ms"] = max(0.0, poll["poll_end_ms"] - poll["poll_start_ms"])
        stages = poll["stages"]
        poll["dominant_stage"] = max(stages, key=stages.get) if stages else None
        self.poll_total_ms += poll["poll_duration_ms"]
        self.slowest_polls.append(poll)
        self.slowest_polls.sort(key=lambda item: item["poll_duration_ms"], reverse=True)
        del self.slowest_polls[_ACK_SLOW_POLL_LIMIT:]
        self.current_poll = None

    def snapshot(self) -> dict[str, Any]:
        calls = {}
        for name, stats in self.calls.items():
            values = sorted(self.samples[name])
            calls[name] = {
                **stats, "p50_ms": statistics.median(values),
                "p95_ms": values[math.ceil(len(values) * .95) - 1] if len(values) >= 20 else None,
                "percentile_sample_count": len(values),
            }
            calls[name] = {k: round(v, 3) if isinstance(v, float) else v
                           for k, v in calls[name].items()}
        return {
            "schema": "nano-banana-ack-poll-breakdown/v1", "total_polls": self.poll_count,
            "total_poll_elapsed_ms": round(self.poll_total_ms, 3),
            "calls": calls,
            "slowest_polls": [{**p, "stages": {k: round(v, 3) for k, v in p["stages"].items()}}
                              for p in self.slowest_polls],
            "slow_poll_limit": _ACK_SLOW_POLL_LIMIT,
            "percentile_scope": "last_128_calls_per_name_min_max_totals_cover_all_calls",
            "duration_scope": "inclusive_nested_spans_do_not_sum_parent_and_child",
        }


_ACTIVE_ACK_PROFILER: ContextVar[_AckPollProfiler | None] = ContextVar(
    "nano_batch_ack_profiler", default=None
)


def _ack_call(name: str, callback: Callable, /, *args, **kwargs):
    profiler = _ACTIVE_ACK_PROFILER.get()
    if profiler is None:
        return callback(*args, **kwargs)
    with profiler.span(name):
        return callback(*args, **kwargs)


def _ack_profiled(name: str):
    def decorate(method):
        @wraps(method)
        def measured(*args, **kwargs):
            return _ack_call(name, method, *args, **kwargs)
        return measured
    return decorate


def _ack_wait_scope(method):
    @wraps(method)
    def measured(self, *args, **kwargs):
        if not self.prestart_timing_debug:
            return method(self, *args, **kwargs)
        profiler = _AckPollProfiler(time.perf_counter)
        token = _ACTIVE_ACK_PROFILER.set(profiler)
        try:
            return method(self, *args, **kwargs)
        finally:
            profiler.finish_poll()
            _ACTIVE_ACK_PROFILER.reset(token)
    return measured
_LATENCY_COUNT_FIELDS = (
    "dom_node_count", "element_count", "candidate_tile_count",
    "visible_candidate_tile_count", "hidden_candidate_tile_count",
    "history_item_count", "result_card_count", "data_media_id_count",
    "button_count", "body_text_length", "history_descendant_count",
    "history_scroll_height", "virtualized_item_count", "active_observer_count",
    "observer_install_count", "observer_disconnect_count", "observer_peak_count",
    "retained_failure_container_count", "detached_failure_container_count",
    "observer_callback_count",
    "page_lifetime_ms", "composer_identity_counter", "history_surface_identity_counter",
    "baseline_candidate_count", "run_owned_candidate_count",
)


def _latency_number(value: Any) -> float | None:
    # An allowlisted numeric-only boundary, including mocked/unavailable metrics.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1_000_000_000.0, float(value))) if math.isfinite(value) else None


def _latency_counts(raw: Any) -> dict[str, int | None]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        key: int(value) if (value := _latency_number(raw.get(key))) is not None else None
        for key in _LATENCY_COUNT_FIELDS
    }
_GENERATION_WINDOW_WATCHDOG_INTERVAL_SECONDS = 6.0
_WINDOW_RECOVERY_EVENT_LIMIT = 20
_WINDOW_RECOVERY_REASONS = {
    "generation_wait_minimized",
    "download_control_zero_geometry",
    "download_control_absent",
    "startup_nonmaximized",
}

_PRESTART_TIMING_DURATION_FIELDS = {
    "fill_duration_ms": ("prompt_fill_start_ms", "prompt_fill_end_ms"),
    "prompt_verify_duration_ms": (
        "prompt_verification_start_ms",
        "prompt_verified_ms",
    ),
    "composer_reacquire_duration_ms": (
        "composer_reacquire_start_ms",
        "composer_reacquired_ms",
    ),
    "start_control_reacquire_duration_ms": (
        "start_control_reacquire_start_ms",
        "start_control_reacquired_ms",
    ),
    "start_actionability_duration_ms": (
        "start_actionability_wait_start_ms",
        "start_actionable_ms",
    ),
    "candidate_baseline_duration_ms": (
        "candidate_baseline_start_ms",
        "candidate_baseline_complete_ms",
    ),
    "progress_baseline_duration_ms": (
        "progress_baseline_start_ms",
        "progress_baseline_complete_ms",
    ),
    "observer_arm_duration_ms": (
        "failure_observer_arm_start_ms",
        "failure_observer_armed_ms",
    ),
    "final_prestart_validation_duration_ms": (
        "final_prestart_validation_start_ms",
        "final_prestart_validation_complete_ms",
    ),
    "mouse_position_duration_ms": (
        "mouse_position_start_ms",
        "mouse_position_complete_ms",
    ),
    "fill_to_mouse_down_ms": ("prompt_fill_start_ms", "start_mouse_down_ms"),
    "fill_end_to_mouse_down_ms": ("prompt_fill_end_ms", "start_mouse_down_ms"),
    "mouse_down_to_ack_ms": ("start_mouse_down_ms", "start_ack_ms"),
    "start_ack_wait_duration_ms": ("start_ack_wait_start_ms", "start_ack_ms"),
    "total_prestart_duration_ms": ("prompt_fill_start_ms", "start_mouse_down_ms"),
}


class _PreStartTimingRecorder:
    """Privacy-safe monotonic phase recorder for one Start activation."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._absolute_marks: dict[str, float] = {}
        self._polling: dict[str, dict[str, Any]] = {}
        self._last_clock_value: float | None = None

    def mark_once(self, name: str) -> None:
        if name in self._absolute_marks:
            return
        observed = float(self._clock())
        if not math.isfinite(observed):
            observed = self._last_clock_value or 0.0
        if self._last_clock_value is not None:
            observed = max(self._last_clock_value, observed)
        self._last_clock_value = observed
        self._absolute_marks[name] = observed

    def record_polling(
        self,
        name: str,
        *,
        poll_count: int,
        poll_interval_ms: int,
        timeout_ms: int,
        success_immediate: bool,
        final_reason: str,
        remount_observed: bool | None = None,
    ) -> None:
        detail: dict[str, Any] = {
            "poll_count": max(0, int(poll_count)),
            "configured_poll_interval_ms": max(0, int(poll_interval_ms)),
            "configured_timeout_ms": max(0, int(timeout_ms)),
            "success_immediate": bool(success_immediate),
            "final_reason": (
                final_reason
                if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", final_reason)
                else "unknown"
            ),
        }
        if remount_observed is not None:
            detail["remount_observed"] = bool(remount_observed)
        self._polling[name] = detail

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "nano-banana-prestart-timing/v1",
            "clock": "monotonic_perf_counter",
            "timing_origin": "prompt_fill_start_ms",
            "pre_origin_phases": ["candidate_baseline", "progress_baseline"],
            "polling": {
                key: dict(value) for key, value in self._polling.items()
            },
        }
        origin = self._absolute_marks.get("prompt_fill_start_ms")
        if origin is None:
            result["origin_established"] = False
            return result
        result["origin_established"] = True
        elapsed_values: list[int] = []
        for name, absolute in self._absolute_marks.items():
            elapsed = round((absolute - origin) * 1_000)
            result[name] = elapsed
            elapsed_values.append(elapsed)
        result["monotonic_elapsed_non_decreasing"] = all(
            later >= earlier
            for earlier, later in zip(elapsed_values, elapsed_values[1:])
        )
        for name, (start_name, end_name) in _PRESTART_TIMING_DURATION_FIELDS.items():
            if start_name in result and end_name in result:
                result[name] = max(0, int(result[end_name]) - int(result[start_name]))
        return result


_POST_START_ACK_STATE_KEYS = (
    "start_button_visible",
    "start_button_enabled",
    "start_button_actionable",
    "start_button_disappeared",
    "prompt_editor_changed",
    "prompt_editor_cleared",
    "prompt_editor_disabled",
    "progress_signal_count",
    "candidate_count",
    "candidate_uuid_appeared",
    "generation_dom_mutation_count",
    "exact_explicit_failure_detected",
    "composer_remounted",
)


class _PostStartAckTimingRecorder:
    """Bounded, prompt-free timing for the existing Start acknowledgement loop."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        initial_composer_token: int | None,
        origin_source: str,
    ):
        self._clock = clock
        self._last_clock_value: float | None = None
        self._origin = self._read_clock()
        self._marks: dict[str, int] = {"start_mouse_down_ms": 0}
        self._poll_count = 0
        self._current_poll: dict[str, Any] | None = None
        self._last_poll_complete_ms: int | None = None
        self._last_poll: dict[str, Any] | None = None
        self._trace: list[dict[str, Any]] = []
        self._trace_truncated = False
        self._last_trace_signature: tuple[Any, ...] | None = None
        self._stage_totals_ms: dict[str, int] = {}
        self._stage_max_ms: dict[str, int] = {}
        self._configured_timeout_ms: int | None = None
        self._configured_poll_interval_ms: int | None = None
        self._ack_signal: str | None = None
        self._origin_source = origin_source
        self.initial_composer_token = initial_composer_token
        self._latency_samples: dict[str, deque[float]] = {}
        self._browser_anchor: dict[str, float] | None = None
        self._diagnostic_counts: dict[str, dict[str, int | None]] = {}
        self._observer_metrics: dict[str, float | None] = {}
        self._ack_profiler: _AckPollProfiler | None = None
        self._callback_unavailable_reason: str | None = "observer_not_read"
        self._first_nonzero_observer_read_duration_ms: float | None = None
        self._observer_reads: list[dict[str, Any]] = []
        self._observer_read_count = 0
        self._observer_stage_max: dict[str, float] = {}
        self.initial_state = {
            "start_button_visible": True,
            "start_button_enabled": True,
            "start_button_actionable": True,
            "start_button_disappeared": False,
            "prompt_editor_changed": False,
            "prompt_editor_cleared": False,
            "prompt_editor_disabled": False,
            "progress_signal_count": 0,
            "candidate_count": 0,
            "candidate_uuid_appeared": False,
            "generation_dom_mutation_count": 0,
            "exact_explicit_failure_detected": False,
            "composer_remounted": False,
        }

    def _read_clock(self) -> float:
        observed = float(self._clock())
        if not math.isfinite(observed):
            observed = self._last_clock_value or 0.0
        if self._last_clock_value is not None:
            observed = max(self._last_clock_value, observed)
        self._last_clock_value = observed
        return observed

    def _elapsed_ms(self) -> int:
        return max(0, round((self._read_clock() - self._origin) * 1_000))

    def mark_once(self, name: str, *, elapsed_ms: int | None = None) -> int:
        elapsed = self._elapsed_ms() if elapsed_ms is None else max(0, int(elapsed_ms))
        self._marks.setdefault(name, elapsed)
        return elapsed

    def configure_wait(self, *, timeout_ms: int, poll_interval_ms: int) -> None:
        self._configured_timeout_ms = max(0, int(timeout_ms))
        self._configured_poll_interval_ms = max(0, int(poll_interval_ms))
        self.mark_once("ack_wait_start_ms")

    def begin_poll(self) -> None:
        self._poll_count += 1
        elapsed = self._elapsed_ms()
        current: dict[str, Any] = {
            "poll": self._poll_count,
            "poll_started_ms": elapsed,
            "stage_durations_ms": {},
        }
        if self._last_poll_complete_ms is not None:
            current["inter_poll_gap_ms"] = max(
                0, elapsed - self._last_poll_complete_ms
            )
        self._current_poll = current
        self._last_stage_complete_ms = elapsed
        profiler = _ACTIVE_ACK_PROFILER.get()
        if profiler is not None:
            self._ack_profiler = profiler
            profiler.begin_poll(self._origin)

    def stage_complete(self, stage: str, **state: Any) -> int:
        if self._current_poll is None:
            return self._elapsed_ms()
        elapsed = self._elapsed_ms()
        duration = max(0, elapsed - self._last_stage_complete_ms)
        self._last_stage_complete_ms = elapsed
        durations = self._current_poll["stage_durations_ms"]
        durations[stage] = duration
        self._stage_totals_ms[stage] = self._stage_totals_ms.get(stage, 0) + duration
        self._stage_max_ms[stage] = max(self._stage_max_ms.get(stage, 0), duration)
        self.record_latency(stage, duration)
        for key, value in state.items():
            if key in _POST_START_ACK_STATE_KEYS:
                self._current_poll[key] = value
        self._mark_first_signals(state, elapsed)
        return elapsed

    def record_latency(self, stage: str, duration_ms: Any) -> None:
        if stage not in {
            "explicit_failure_check", "progress_check", "candidate_check",
            "composer_check", "live_start_check", "prompt_check", "observer_read",
            "cheap_round_trip", "dom_count_snapshot",
        }:
            return
        duration = _latency_number(duration_ms)
        if duration is not None:
            self._latency_samples.setdefault(stage, deque(maxlen=_LATENCY_SAMPLE_LIMIT)).append(duration)

    def set_browser_anchor(self, anchor: dict[str, float] | None) -> None:
        self._browser_anchor = anchor

    def observe_browser_metrics(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            if "first_mutation_callback_ms" not in self._marks:
                self._callback_unavailable_reason = "observer_diagnostics_unavailable"
            return
        self._observer_metrics = {
            key: _latency_number(raw.get(key)) for key in (
                "active_observer_count", "observer_install_count", "observer_disconnect_count",
                "observer_peak_count", "observer_callback_count",
                "observer_callback_total_ms", "observer_callback_max_ms",
                "retained_failure_container_count", "detached_failure_container_count",
            )
        }
        first = _latency_number(raw.get("first_mutation_at_ms"))
        if "first_mutation_callback_ms" not in self._marks:
            self._callback_unavailable_reason = (
                "browser_callback_not_captured" if first is None else
                "clock_anchor_unavailable" if not self._browser_anchor else
                "callback_precedes_click_boundary"
            )
        if first is not None and self._browser_anchor:
            anchor = self._browser_anchor
            elapsed = round(first - anchor["browser_now_ms"] +
                            (anchor["python_midpoint"] - self._origin) * 1_000)
            if elapsed >= 0:
                self.mark_once("first_mutation_callback_ms", elapsed_ms=elapsed)
                self._callback_unavailable_reason = None
                key = "first_any_browser_observed_change_ms"
                self._marks[key] = min(self._marks.get(key, elapsed), elapsed)

    def note_observer_read(self, raw: Any, duration_ms: float) -> None:
        count = _latency_number(raw.get("generation_dom_mutation_count")) if isinstance(raw, dict) else None
        if count is not None and count > 0 and "python_first_observer_read_nonzero_ms" not in self._marks:
            self.mark_once("python_first_observer_read_nonzero_ms")
            self._first_nonzero_observer_read_duration_ms = _latency_number(duration_ms)

    def record_observer_internal(self, raw: Any, requested: float, returned: float) -> None:
        """Numeric allowlist only; five slowest reads, no DOM handles/text retained."""
        if not isinstance(raw, dict):
            return
        self._observer_read_count += 1
        wall = max(0.0, (returned - requested) * 1000)
        browser = _latency_number(raw.get("total_browser_js_ms"))
        stages = raw.get("stages")
        stages = stages if isinstance(stages, dict) else {}
        safe_stages = {}
        for name in _OBSERVER_READ_STAGES:
            stats = stages.get(name)
            if not isinstance(stats, dict):
                continue
            safe_stages[name] = {key: _latency_number(stats.get(key))
                                 for key in ("calls", "total_ms", "max_ms", "self_ms")}
            maximum = safe_stages[name]["max_ms"]
            if maximum is not None:
                self._observer_stage_max[name] = max(self._observer_stage_max.get(name, 0), maximum)
        dominant = max(safe_stages, key=lambda name: safe_stages[name]["self_ms"] or 0,
                       default=None)
        counts = self._diagnostic_counts.get("post_ack", self._diagnostic_counts.get("pre_click", {}))
        def count(name, limit):
            value = _latency_number(raw.get(name))
            return min(limit, int(value)) if value is not None else None
        record = {
            "read": self._observer_read_count, "wall_ms": round(wall, 3),
            "total_browser_js_ms": browser,
            # Includes scheduling, transport, protocol serialization, and unmeasured
            # wrapper work. It is not a measurement of any one of those components.
            "transport_queue_serialization_gap_ms": (
                round(wall - browser, 3) if browser is not None and wall >= browser else None
            ),
            "stages": safe_stages, "dominant_internal_stage": dominant,
            "dom_counts": {key: counts.get(key)
                           for key in ("dom_node_count", "candidate_tile_count", "history_item_count")},
            "dom_count_snapshot_scope": "post_ack_reused" if "post_ack" in self._diagnostic_counts else "pre_click_reused",
            "containers_scanned": count("containers_scanned", 200),
            "retained_strong_container_count": count("retained_strong_container_count", 200),
            "mutation_record_count": count("mutation_record_count", 100_000),
            "container_sample_limit": _LATENCY_SAMPLE_LIMIT,
            "container_sample_count": min(_LATENCY_SAMPLE_LIMIT, int(_latency_number(raw.get("container_sample_count")) or 0)),
            "container_percentile_scope": "last_128_containers_max_covers_all_scanned",
            "max_container_ms": _latency_number(raw.get("max_container_ms")),
            "p50_container_ms": _latency_number(raw.get("p50_container_ms")),
            "p95_container_ms": _latency_number(raw.get("p95_container_ms")),
            "slowest_container_index": None,
            "slowest_container_stage": raw.get("slowest_container_stage") if raw.get("slowest_container_stage") in _OBSERVER_READ_STAGES else None,
            "evaluate_enter_browser_ms": _latency_number(raw.get("evaluate_enter_browser_ms")),
            "evaluate_exit_browser_ms": _latency_number(raw.get("evaluate_exit_browser_ms")),
            "queue_start_ms": None, "protocol_serialization_ms": None,
            "estimated_request_to_browser_enter_ms": None,
            "estimated_browser_exit_to_python_ms": None,
            "clock_alignment_uncertainty_ms": None,
            "queue_timing_unavailable_reason": "clock_anchor_unavailable",
        }
        index = raw.get("slowest_container_index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < 200:
            record["slowest_container_index"] = index
        anchor = self._browser_anchor
        enter, exit_ = record["evaluate_enter_browser_ms"], record["evaluate_exit_browser_ms"]
        if anchor and enter is not None and exit_ is not None:
            before = (anchor["python_midpoint"] - requested) * 1000 + enter - anchor["browser_now_ms"]
            after = (returned - anchor["python_midpoint"]) * 1000 - exit_ + anchor["browser_now_ms"]
            record["clock_alignment_uncertainty_ms"] = _latency_number(anchor.get("uncertainty_ms"))
            if before >= 0 and after >= 0 and exit_ >= enter:
                record["estimated_request_to_browser_enter_ms"] = round(before, 3)
                record["estimated_browser_exit_to_python_ms"] = round(after, 3)
                record["queue_timing_unavailable_reason"] = "direct_queue_start_not_observable_estimates_only"
            else:
                record["queue_timing_unavailable_reason"] = "clock_alignment_inconsistent"
        self._observer_reads.append(record)
        self._observer_reads.sort(key=lambda item: item["wall_ms"], reverse=True)
        del self._observer_reads[_ACK_SLOW_POLL_LIMIT:]

    def observer_read_snapshot(self) -> dict[str, Any] | None:
        if not self._observer_reads:
            return None
        return {
            "schema": "nano-banana-observer-read-breakdown/v1",
            "total_reads": self._observer_read_count,
            "slowest_reads": list(self._observer_reads), "read_limit": _ACK_SLOW_POLL_LIMIT,
            "internal_stage_max_ms": dict(self._observer_stage_max),
            "duration_scope": "inclusive_stages_do_not_sum_dominant_uses_exclusive_self_time",
            "serialization_scope": "return_payload_is_object_construction_protocol_serialization_unmeasured",
        }

    def set_counts(self, phase: str, raw: Any) -> None:
        if phase in {"pre_click", "post_ack"}:
            self._diagnostic_counts[phase] = _latency_counts(raw)

    def _mark_first_signals(self, state: dict[str, Any], elapsed: int) -> None:
        changed = any(
            value is not None and value != self.initial_state.get(key)
            for key, value in state.items() if key in _POST_START_ACK_STATE_KEYS
            and key != "generation_dom_mutation_count"
        )
        if changed:
            self.mark_once("first_any_browser_observed_change_ms", elapsed_ms=elapsed)
        if any(state.get(key) is not None and state[key] != self.initial_state[key]
               for key in ("start_button_visible", "start_button_enabled",
                           "start_button_actionable", "start_button_disappeared")):
            self.mark_once("first_start_state_change_ms", elapsed_ms=elapsed)
        if state.get("start_button_visible") is False:
            self.mark_once("start_button_hidden_ms", elapsed_ms=elapsed)
        if state.get("start_button_enabled") is False:
            self.mark_once("start_button_disabled_ms", elapsed_ms=elapsed)
        if state.get("start_button_disappeared") is True:
            self.mark_once("start_button_disappeared_ms", elapsed_ms=elapsed)
        if any(
            state.get(key) is True
            for key in (
                "prompt_editor_changed",
                "prompt_editor_cleared",
                "prompt_editor_disabled",
            )
        ):
            self.mark_once("prompt_editor_transition_ms", elapsed_ms=elapsed)
        if state.get("prompt_editor_cleared") is True:
            self.mark_once("prompt_editor_cleared_ms", elapsed_ms=elapsed)
        if state.get("prompt_editor_disabled") is True:
            self.mark_once("prompt_editor_disabled_ms", elapsed_ms=elapsed)
        if int(state.get("progress_signal_count") or 0) > 0:
            self.mark_once("first_progress_signal_ms", elapsed_ms=elapsed)
            self.mark_once("first_progress_change_ms", elapsed_ms=elapsed)
        if int(state.get("candidate_count") or 0) > 0:
            self.mark_once("first_candidate_signal_ms", elapsed_ms=elapsed)
            self.mark_once("first_candidate_change_ms", elapsed_ms=elapsed)
        if state.get("candidate_uuid_appeared") is True:
            self.mark_once("candidate_uuid_appeared_ms", elapsed_ms=elapsed)
        if int(state.get("generation_dom_mutation_count") or 0) > 0:
            self.mark_once("first_post_start_mutation_ms", elapsed_ms=elapsed)
        if state.get("exact_explicit_failure_detected") is True:
            self.mark_once("explicit_failure_ms", elapsed_ms=elapsed)
        if state.get("composer_remounted") is True:
            self.mark_once("composer_remounted_ms", elapsed_ms=elapsed)

    def finish_poll(self, *, ack_signal: str | None = None) -> None:
        if self._current_poll is None:
            return
        elapsed = self._elapsed_ms()
        current = self._current_poll
        current["poll_complete_ms"] = elapsed
        current["ack_signal"] = ack_signal
        state = {key: current.get(key) for key in _POST_START_ACK_STATE_KEYS}
        signature = tuple(state[key] for key in _POST_START_ACK_STATE_KEYS) + (
            ack_signal,
        )
        if signature == self._last_trace_signature and self._trace:
            self._trace[-1]["through_poll"] = current["poll"]
            self._trace[-1]["last_elapsed_ms"] = elapsed
        elif len(self._trace) < _POST_START_ACK_TRACE_LIMIT:
            self._trace.append(
                {
                    "from_poll": current["poll"],
                    "through_poll": current["poll"],
                    "first_elapsed_ms": current["poll_started_ms"],
                    "last_elapsed_ms": elapsed,
                    "state": {**state, "ack_signal": ack_signal},
                }
            )
            self._last_trace_signature = signature
        else:
            self._trace_truncated = True
        self._last_poll_complete_ms = elapsed
        self._last_poll = {
            key: value for key, value in current.items() if key != "_last_stage_ms"
        }
        self._current_poll = None

    def acknowledge(self, signal: str) -> None:
        elapsed = self.mark_once("ack_ms")
        self._ack_signal = signal
        self.finish_poll(ack_signal=signal)
        self._marks["ack_ms"] = elapsed
        if self._ack_profiler is not None:
            self._ack_profiler.finish_poll()

    def record_timeout(self) -> None:
        self.mark_once("timeout_observed_ms")

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "nano-banana-post-start-ack-timing/v1",
            "clock": "monotonic_perf_counter",
            "timing_origin": "immediately_before_start_mouse_down",
            "origin_source": self._origin_source,
            "origin_established": True,
            "poll_count": self._poll_count,
            "configured_timeout_ms": self._configured_timeout_ms,
            "configured_poll_interval_ms": self._configured_poll_interval_ms,
            "timeout_origin": "ack_wait_entry_after_post_down_validation_and_mouse_up",
            "deadline_check": "before_each_poll_only",
            "poll_stage_order": [
                "explicit_failure_check",
                "progress_check",
                "candidate_check",
                "composer_check",
                "live_start_check",
                "prompt_check",
            ],
            "ack_signal": self._ack_signal,
            "initial_composer_token": self.initial_composer_token,
            "initial_state": dict(self.initial_state),
            "state_change_trace": [
                {
                    **entry,
                    "state": dict(entry["state"]),
                }
                for entry in self._trace
            ],
            "trace_limit": _POST_START_ACK_TRACE_LIMIT,
            "trace_truncated": self._trace_truncated,
            "last_poll": dict(self._last_poll) if self._last_poll else None,
            "stage_total_ms": dict(self._stage_totals_ms),
            "stage_max_ms": dict(self._stage_max_ms),
            **self._marks,
        }
        for key in ("first_any_browser_observed_change_ms", "first_mutation_callback_ms",
                    "first_start_state_change_ms", "first_progress_change_ms",
                    "first_candidate_change_ms"):
            result.setdefault(key, None)
        pre = self._diagnostic_counts.get("pre_click", {})
        post = self._diagnostic_counts.get("post_ack", {})
        result.update({
            "pre_click_dom_node_count": pre.get("dom_node_count"),
            "post_ack_dom_node_count": post.get("dom_node_count"),
            "history_item_count": post.get("history_item_count"),
            "candidate_tile_count": post.get("candidate_tile_count"),
            "active_observer_count": post.get("active_observer_count"),
            "dom_counts": {phase: dict(counts) for phase, counts in self._diagnostic_counts.items()},
            "observer_metrics": dict(self._observer_metrics),
            "browser_clock_anchor_uncertainty_ms": (
                self._browser_anchor.get("uncertainty_ms") if self._browser_anchor else None
            ),
            "observer_count_scope": "adapter_generation_observers_instrumented_on_this_page",
            "mutation_counter_boundary": "observer_arm_before_mouse_positioning",
            "first_mutation_callback_boundary": "last_pre_down_validation_browser_clock_with_rpc_uncertainty",
            "history_count_scope": "rendered_tile_common_ancestor_children_not_total_project_history",
            "detached_history_item_count": None,
            "latency_sample_limit_per_stage": _LATENCY_SAMPLE_LIMIT,
            "check_latency_scope": "existing_ack_check_spans_including_python_work",
            "check_latency_ms": {
                stage: {"samples": len(values), "p50": round(statistics.median(values), 3),
                        "max": round(max(values), 3)}
                for stage, values in self._latency_samples.items() if values
            },
        })
        browser_first = self._marks.get("first_mutation_callback_ms")
        python_first = self._marks.get("python_first_observer_read_nonzero_ms")
        gap = python_first - browser_first if browser_first is not None and python_first is not None else None
        result.update({
            "browser_first_mutation_callback_ms": browser_first,
            "python_first_observer_read_nonzero_ms": python_first,
            "callback_to_python_observation_gap_ms": gap if gap is not None and gap >= 0 else None,
            "browser_callback_unavailable_reason": self._callback_unavailable_reason,
            "gap_unavailable_reason": (
                "clock_alignment_inconsistent" if gap is not None and gap < 0 else
                self._callback_unavailable_reason if browser_first is None else
                "no_nonzero_observer_read" if python_first is None else None
            ),
            "observer_read_call_duration_ms": self._first_nonzero_observer_read_duration_ms,
            "ack_poll_breakdown": self._ack_profiler.snapshot() if self._ack_profiler else None,
            "observer_read_breakdown": self.observer_read_snapshot(),
        })
        wait_start = self._marks.get("ack_wait_start_ms")
        if wait_start is not None and self._configured_timeout_ms is not None:
            nominal_deadline = wait_start + self._configured_timeout_ms
            result["nominal_ack_deadline_ms"] = nominal_deadline
            terminal = self._marks.get("ack_ms", self._marks.get("timeout_observed_ms"))
            if terminal is not None:
                result["deadline_overrun_ms"] = max(0, terminal - nominal_deadline)
        if "ack_ms" in self._marks:
            result["mouse_down_to_ack_ms"] = self._marks["ack_ms"]
        return result
_FLOW_PROJECT_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_PROJECT_UI_SAFE_LABEL_PATTERN = re.compile(
    r"^(?:\+|plus|add|new|create|projects?|"
    r"(?:add|new|create)(?:\s+a)?(?:\s+new)?\s+projects?|"
    r"(?:back|go)\s+to\s+projects?|all\s+projects?|"
    r"open\s+projects?|projects?\s+(?:menu|navigation|list)|"
    r"projects?\s+(?:name|title)|name\s+(?:a\s+)?projects?|"
    r"name\s+your\s+projects?|title|save|cancel|close|done|continue)$",
    re.I,
)
_PROJECT_UI_SENSITIVE_LABEL_PATTERN = re.compile(
    r"account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|"
    r"email|gmail|manage\s+your\s+google|@",
    re.I,
)
_GENERATION_SUBMIT_PATTERN = re.compile(
    r"^(?:(?:start\s+)?generation|generate|create|submit|send)(?:\s+(?:image|images))?$",
    re.I,
)
_FLOW_USAGE_LIMIT_PATTERN = re.compile(
    r"\byou\s*(?:['’]\s*ve|have)\s+reached\s+your\s+usage\s+limit\b",
    re.I,
)
_CURRENT_GENERATION_FAILURE_PATTERN = re.compile(
    r"generation failed|could not generate|couldn't generate|failed to generate|"
    r"error generating|reached\s+your\s+usage\s+limit",
    re.I,
)
_UNUSUAL_ACTIVITY_CANONICAL_FAILURE = (
    "Failed. We noticed some unusual activity. "
    "Please visit the Help Center for more information. "
    "You have not been charged for this generation."
)
_UNUSUAL_ACTIVITY_GENERATION_FAILURE_PATTERN = re.compile(
    r"\bfailed\b.*\bwe noticed some unusual activity\b.*"
    r"\bplease visit the help center for more information\b.*"
    r"\byou have not been charged for this generation\b",
    re.I | re.S,
)
_POLICY_VIOLATION_GENERATION_FAILURE_PATTERN = re.compile(
    r"\bfailed\b.*\bthis generation might violate our policies\b.*"
    r"\bplease try a different prompt or send feedback\b.*"
    r"\byou have not been charged for this generation\b",
    re.I | re.S,
)
_GENERIC_GENERATION_FAILURE_CLAUSE_PATTERN = re.compile(
    r"\bsorry,\s+this image failed to generate\b",
    re.I | re.S,
)
_HIGH_DEMAND_GENERATION_FAILURE_PATTERN = re.compile(
    r"\bfailed\b.*\bflow is experiencing high demand\.\s+please try again later\..*"
    r"\byou have not been charged for this generation\.",
    re.I | re.S,
)
_GENERIC_GENERATION_FAILURE_PATTERN = re.compile(
    r"\bfailed\b.*\bsorry,\s+this image failed to generate\b.*"
    r"\byou have not been charged for this generation\b",
    re.I | re.S,
)


def _is_current_generation_failure_text(value: str) -> bool:
    if (
        _UNUSUAL_ACTIVITY_GENERATION_FAILURE_PATTERN.search(value)
        or _POLICY_VIOLATION_GENERATION_FAILURE_PATTERN.search(value)
        or _GENERIC_GENERATION_FAILURE_PATTERN.search(value)
        or _HIGH_DEMAND_GENERATION_FAILURE_PATTERN.search(value)
    ):
        return True
    return bool(
        _CURRENT_GENERATION_FAILURE_PATTERN.search(value)
        and not _GENERIC_GENERATION_FAILURE_CLAUSE_PATTERN.search(value)
    )
_VISIBLE_MODEL_NAME_PATTERN = re.compile(
    r"\b(?:"
    r"nano[\s_-]+banana(?:[\s_-]+(?:"
    r"\d+(?:\.\d+)?(?:[\s_-]+(?:lite|pro|flash))?|lite|pro|flash"
    r"))?"
    r"|imagen(?:[\s_-]+\d+(?:\.\d+)?)?"
    r"|gemini(?:[\s_-]+\d+(?:\.\d+)?)?"
    r"|veo(?:[\s_-]+\d+(?:\.\d+)?)?"
    r")\b",
    re.I,
)
_MODEL_CONTROL_METADATA_SUFFIX_PATTERN = re.compile(
    r"^(?:\s*(?:crop[\s_-]*\d+[\s_:-]*\d+|\d+\s*:\s*\d+|x\s*\d+))*\s*$",
    re.I,
)
_MODEL_MENU_RECON_MODEL_PATTERN = re.compile(
    r"\b(?:"
    r"nano[\s_-]+banana(?:[\s_-]+(?:lite|pro|flash|\d+(?:\.\d+)?))?"
    r"|imagen(?:[\s_-]+\d+(?:\.\d+)?)?"
    r"|gemini(?:[\s_-]+\d+(?:\.\d+)?)?"
    r"|veo(?:[\s_-]+\d+(?:\.\d+)?)?"
    r")\b",
    re.I,
)
_MODEL_MENU_RECON_SENSITIVE_PATTERN = re.compile(
    r"account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|"
    r"email|gmail|manage\s+your\s+google|password|cookie|token|session|"
    r"prompt|what\s+do\s+you\s+want|@|https?://",
    re.I,
)


def find_installed_chrome() -> Path:
    """Find normal installed Google Chrome without inspecting browser data."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FlowAutomationError(
        "Installed Google Chrome was not found in a standard location. "
        "Install Chrome or launch it manually with --user-data-dir=.flow_chrome_profile."
    )


@dataclass(frozen=True)
class FlowInspection:
    current_url: str
    title: str
    document_ready_state: str
    visible_body_text_length: int
    authentication_state: str
    visible_buttons: tuple[str, ...]
    editable_controls: tuple[dict[str, Any], ...]
    prompt_control_candidates: tuple[dict[str, Any], ...]
    comboboxes: tuple[str, ...]
    tabs: tuple[str, ...]
    menus: tuple[str, ...]
    selected_controls: tuple[str, ...]
    generation_controls: tuple[str, ...]
    generation_action_controls: tuple[str, ...]
    model_labels: tuple[str, ...]
    model_related_text: tuple[str, ...]
    aspect_ratio_controls: tuple[str, ...]
    download_controls: tuple[str, ...]
    rendered_assets: tuple[dict[str, Any], ...]
    media: tuple[dict[str, Any], ...]
    iframes: tuple[dict[str, Any], ...]
    loading_or_error_states: tuple[str, ...]
    generation_control_resolution: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    screenshot_method: str | None = None
    screenshot_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModeDetection:
    """Auditable result of a read-only Flow generation-mode preflight."""

    mode: str
    confidence: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_visible_url(value: str | None) -> str | None:
    """Remove query strings/fragments so signed URLs and tokens are never persisted."""
    if not value or value.startswith(("data:", "blob:")):
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _flow_project_identity(value: str | None) -> dict[str, Any]:
    """Return only the normalized Flow URL and its proven project-path identity."""
    safe_url = _safe_visible_url(value)
    parts = urlsplit(safe_url or "")
    is_flow_url = (
        parts.scheme == "https" and parts.netloc.lower() == "flow.google.com"
    )
    project_id = parts.path.removeprefix("/project/").strip("/")
    is_project_url = bool(
        is_flow_url
        and parts.path.startswith("/project/")
        and project_id
        and "/" not in project_id
    )
    return {
        "current_url": safe_url if is_flow_url else None,
        "is_flow_url": is_flow_url,
        "is_project_url": is_project_url,
        "project_id": project_id if is_project_url else None,
    }


def _canonical_flow_project_reference(
    value: str | None,
) -> FlowProjectReference | None:
    """Return a query-free canonical UUID project reference, or no proof."""
    if not isinstance(value, str):
        return None
    identity = _flow_project_identity(value)
    project_id = identity.get("project_id")
    if not identity.get("is_project_url") or not isinstance(project_id, str):
        return None
    if not _FLOW_PROJECT_UUID_PATTERN.fullmatch(project_id):
        return None
    canonical_id = project_id.lower()
    return FlowProjectReference(
        project_id=canonical_id,
        project_url=f"https://flow.google.com/project/{canonical_id}",
        created_for_batch=True,
    )


def _safe_project_ui_label(value: Any) -> str | None:
    """Allow only short, generic project-navigation UI labels."""
    if not isinstance(value, str):
        return None
    clean = re.sub(r"\s+", " ", value).strip()
    if not clean or len(clean) > 80:
        return None
    if _PROJECT_UI_SENSITIVE_LABEL_PATTERN.search(clean):
        return None
    if not _PROJECT_UI_SAFE_LABEL_PATTERN.fullmatch(clean):
        return None
    return clean


def _safe_project_navigation_url(value: Any) -> str | None:
    """Retain only Flow home/project navigation URLs, without query or fragment."""
    if not isinstance(value, str):
        return None
    safe_url = _safe_visible_url(value)
    parts = urlsplit(safe_url or "")
    if parts.scheme != "https" or parts.netloc.lower() != "flow.google.com":
        return None
    if parts.path in {"", "/"}:
        return safe_url
    project_id = parts.path.removeprefix("/project/").strip("/")
    if parts.path.startswith("/project/") and project_id and "/" not in project_id:
        return safe_url
    return None


def _bounded_project_ui_controls(raw: Any, limit: int) -> tuple[dict[str, Any], ...]:
    """Defense-in-depth sanitizer for the browser-side project UI inventory."""
    if not 1 <= int(limit) <= _PROJECT_UI_RECON_MAX_CANDIDATES:
        raise ValueError(
            f"Project UI inspection limit must be 1 to {_PROJECT_UI_RECON_MAX_CANDIDATES}."
        )
    if not isinstance(raw, list):
        return ()

    allowed_roles = {"", "button", "link", "menuitem", "textbox"}
    allowed_regions = {"header", "sidebar", "navigation", "project_list", "dialog", "other"}
    allowed_reasons = {
        "creation_label",
        "dialog_control",
        "plus_control",
        "project_navigation",
    }
    sanitized: list[dict[str, Any]] = []
    for candidate in raw:
        if len(sanitized) >= limit:
            break
        if not isinstance(candidate, dict) or candidate.get("sensitive_context"):
            continue
        tag = str(candidate.get("tag") or "").lower()
        role = str(candidate.get("role") or "").lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", tag) or role not in allowed_roles:
            continue
        labels = {
            key: _safe_project_ui_label(candidate.get(key))
            for key in ("accessible_name", "aria_label", "title", "visible_text")
        }
        href = _safe_project_navigation_url(candidate.get("href"))
        if not any(labels.values()) and href is None:
            continue
        box = candidate.get("bounding_box")
        bounding_box = None
        if isinstance(box, dict):
            try:
                bounding_box = {
                    key: round(float(box[key]), 1)
                    for key in ("x", "y", "width", "height")
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                bounding_box = None
        ancestor = candidate.get("semantic_ancestor")
        safe_ancestor = None
        if isinstance(ancestor, dict):
            ancestor_role = str(ancestor.get("role") or "").lower()
            if ancestor_role in {
                "dialog",
                "listitem",
                "menu",
                "navigation",
                "tablist",
            }:
                safe_ancestor = {
                    "role": ancestor_role,
                    "name": _safe_project_ui_label(ancestor.get("name")),
                }
        region = str(candidate.get("region") or "other").lower()
        reasons = [
            str(reason)
            for reason in candidate.get("reasons", [])
            if str(reason) in allowed_reasons
        ]
        sanitized.append(
            {
                "tag": tag,
                "role": role or None,
                **labels,
                "connected": bool(candidate.get("connected")),
                "visible": bool(candidate.get("visible")),
                "enabled": bool(candidate.get("enabled")),
                "bounding_box": bounding_box,
                "semantic_ancestor": safe_ancestor,
                "region": region if region in allowed_regions else "other",
                "href": href,
                "reasons": reasons,
            }
        )
    return tuple(sanitized)


def _safe_layout_number(
    value: Any,
    *,
    minimum: float = 0.0,
    maximum: float = 100_000.0,
) -> int | float | None:
    """Normalize one finite, bounded CSS-pixel measurement."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    rounded = round(number, 3)
    return int(rounded) if rounded.is_integer() else rounded


def _safe_layout_box(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, dict):
        return None
    normalized = {
        key: _safe_layout_number(
            value.get(key),
            minimum=-100_000.0 if key in {"x", "y"} else 0.0,
        )
        for key in ("x", "y", "width", "height")
    }
    if any(item is None for item in normalized.values()):
        return None
    return normalized  # type: ignore[return-value]


def _safe_layout_count(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = float(value)
    if not math.isfinite(number):
        return 0
    return min(maximum, max(0, int(number)))


def _normalize_composer_generation_resolution(raw: Any) -> dict[str, Any]:
    """Whitelist bounded, prompt-free active-composer/Start ownership evidence."""
    source = raw if isinstance(raw, dict) else {}
    failure_reason = str(source.get("failure_reason") or "unavailable")
    allowed_failures = {
        "ready",
        "prompt_editor_ambiguous",
        "active_composer_unproven",
        "owned_generation_control_missing",
        "owned_generation_control_ambiguous",
        "control_scan_truncated",
        "dom_unavailable",
        "unavailable",
    }
    if failure_reason not in allowed_failures:
        failure_reason = "unavailable"

    candidates: list[dict[str, Any]] = []
    raw_candidates = source.get("candidates")
    if isinstance(raw_candidates, list):
        for index, value in enumerate(raw_candidates[:_COMPOSER_GENERATION_CONTROL_LIMIT]):
            item = value if isinstance(value, dict) else {}
            relationship = str(item.get("relationship") or "outside")
            if relationship not in {"descendant", "aria_owned", "outside"}:
                relationship = "outside"
            candidates.append(
                {
                    "candidate_index": _safe_layout_count(
                        item.get("candidate_index", index),
                        maximum=_COMPOSER_GENERATION_CONTROL_LIMIT - 1,
                    ),
                    "tag": re.sub(
                        r"[^a-z0-9-]", "", str(item.get("tag") or "")[:40].lower()
                    )
                    or None,
                    "role": re.sub(
                        r"[^a-z0-9_-]", "", str(item.get("role") or "")[:40].lower()
                    )
                    or None,
                    "relationship": relationship,
                    "connected": bool(item.get("connected")),
                    "visible": bool(item.get("visible")),
                    "enabled": bool(item.get("enabled")),
                    "positive_geometry": bool(item.get("positive_geometry")),
                    "pointer_events": (
                        "none" if str(item.get("pointer_events") or "").lower() == "none"
                        else "auto"
                    ),
                    "inactive_reasons": [
                        reason
                        for reason in item.get("inactive_reasons", [])
                        if reason
                        in {
                            "disconnected",
                            "hidden_or_zero_geometry",
                            "disabled_attribute",
                            "aria_disabled",
                            "pointer_events_none",
                        }
                    ][:5]
                    if isinstance(item.get("inactive_reasons"), list)
                    else [],
                    "bounding_box": _safe_layout_box(item.get("bounding_box")),
                }
            )

    composer_depth_raw = source.get("composer_depth")
    composer_depth = (
        int(composer_depth_raw)
        if isinstance(composer_depth_raw, (int, float))
        and not isinstance(composer_depth_raw, bool)
        and 1 <= int(composer_depth_raw) <= 20
        else None
    )
    composer_token_raw = source.get("composer_token")
    composer_token = (
        int(composer_token_raw)
        if isinstance(composer_token_raw, (int, float))
        and not isinstance(composer_token_raw, bool)
        and 1 <= int(composer_token_raw) <= 1_000_000_000
        else None
    )
    owned_label = re.sub(
        r"\s+", " ", str(source.get("owned_control_label") or "")
    ).strip()[:120]
    active_owned_count = _safe_layout_count(
        source.get("active_composer_generation_control_count"), maximum=1_000
    )
    structural_owned_count = _safe_layout_count(
        source.get(
            "structurally_owned_generation_control_count",
            active_owned_count,
        ),
        maximum=1_000,
    )
    actionable_owned_count = _safe_layout_count(
        source.get("actionable_owned_generation_control_count"), maximum=1_000
    )
    structural_ready = bool(
        source.get("structural_generation_control_ready", source.get("ready"))
    )
    generation_control_actionable = bool(
        source.get(
            "generation_control_actionable",
            structural_ready and actionable_owned_count == 1,
        )
    )
    return {
        "schema": "nano-banana-composer-generation/v1",
        "prompt_editor_count": _safe_layout_count(
            source.get("prompt_editor_count"), maximum=1_000
        ),
        "global_generation_control_count": _safe_layout_count(
            source.get("global_generation_control_count"), maximum=1_000
        ),
        "global_visible_generation_control_count": _safe_layout_count(
            source.get("global_visible_generation_control_count"), maximum=1_000
        ),
        "active_composer_count": _safe_layout_count(
            source.get("active_composer_count"), maximum=20
        ),
        "active_composer_generation_control_count": active_owned_count,
        "structurally_owned_generation_control_count": structural_owned_count,
        "actionable_owned_generation_control_count": actionable_owned_count,
        "ignored_generation_control_count": _safe_layout_count(
            source.get("ignored_generation_control_count"), maximum=1_000
        ),
        "composer_depth": composer_depth,
        "composer_token": composer_token,
        "composer_semantic": bool(source.get("composer_semantic")),
        "scan_truncated": bool(source.get("scan_truncated")),
        "structural_generation_control_ready": structural_ready,
        "generation_control_actionable": generation_control_actionable,
        # Compatibility alias: readiness is structural before prompt input.
        "ready": structural_ready,
        "failure_reason": failure_reason,
        "owned_control_label": owned_label or None,
        "prompt_populated": bool(source.get("prompt_populated")),
        "candidates": candidates,
        "remount_observed": bool(source.get("remount_observed")),
        "settle_elapsed_ms": _safe_layout_count(
            source.get("settle_elapsed_ms"), maximum=60_000
        ),
        "settle_poll_count": _safe_layout_count(
            source.get("settle_poll_count"), maximum=1_000
        ),
        "post_input_actionability_wait_elapsed_ms": _safe_layout_count(
            source.get("post_input_actionability_wait_elapsed_ms"), maximum=60_000
        ),
    }


def _safe_candidate_history_token(value: Any, *, limit: int = 100) -> str | None:
    """Retain only short, non-sensitive DOM identity tokens for diagnostics."""
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if (
        not clean
        or len(clean) > limit
        or "@" in clean
        or re.search(r"account|profile|email|token|session|cookie", clean, re.I)
        or not re.fullmatch(r"[A-Za-z0-9_.:\-]+", clean)
    ):
        return None
    return clean


def _normalize_candidate_history_snapshot(raw: Any) -> dict[str, Any]:
    """Whitelist one bounded result-history scroll/UUID ownership observation."""
    source = raw if isinstance(raw, dict) else {}
    raw_surface = source.get("surface")
    surface = raw_surface if isinstance(raw_surface, dict) else {}

    def scroll_number(value: Any) -> int | float | None:
        return _safe_layout_number(value, maximum=10_000_000.0)

    kind = str(surface.get("kind") or "")
    if kind not in {"tile_scroll_ancestor", "document_scrolling_element"}:
        kind = "unknown"
    tag = _safe_candidate_history_token(surface.get("tag"), limit=40)
    role = _safe_candidate_history_token(surface.get("role"), limit=40)
    token = _safe_candidate_history_token(surface.get("token"), limit=80)
    status = str(source.get("surface_status") or "unavailable")
    if status not in {"ready", "unavailable", "ambiguous"}:
        status = "unavailable"
    normalized_surface = None
    if status == "ready" and kind != "unknown" and tag is not None:
        normalized_surface = {
            "kind": kind,
            "tag": tag.lower(),
            "role": role.lower() if role else None,
            "id": _safe_candidate_history_token(surface.get("id")),
            "data_testid": _safe_candidate_history_token(
                surface.get("data_testid")
            ),
            "token": token,
            "connected": bool(surface.get("connected")),
            "scroll_top": scroll_number(surface.get("scroll_top")),
            "scroll_height": scroll_number(surface.get("scroll_height")),
            "client_height": scroll_number(surface.get("client_height")),
            "max_scroll_top": scroll_number(surface.get("max_scroll_top")),
        }
    return {
        "exact_match_count": _safe_layout_count(
            source.get("exact_match_count"), maximum=100
        ),
        "exact_ready": bool(source.get("exact_ready")),
        "mounted_candidate_tile_count": _safe_layout_count(
            source.get("mounted_candidate_tile_count"), maximum=10_000
        ),
        "surface_status": status,
        "surface_candidate_count": _safe_layout_count(
            source.get("surface_candidate_count"), maximum=10_000
        ),
        "surface": normalized_surface,
        "requested_scroll_top": scroll_number(source.get("requested_scroll_top")),
        "moved": bool(source.get("moved")),
    }


def _normalize_download_layout_candidate(raw: Any) -> dict[str, Any] | None:
    """Whitelist one candidate's safe structural layout facts."""
    if not isinstance(raw, dict):
        return None
    candidate_id = raw.get("candidate_id")
    if not isinstance(candidate_id, str) or not _FLOW_PROJECT_UUID_PATTERN.fullmatch(
        candidate_id
    ):
        candidate_id = None

    raw_controls = raw.get("more_options_controls")
    controls: list[dict[str, Any]] = []
    if isinstance(raw_controls, list):
        for control in raw_controls[:3]:
            if not isinstance(control, dict):
                continue
            controls.append(
                {
                    "connected": bool(control.get("connected")),
                    "visible": bool(control.get("visible")),
                    "enabled": bool(control.get("enabled")),
                    "bounding_box": _safe_layout_box(control.get("bounding_box")),
                }
            )

    raw_count = raw.get("more_options_count")
    more_options_count = (
        _safe_layout_count(raw_count, maximum=100)
        if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool)
        else len(controls)
    )
    raw_media_count = raw.get("data_media_id_count")
    data_media_id_count = _safe_layout_count(raw_media_count, maximum=100)
    primary = controls[0] if len(controls) == 1 and more_options_count == 1 else None
    return {
        "candidate_id": candidate_id,
        "connected": bool(raw.get("connected")),
        "visible": bool(raw.get("visible")),
        "bounding_box": _safe_layout_box(raw.get("bounding_box")),
        "data_media_id_count": data_media_id_count,
        "more_options_count": more_options_count,
        "more_options_connected": any(item["connected"] for item in controls),
        "more_options_visible": any(item["visible"] for item in controls),
        "more_options_enabled": any(item["enabled"] for item in controls),
        "more_options_bounding_box": (
            primary["bounding_box"] if primary is not None else None
        ),
        "more_options_controls": controls,
    }


def _normalize_download_layout_snapshot(raw: Any, limit: int) -> dict[str, Any]:
    """Whitelist bounded DOM measurements for download-layout reconnaissance."""
    if not 1 <= int(limit) <= _DOWNLOAD_LAYOUT_MAX_CANDIDATES:
        raise ValueError(
            f"Download-layout inspection limit must be 1 to {_DOWNLOAD_LAYOUT_MAX_CANDIDATES}."
        )
    source = raw if isinstance(raw, dict) else {}
    candidates: list[dict[str, Any]] = []
    raw_candidates = source.get("candidates")
    if isinstance(raw_candidates, list):
        for raw_candidate in raw_candidates:
            if len(candidates) >= int(limit):
                break
            candidate = _normalize_download_layout_candidate(raw_candidate)
            if candidate is not None and candidate["visible"]:
                candidates.append(candidate)

    target_candidate = _normalize_download_layout_candidate(
        source.get("target_candidate")
    )
    dimensions = {
        key: _safe_layout_number(source.get(key))
        for key in (
            "outer_width",
            "outer_height",
            "inner_width",
            "inner_height",
            "viewport_width",
            "viewport_height",
        )
    }
    dimensions["devicePixelRatio"] = _safe_layout_number(
        source.get("devicePixelRatio"), maximum=100.0
    )
    visible_count = source.get("visible_candidate_tile_count")
    identified_count = source.get("candidate_tiles_with_data_media_id_count")
    return {
        "browser_window": dimensions,
        "candidate_summary": {
            "visible_candidate_tile_count": (
                _safe_layout_count(visible_count, maximum=10_000)
            ),
            "candidate_tiles_with_data_media_id_count": (
                _safe_layout_count(identified_count, maximum=10_000)
            ),
            "candidate_limit": int(limit),
            "candidate_count": len(candidates),
            "truncated": bool(source.get("truncated")),
            "candidates": candidates,
        },
        "target_candidate": target_candidate,
    }


def _safe_model_menu_text(value: Any) -> str | None:
    """Retain only short model-bearing UI labels with no private context."""
    if not isinstance(value, str):
        return None
    clean = re.sub(r"\s+", " ", value).strip()
    if (
        not clean
        or len(clean) > 120
        or _MODEL_MENU_RECON_SENSITIVE_PATTERN.search(clean)
        or not _MODEL_MENU_RECON_MODEL_PATTERN.search(clean)
    ):
        return None
    return clean


def _safe_model_menu_token(value: Any, *, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    clean = re.sub(r"\s+", " ", value).strip()
    if (
        not clean
        or len(clean) > limit
        or _MODEL_MENU_RECON_SENSITIVE_PATTERN.search(clean)
        or not re.fullmatch(r"[A-Za-z0-9_.:\- ]+", clean)
    ):
        return None
    return clean


def _normalize_model_menu_attributes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "data-testid",
        "data-test-id",
        "data-control",
        "data-control-name",
        "data-state",
        "data-value",
        "data-type",
        "data-model",
    }
    result: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key).lower()
        if normalized_key not in allowed:
            continue
        safe_value = _safe_model_menu_token(value, limit=100)
        if safe_value is not None:
            result[normalized_key] = safe_value
    return result


def _normalize_model_menu_named_control(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    visible_text = _safe_model_menu_text(raw.get("visible_text"))
    accessible_name = _safe_model_menu_text(raw.get("accessible_name"))
    aria_label = _safe_model_menu_text(raw.get("aria_label"))
    if not any((visible_text, accessible_name, aria_label)):
        return None
    tag = str(raw.get("tag") or "").lower()
    role = str(raw.get("role") or "").lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", tag):
        return None
    if role not in {"", "button", "combobox", "option", "menuitem", "radio"}:
        return None

    ancestor = raw.get("semantic_ancestor")
    safe_ancestor = None
    if isinstance(ancestor, dict):
        ancestor_tag = str(ancestor.get("tag") or "").lower()
        ancestor_role = str(ancestor.get("role") or "").lower()
        if re.fullmatch(r"[a-z][a-z0-9-]{0,39}", ancestor_tag) and ancestor_role in {
            "",
            "dialog",
            "form",
            "group",
            "region",
            "toolbar",
        }:
            safe_ancestor = {
                "tag": ancestor_tag,
                "role": ancestor_role or None,
                "accessible_name": _safe_model_menu_text(
                    ancestor.get("accessible_name")
                ),
                "is_composer_root": bool(ancestor.get("is_composer_root")),
            }

    def aria_state(name: str, allowed: set[str]) -> str | None:
        value = str(raw.get(name) or "").lower()
        return value if value in allowed else None

    normalized_visible = _normalize_model_name(visible_text)
    normalized_accessible = _normalize_model_name(accessible_name)
    return {
        "tag": tag,
        "role": role or None,
        "visible_text": visible_text,
        "accessible_name": accessible_name,
        "aria_label": aria_label,
        "connected": bool(raw.get("connected")),
        "visible": bool(raw.get("visible")),
        "enabled": bool(raw.get("enabled")),
        "bounding_box": _safe_layout_box(raw.get("bounding_box")),
        "aria_haspopup": _safe_model_menu_token(raw.get("aria_haspopup"), limit=30),
        "aria_expanded": aria_state("aria_expanded", {"true", "false"}),
        "aria_controls": _safe_model_menu_token(raw.get("aria_controls")),
        "aria_owns": _safe_model_menu_token(raw.get("aria_owns")),
        "selected": bool(raw.get("selected")),
        "aria_selected": aria_state("aria_selected", {"true", "false", "mixed"}),
        "aria_checked": aria_state("aria_checked", {"true", "false", "mixed"}),
        "checked": raw.get("checked") if isinstance(raw.get("checked"), bool) else None,
        "stable_attributes": _normalize_model_menu_attributes(
            raw.get("stable_attributes")
        ),
        "semantic_ancestor": safe_ancestor,
        "normalized_visible_text": normalized_visible or None,
        "normalized_accessible_name": normalized_accessible or None,
    }


def _normalize_model_menu_popup(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    relationship = str(raw.get("ownership_relationship") or "")
    allowed_relationships = {
        "aria-controls",
        "aria-controls-descendant",
        "aria-owns",
        "aria-owns-descendant",
        "popup-aria-labelledby",
    }
    if relationship not in allowed_relationships:
        return None
    tag = str(raw.get("tag") or "").lower()
    role = str(raw.get("role") or "").lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", tag):
        return None
    if role not in {"", "dialog", "group", "listbox", "menu"}:
        return None
    return {
        "tag": tag,
        "role": role or None,
        "id": _safe_model_menu_token(raw.get("id")),
        "connected": bool(raw.get("connected")),
        "visible": bool(raw.get("visible")),
        "bounding_box": _safe_layout_box(raw.get("bounding_box")),
        "ownership_relationship": relationship,
        "stable_attributes": _normalize_model_menu_attributes(
            raw.get("stable_attributes")
        ),
    }


def _normalize_model_menu_recon_snapshot(raw: Any, limit: int) -> dict[str, Any]:
    """Whitelist bounded, model-only facts from the already-open selector menu."""
    if not 1 <= int(limit) <= _MODEL_MENU_RECON_MAX_CONTROLS:
        raise ValueError(
            "Model-menu inspection limit must be 1 to "
            f"{_MODEL_MENU_RECON_MAX_CONTROLS}."
        )
    source = raw if isinstance(raw, dict) else {}

    triggers: list[dict[str, Any]] = []
    raw_triggers = source.get("trigger_candidates")
    if isinstance(raw_triggers, list):
        for raw_trigger in raw_triggers:
            if len(triggers) >= int(limit):
                break
            trigger = _normalize_model_menu_named_control(raw_trigger)
            if trigger is not None:
                triggers.append(trigger)

    popups: list[dict[str, Any]] = []
    raw_popups = source.get("popups")
    if isinstance(raw_popups, list):
        for raw_popup in raw_popups:
            if len(popups) >= min(int(limit), 10):
                break
            popup = _normalize_model_menu_popup(raw_popup)
            if popup is not None and popup["connected"] and popup["visible"]:
                popups.append(popup)

    options: list[dict[str, Any]] = []
    raw_options = source.get("model_options")
    allowed_ownerships = {
        (popup["id"], popup["ownership_relationship"]) for popup in popups
    }
    if isinstance(raw_options, list):
        for raw_option in raw_options:
            if len(options) >= int(limit):
                break
            if not isinstance(raw_option, dict):
                continue
            relationship = str(raw_option.get("ownership_relationship") or "")
            popup_id = _safe_model_menu_token(raw_option.get("popup_id"))
            if (popup_id, relationship) not in allowed_ownerships:
                continue
            option = _normalize_model_menu_named_control(raw_option)
            if option is None or not option["connected"] or not option["visible"]:
                continue
            option["popup_ownership"] = {
                "popup_id": popup_id,
                "relationship": relationship,
            }
            options.append(option)

    def exact_count(target: str) -> int:
        return sum(
            1
            for option in options
            if option["enabled"]
            and target
            in {
                option["normalized_visible_text"],
                option["normalized_accessible_name"],
            }
        )

    target_count = exact_count("nano banana 2")
    lite_count = exact_count("nano banana lite")
    unique_trigger = triggers[0] if len(triggers) == 1 else None
    if unique_trigger is None:
        status = "current_model_trigger_not_unique"
    elif unique_trigger["aria_expanded"] != "true":
        status = "model_menu_not_open"
    elif not popups:
        status = "owned_popup_not_found"
    else:
        status = "owned_popup_found"
    return {
        "schema": "nano-banana-flow-model-menu-inspection/v1",
        "inspection_mode": "read_only_dom",
        "control_limit": int(limit),
        "status": status,
        "current_model_trigger_count": len(triggers),
        "current_model_trigger": unique_trigger,
        "current_model_trigger_candidates": triggers,
        "open_popup_count": len(popups),
        "open_popups": popups,
        "model_option_count": len(options),
        "model_options": options,
        "truncated": bool(source.get("truncated"))
        or (isinstance(raw_triggers, list) and len(raw_triggers) > int(limit))
        or (isinstance(raw_options, list) and len(raw_options) > int(limit)),
        "target_evidence": {
            "normalized_target": "nano banana 2",
            "exact_visible_enabled_target_count": target_count,
            "exact_visible_enabled_lite_count": lite_count,
            "lite_and_target_distinguishable": target_count > 0 and lite_count > 0,
            "exact_target_proven": (
                status == "owned_popup_found" and target_count == 1
            ),
        },
        "safety": {
            "clicks": 0,
            "hovers": 0,
            "keyboard_inputs": 0,
            "navigations": 0,
            "screenshots": 0,
            "focus_or_window_actions": 0,
            "flow_mutations": 0,
        },
    }


def _safe_candidate_asset_url(value: str | None) -> str | None:
    """Omit Flow's transient opaque ASB source; candidate identity is data-media-id."""
    safe_url = _safe_visible_url(value)
    if not safe_url:
        return None
    parts = urlsplit(safe_url)
    if parts.netloc.lower() == "flow.google.com" and parts.path.startswith("/asb/"):
        return None
    return safe_url


def _trace_host_allowed(host: str) -> bool:
    host = host.lower()
    return host in _TRACE_ALLOWED_HOSTS or host == "googleusercontent.com" or host.endswith(
        ".googleusercontent.com"
    )


def _trace_path_shape(path: str, candidate_id: str) -> tuple[str, bool]:
    candidate_in_path = False
    safe_segments = []
    segments = [segment for segment in path.split("/") if segment]
    asb_path = bool(segments and segments[0].lower() == "asb")
    for index, segment in enumerate(segments):
        if segment == candidate_id:
            safe_segments.append("[candidate-id]")
            candidate_in_path = True
        elif asb_path and index > 0:
            safe_segments.append("[opaque]")
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9._-]{0,31}", segment):
            safe_segments.append(segment)
        elif re.fullmatch(r"\d+", segment):
            safe_segments.append("[number]")
        else:
            safe_segments.append("[opaque]")
    return "/" + "/".join(safe_segments), candidate_in_path


def _sanitize_trace_url(value: str | None, candidate_id: str) -> dict[str, Any] | None:
    """Return only scheme, allowed host, redacted path shape, and redaction flags."""
    if not value:
        return None
    if value.startswith("blob:"):
        inner = urlsplit(value.removeprefix("blob:"))
        host = inner.netloc.lower()
        if not _trace_host_allowed(host):
            return None
        return {
            "scheme": "blob",
            "host": host,
            "path_shape": "/[blob]",
            "query_redacted": bool(inner.query),
            "fragment_redacted": bool(inner.fragment),
            "candidate_id_in_path": candidate_id in inner.path,
        }
    parts = urlsplit(value)
    host = parts.netloc.lower()
    if parts.scheme not in {"http", "https"} or not _trace_host_allowed(host):
        return None
    path_shape, candidate_in_path = _trace_path_shape(parts.path, candidate_id)
    return {
        "scheme": parts.scheme,
        "host": host,
        "path_shape": path_shape,
        "query_redacted": bool(parts.query),
        "fragment_redacted": bool(parts.fragment),
        "candidate_id_in_path": candidate_in_path,
    }


def _safe_trace_header(value: str | None) -> str | None:
    if not value:
        return None
    text = _safe_label(value.replace("\r", " ").replace("\n", " "))
    text = re.sub(
        r"(?i)\b(token|secret|authorization|cookie|password)\s*=\s*(?:\"[^\"]*\"|[^;]+)",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"https?://[^\s;]+", "[redacted-url]", text)
    return _clean_text(text, 200) or None


def _correlate_download_trace(
    candidate_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    qualifying = [
        event
        for event in events
        if event.get("kind") == "response" and event.get("candidate_bytes") is True
    ]
    exact = [
        event
        for event in qualifying
        if (event.get("url") or {}).get("candidate_id_in_path") is True
    ]
    if len(exact) == 1 and len(qualifying) == 1:
        outcome = "exact_candidate_id"
    elif len(qualifying) == 1:
        outcome = "single_qualifying_response_in_action_window"
    elif len(qualifying) > 1:
        outcome = "ambiguous_multiple_qualifying_responses"
    else:
        outcome = "no_qualifying_response"
    return {
        "candidate_id": candidate_id,
        "outcome": outcome,
        "qualifying_response_count": len(qualifying),
        "exact_candidate_path_count": len(exact),
    }


def _normalize_trace_hook_event(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    allowed_kinds = {
        "window_open",
        "blob_created",
        "anchor_programmatic_click",
        "anchor_activation",
        "download_control_activation",
        "input_event",
        "anchor_added",
        "fetch_call",
        "fetch_response",
        "fetch_failed",
        "dom_status",
        "menu_state",
        "candidate_menu_request",
        "download_control_seen",
    }
    kind = str(raw.get("kind") or "")
    if kind not in allowed_kinds:
        return None
    event: dict[str, Any] = {"kind": kind}
    offset = raw.get("offset_ms")
    if isinstance(offset, (int, float)) and 0 <= offset <= 120_000:
        event["offset_ms"] = round(float(offset), 1)
    url = raw.get("url")
    if isinstance(url, dict):
        scheme = str(url.get("scheme") or "")
        host = str(url.get("host") or "").lower()
        if (scheme in {"http", "https"} and _trace_host_allowed(host)) or (
            scheme == "blob" and _trace_host_allowed(host)
        ):
            event["url"] = {
                "scheme": scheme,
                "host": host,
                "path_shape": _clean_text(str(url.get("path_shape") or "/[opaque]"), 160),
                "query_redacted": bool(url.get("query_redacted")),
                "fragment_redacted": bool(url.get("fragment_redacted")),
                "candidate_id_in_path": bool(url.get("candidate_id_in_path")),
            }
    for key in (
        "method",
        "resource_type",
        "object_type",
        "tag",
        "role",
        "matched_keyword",
        "event_type",
        "target_scope",
    ):
        value = _clean_text(str(raw.get(key) or ""), 80)
        if value:
            event[key] = value
    if kind == "input_event":
        event_type = event.get("event_type")
        if event_type not in {
            "pointerdown",
            "mousedown",
            "pointerup",
            "mouseup",
            "click",
            "keydown",
            "keyup",
        }:
            return None
        target_label = _safe_label(str(raw.get("target_label") or ""))
        if target_label:
            event["target_label"] = target_label
        key = str(raw.get("key") or "")
        if key in {"Enter", " ", "Space", "Spacebar"}:
            event["key"] = "Space" if key != "Enter" else "Enter"
        for field in ("button", "buttons"):
            value = raw.get(field)
            if isinstance(value, (int, float)) and -1 <= int(value) <= 31:
                event[field] = int(value)
    for key in ("content_type", "content_disposition", "download_attribute"):
        value = _safe_trace_header(str(raw.get(key) or ""))
        if value:
            event[key] = value
    for key in ("status", "object_size"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            event[key] = int(value)
    if "is_trusted" in raw:
        event["is_trusted"] = bool(raw.get("is_trusted"))
    if "default_prevented" in raw:
        event["default_prevented"] = bool(raw.get("default_prevented"))
    if kind == "download_control_seen":
        event["control"] = _normalize_download_control_snapshot(
            {
                "tag": raw.get("tag"),
                "role": raw.get("role"),
                "visible_text": raw.get("visible_text"),
                "aria_label": raw.get("aria_label"),
                "title": raw.get("title"),
                "disabled": raw.get("disabled"),
                "aria_disabled": raw.get("aria_disabled"),
                "tabindex": raw.get("tabindex"),
                "pointer_events": raw.get("pointer_events"),
                "connected": raw.get("target_connected"),
                "visible": raw.get("target_visible"),
                "bounding_box": raw.get("bounding_box"),
                "bounding_box_samples": [raw.get("bounding_box")],
                "center_top_belongs_to_control": raw.get(
                    "center_top_belongs_to_control"
                ),
            }
        )
    for key in (
        "menu_visible",
        "menu_connected",
        "target_connected",
        "target_visible",
        "disabled",
    ):
        if key in raw:
            event[key] = bool(raw.get(key))
    for key in ("live_menu_count", "download_match_count"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and 0 <= int(value) <= 100:
            event[key] = int(value)
    return event


def _normalize_trace_node(raw: Any, *, include_text: bool = True) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    node: dict[str, Any] = {}
    for key in ("tag", "role"):
        value = _clean_text(str(raw.get(key) or ""), 80)
        if value:
            node[key] = value.lower()
    if include_text:
        for key in ("text", "aria_label", "title"):
            value = _safe_label(str(raw.get(key) or ""))
            if value:
                node[key] = value
    class_names = raw.get("class_summary")
    if isinstance(class_names, (list, tuple)):
        safe_classes = [
            value
            for value in (
                re.sub(r"[^A-Za-z0-9_-]", "", str(item))[:60]
                for item in class_names[:6]
            )
            if value
        ]
        if safe_classes:
            node["class_summary"] = safe_classes
    for key in ("pointer_events", "visibility", "opacity"):
        value = _clean_text(str(raw.get(key) or ""), 40)
        if value:
            node[key] = value.lower() if key != "opacity" else value
    raw_box = raw.get("bounding_box")
    if isinstance(raw_box, dict):
        box: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            value = raw_box.get(key)
            if not isinstance(value, (int, float)):
                box = {}
                break
            box[key] = round(float(value), 3)
        if box:
            node["bounding_box"] = box
    for key in (
        "connected",
        "inside_menu",
        "top_contains_control",
        "control_contains_top",
        "same_parent",
        "same_toolbar",
        "same_candidate_tile",
        "top_closest_button_is_control",
        "event_path_reaches_control",
        "material_snackbar",
    ):
        if key in raw:
            node[key] = bool(raw.get(key))
    composed_ownership = _clean_text(str(raw.get("composed_ownership") or ""), 40)
    if composed_ownership in {"self", "descendant", "composed_descendant"}:
        node["composed_ownership"] = composed_ownership
    return node or None


def _normalize_download_control_snapshot(raw: Any) -> dict[str, Any]:
    """Whitelist safe DOM facts for the exact Download menu item."""
    if not isinstance(raw, dict):
        raise FlowAutomationError("DOWNLOAD_CONTROL_INSPECTION_FAILED: no structural result.")
    snapshot: dict[str, Any] = {}
    for key in (
        "tag",
        "role",
        "aria_disabled",
        "aria_haspopup",
        "aria_expanded",
        "tabindex",
        "pointer_events",
        "opacity",
        "visibility",
        "display",
    ):
        value = _clean_text(str(raw.get(key) or ""), 80)
        snapshot[key] = (
            value.lower()
            if key in {"tag", "role", "pointer_events", "visibility", "display"}
            else value or None
        )
    for key in ("visible_text", "aria_label", "title"):
        value = _safe_label(str(raw.get(key) or ""))
        snapshot[key] = value or None
    for key in (
        "disabled",
        "connected",
        "visible",
        "center_top_belongs_to_control",
        "has_aria_controls",
        "has_aria_owns",
    ):
        snapshot[key] = bool(raw.get(key))

    def box(value: Any) -> dict[str, float] | None:
        if not isinstance(value, dict):
            return None
        result = {}
        for key in ("x", "y", "width", "height"):
            number = value.get(key)
            if not isinstance(number, (int, float)):
                return None
            result[key] = round(float(number), 3)
        return result

    snapshot["bounding_box"] = box(raw.get("bounding_box"))
    snapshot["bounding_box_samples"] = [
        normalized
        for value in (raw.get("bounding_box_samples") or [])[:8]
        if (normalized := box(value)) is not None
    ]
    snapshot["center_top"] = _normalize_trace_node(raw.get("center_top"))
    snapshot["elements_from_center"] = [
        normalized
        for value in (raw.get("elements_from_center") or [])[:8]
        if (normalized := _normalize_trace_node(value)) is not None
    ]
    snapshot["descendants"] = [
        normalized
        for value in (raw.get("descendants") or [])[:16]
        if (normalized := _normalize_trace_node(value)) is not None
    ]
    snapshot["ancestors"] = [
        normalized
        for value in (raw.get("ancestors") or [])[:8]
        if (normalized := _normalize_trace_node(value, include_text=False)) is not None
    ]
    snapshot["active_element"] = _normalize_trace_node(raw.get("active_element"))
    snapshot["has_descendants"] = bool(snapshot["descendants"])
    snapshot["overlay_above"] = not snapshot["center_top_belongs_to_control"]
    return snapshot


def _normalize_submenu_box(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        value = raw.get(key)
        if not isinstance(value, (int, float)):
            return None
        result[key] = round(float(value), 3)
    return result


def _normalize_download_submenu_snapshot(raw: Any) -> dict[str, Any]:
    """Whitelist user-visible menu structure without retaining framework internals."""
    if not isinstance(raw, dict):
        raise FlowAutomationError("DOWNLOAD_SUBMENU_INSPECTION_FAILED: no DOM snapshot.")

    def bounded_index(value: Any) -> int | None:
        if isinstance(value, (int, float)) and 0 <= int(value) <= 100:
            return int(value)
        return None

    def text(value: Any, limit: int = 180) -> str | None:
        return _safe_label(_clean_text(str(value or ""), limit)) or None

    def icon(raw_icon: Any) -> dict[str, Any] | None:
        if not isinstance(raw_icon, dict):
            return None
        result = {
            "tag": text(raw_icon.get("tag"), 40),
            "role": text(raw_icon.get("role"), 40),
            "text": text(raw_icon.get("text"), 80),
            "aria_label": text(raw_icon.get("aria_label"), 80),
            "title": text(raw_icon.get("title"), 80),
        }
        return {key: value for key, value in result.items() if value is not None} or None

    menus: list[dict[str, Any]] = []
    for raw_menu in (raw.get("menus") or [])[:8]:
        if not isinstance(raw_menu, dict):
            continue
        menu_index = bounded_index(raw_menu.get("index"))
        if menu_index is None:
            continue
        menu: dict[str, Any] = {
            "index": menu_index,
            "tag": (text(raw_menu.get("tag"), 40) or "").lower() or None,
            "role": (text(raw_menu.get("role"), 40) or "").lower() or None,
            "visible_text": text(raw_menu.get("visible_text")),
            "aria_label": text(raw_menu.get("aria_label"), 120),
            "title": text(raw_menu.get("title"), 120),
            "connected": bool(raw_menu.get("connected")),
            "visible": bool(raw_menu.get("visible")),
            "bounding_box": _normalize_submenu_box(raw_menu.get("bounding_box")),
            "items": [],
        }
        for raw_item in (raw_menu.get("items") or [])[:32]:
            if not isinstance(raw_item, dict):
                continue
            item_index = bounded_index(raw_item.get("index"))
            if item_index is None:
                continue
            item: dict[str, Any] = {
                "index": item_index,
                "tag": (text(raw_item.get("tag"), 40) or "").lower() or None,
                "role": (text(raw_item.get("role"), 40) or "").lower() or None,
                "visible_text": text(raw_item.get("visible_text")),
                "aria_label": text(raw_item.get("aria_label"), 120),
                "title": text(raw_item.get("title"), 120),
                "disabled": bool(raw_item.get("disabled")),
                "aria_disabled": text(raw_item.get("aria_disabled"), 20),
                "tabindex": text(raw_item.get("tabindex"), 20),
                "aria_haspopup": text(raw_item.get("aria_haspopup"), 40),
                "aria_expanded": text(raw_item.get("aria_expanded"), 20),
                "connected": bool(raw_item.get("connected")),
                "visible": bool(raw_item.get("visible")),
                "bounding_box": _normalize_submenu_box(raw_item.get("bounding_box")),
                "icons": [],
            }
            item["icons"] = [
                normalized
                for raw_icon in (raw_item.get("icons") or [])[:8]
                if (normalized := icon(raw_icon)) is not None
            ]
            menu["items"].append(item)
        menus.append(menu)

    raw_parent = raw.get("download_parent") or {}
    relationship = raw_parent.get("relationship") or {}
    status = str(relationship.get("status") or "unresolved")
    if status not in {"explicit", "inferred_visible_sibling", "unresolved", "absent"}:
        status = "unresolved"
    bases = []
    for value in (relationship.get("basis") or [])[:8]:
        normalized = text(value, 80)
        if normalized in {
            "aria_controls",
            "aria_owns",
            "aria_labelledby",
            "dom_descendant",
            "visible_sibling_with_submenu_indicator",
        }:
            bases.append(normalized)
    submenu_indices = [
        index
        for value in (relationship.get("submenu_menu_indices") or [])[:8]
        if (index := bounded_index(value)) is not None
    ]
    match_count = bounded_index(raw_parent.get("match_count")) or 0
    parent_menu_index = bounded_index(raw_parent.get("parent_menu_index"))
    parent_item_index = bounded_index(raw_parent.get("parent_item_index"))
    normalized = {
        "visible_menu_count": len(menus),
        "menus": menus,
        "download_parent": {
            "match_count": match_count,
            "parent_menu_index": parent_menu_index,
            "parent_item_index": parent_item_index,
            "aria_haspopup": text(raw_parent.get("aria_haspopup"), 40),
            "aria_expanded": text(raw_parent.get("aria_expanded"), 20),
            "has_aria_controls": bool(raw_parent.get("has_aria_controls")),
            "has_aria_owns": bool(raw_parent.get("has_aria_owns")),
            "submenu_indicator": [
                normalized_icon
                for raw_icon in (raw_parent.get("submenu_indicator") or [])[:8]
                if (normalized_icon := icon(raw_icon)) is not None
            ],
            "relationship": {
                "status": status,
                "basis": bases,
                "submenu_menu_indices": submenu_indices,
            },
        },
        "submenu_detected": bool(raw.get("submenu_detected")),
    }
    return normalized


def _download_submenu_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    """Ignore animation coordinates while checking structural stability."""
    return (
        snapshot.get("visible_menu_count"),
        tuple(
            (
                menu.get("index"),
                tuple(
                    (
                        item.get("visible_text"),
                        item.get("aria_label"),
                        item.get("aria_haspopup"),
                        item.get("aria_expanded"),
                        item.get("disabled"),
                    )
                    for item in menu.get("items") or []
                ),
            )
            for menu in snapshot.get("menus") or []
        ),
        (snapshot.get("download_parent") or {}).get("match_count"),
        ((snapshot.get("download_parent") or {}).get("relationship") or {}).get("status"),
        tuple(
            ((snapshot.get("download_parent") or {}).get("relationship") or {}).get(
                "submenu_menu_indices"
            )
            or []
        ),
    )


def _stable_download_control_box(
    snapshot: dict[str, Any],
    *,
    tolerance: float = 1.0,
    require_pointer_hit: bool = True,
) -> dict[str, float]:
    """Fail closed unless the exact menu item is visible, enabled, stable, and unobscured."""
    if not snapshot.get("connected") or not snapshot.get("visible"):
        raise FlowAutomationError("DOWNLOAD_CONTROL_NOT_ACTIONABLE: item is not visible and connected.")
    aria_disabled = str(snapshot.get("aria_disabled") or "").lower()
    if snapshot.get("disabled") or aria_disabled == "true":
        raise FlowAutomationError("DOWNLOAD_CONTROL_DISABLED: disabled or aria-disabled is true.")
    if require_pointer_hit and str(snapshot.get("pointer_events") or "").lower() == "none":
        raise FlowAutomationError("DOWNLOAD_CONTROL_NOT_ACTIONABLE: pointer-events is none.")
    if require_pointer_hit and not snapshot.get("center_top_belongs_to_control"):
        raise FlowAutomationError(
            "DOWNLOAD_CONTROL_OVERLAID: elementFromPoint at the center is not the Download item or its descendant."
        )
    samples = snapshot.get("bounding_box_samples") or []
    if len(samples) < 2:
        raise FlowAutomationError("DOWNLOAD_CONTROL_BBOX_UNSTABLE: fewer than two samples.")
    stable_samples = samples[-3:] if len(samples) >= 3 else samples
    reference = stable_samples[-1]
    for sample in stable_samples:
        if sample["width"] <= 0 or sample["height"] <= 0:
            raise FlowAutomationError("DOWNLOAD_CONTROL_BBOX_UNSTABLE: non-positive box.")
        if any(
            abs(sample[key] - reference[key]) > tolerance
            for key in ("x", "y", "width", "height")
        ):
            raise FlowAutomationError(
                "DOWNLOAD_CONTROL_BBOX_UNSTABLE: position or size changed beyond tolerance."
            )
    return reference


def _stable_generation_submit_box(
    snapshot: dict[str, Any],
    *,
    tolerance: float = 1.0,
) -> dict[str, float]:
    """Fail closed unless the exact generation submit button is safely actionable."""
    if not snapshot.get("connected") or not snapshot.get("visible"):
        raise FlowAutomationError(
            "GENERATION_SUBMIT_NOT_ACTIONABLE: button is not visible and connected."
        )
    aria_disabled = str(snapshot.get("aria_disabled") or "").lower()
    if snapshot.get("disabled") or aria_disabled == "true":
        raise FlowAutomationError(
            "GENERATION_SUBMIT_DISABLED: disabled or aria-disabled is true."
        )
    if str(snapshot.get("pointer_events") or "").lower() == "none":
        raise FlowAutomationError(
            "GENERATION_SUBMIT_NOT_ACTIONABLE: pointer-events is none."
        )
    if not snapshot.get("center_top_belongs_to_control"):
        raise FlowAutomationError(
            "GENERATION_SUBMIT_OVERLAID: elementFromPoint at the center is not "
            "the Start generation button or its descendant."
        )
    samples = snapshot.get("bounding_box_samples") or []
    if len(samples) < 2:
        raise FlowAutomationError(
            "GENERATION_SUBMIT_BBOX_UNSTABLE: fewer than two samples."
        )
    stable_samples = samples[-3:] if len(samples) >= 3 else samples
    reference = stable_samples[-1]
    for sample in stable_samples:
        if sample["width"] <= 0 or sample["height"] <= 0:
            raise FlowAutomationError(
                "GENERATION_SUBMIT_BBOX_UNSTABLE: non-positive box."
            )
        if any(
            abs(sample[key] - reference[key]) > tolerance
            for key in ("x", "y", "width", "height")
        ):
            raise FlowAutomationError(
                "GENERATION_SUBMIT_BBOX_UNSTABLE: position or size changed "
                "beyond tolerance."
            )
    return reference


def _stable_candidate_more_options_box(
    snapshot: dict[str, Any],
    *,
    tolerance: float = 1.0,
) -> dict[str, float]:
    """Fail closed unless candidate More options is stable and unobscured."""
    if not snapshot.get("connected") or not snapshot.get("visible"):
        raise FlowAutomationError(
            "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE: control is not visible and connected."
        )
    aria_disabled = str(snapshot.get("aria_disabled") or "").lower()
    if snapshot.get("disabled") or aria_disabled == "true":
        raise FlowAutomationError(
            "CANDIDATE_MORE_OPTIONS_DISABLED: disabled or aria-disabled is true."
        )
    if str(snapshot.get("pointer_events") or "").lower() == "none":
        raise FlowAutomationError(
            "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE: pointer-events is none."
        )
    try:
        opacity = float(snapshot.get("opacity"))
    except (TypeError, ValueError):
        opacity = None
    if (
        opacity is not None
        and opacity <= 0.01
        or str(snapshot.get("visibility") or "").lower() == "hidden"
        or str(snapshot.get("display") or "").lower() == "none"
    ):
        raise FlowAutomationError(
            "CANDIDATE_MORE_OPTIONS_HIDDEN: computed style is not painted; "
            f"opacity={snapshot.get('opacity') or '(unreadable)'}; "
            f"visibility={snapshot.get('visibility') or '(unreadable)'}; "
            f"display={snapshot.get('display') or '(unreadable)'}."
        )
    samples = snapshot.get("bounding_box_samples") or []
    if len(samples) < 2:
        raise FlowAutomationError(
            "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE: fewer than two samples."
        )
    stable_samples = samples[-3:] if len(samples) >= 3 else samples
    reference = stable_samples[-1]
    for sample in stable_samples:
        if sample["width"] <= 0 or sample["height"] <= 0:
            raise FlowAutomationError(
                "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE: non-positive box."
            )
        if any(
            abs(sample[key] - reference[key]) > tolerance
            for key in ("x", "y", "width", "height")
        ):
            raise FlowAutomationError(
                "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE: position or size changed "
                "beyond tolerance."
            )
    _candidate_more_options_safe_point(snapshot, reference)
    return reference


def _candidate_more_options_hit_diagnostic(snapshot: dict[str, Any]) -> str:
    """Render bounded, safe structural facts for More-options hit-test failures."""
    entries = []
    for probe in (snapshot.get("hit_test_probes") or [])[:5]:
        if not isinstance(probe, dict):
            continue
        top = probe.get("top") or {}
        if not isinstance(top, dict):
            top = {}
        classes = ".".join(str(value) for value in (top.get("class_summary") or [])[:6])
        box = top.get("bounding_box") or {}
        if isinstance(box, dict) and all(
            isinstance(box.get(key), (int, float))
            for key in ("x", "y", "width", "height")
        ):
            box_text = "/".join(
                f"{float(box[key]):.1f}" for key in ("x", "y", "width", "height")
            )
        else:
            box_text = "unreadable"
        entries.append(
            f"{probe.get('name') or 'unknown'}["
            + f"tag={top.get('tag') or 'none'};role={top.get('role') or 'none'};"
            + f"aria={top.get('aria_label') or 'none'};class={classes or 'none'};"
            + f"pointer={top.get('pointer_events') or 'unreadable'};"
            + f"visibility={top.get('visibility') or 'unreadable'};"
            + f"opacity={top.get('opacity') or 'unreadable'};box={box_text};"
            + f"connected={bool(top.get('connected'))};"
            + f"control_relation={probe.get('ownership') or 'unowned'};"
            + f"top_contains_control={bool(top.get('top_contains_control'))};"
            + f"control_contains_top={bool(top.get('control_contains_top'))};"
            + f"same_parent={bool(top.get('same_parent'))};"
            + f"same_toolbar={bool(top.get('same_toolbar'))};"
            + f"same_candidate_tile={bool(top.get('same_candidate_tile'))};"
            + f"closest_button_is_control={bool(top.get('top_closest_button_is_control'))};"
            + f"event_path_reaches_control={bool(top.get('event_path_reaches_control'))};"
            + f"composed={top.get('composed_ownership') or 'unowned'}]"
        )
    return "|".join(entries)[:2_000]


def _candidate_more_options_all_topmost(snapshot: dict[str, Any], tag: str) -> bool:
    probes = [
        probe
        for probe in (snapshot.get("hit_test_probes") or [])[:5]
        if isinstance(probe, dict)
    ]
    return bool(probes) and all(
        str(((probe.get("top") or {}).get("tag") or "")).casefold()
        == tag.casefold()
        for probe in probes
    )


def _candidate_more_options_has_material_snackbar_occlusion(
    snapshot: dict[str, Any],
) -> bool:
    """Recognize only a visible, pointer-active Material snackbar interception."""
    for probe in (snapshot.get("hit_test_probes") or [])[:5]:
        if not isinstance(probe, dict) or probe.get("ownership") is not None:
            continue
        top = probe.get("top") or {}
        if not isinstance(top, dict) or not top.get("material_snackbar"):
            continue
        if not top.get("connected"):
            continue
        if str(top.get("pointer_events") or "").casefold() == "none":
            continue
        if str(top.get("visibility") or "").casefold() == "hidden":
            continue
        try:
            opacity = float(top.get("opacity", 1))
        except (TypeError, ValueError):
            opacity = 1
        if opacity > 0.01:
            return True
    return False


def _candidate_more_options_safe_point(
    snapshot: dict[str, Any],
    box: dict[str, float],
    *,
    preferred_probe: str | None = None,
) -> dict[str, Any]:
    """Choose only a deterministic interior point owned by the exact control."""
    allowed_names = {"center", "left", "right", "up", "down"}
    allowed_ownership = {"self", "descendant", "composed_descendant"}
    raw_probes = (snapshot.get("hit_test_probes") or [])[:5]
    probes = [
        probe
        for probe in raw_probes
        if isinstance(probe, dict)
        and probe.get("name") in allowed_names
        and probe.get("ownership") in allowed_ownership
        and isinstance(probe.get("x"), (int, float))
        and isinstance(probe.get("y"), (int, float))
    ]
    if not raw_probes and snapshot.get("center_top_belongs_to_control"):
        probes = [
            {
                "name": "center",
                "x": float(box["x"] + box["width"] / 2),
                "y": float(box["y"] + box["height"] / 2),
                "ownership": "descendant",
            }
        ]
    if preferred_probe is not None:
        probes = [probe for probe in probes if probe["name"] == preferred_probe]

    margin_x = max(1.0, float(box["width"]) * 0.10)
    margin_y = max(1.0, float(box["height"]) * 0.10)
    interior = [
        probe
        for probe in probes
        if float(box["x"]) + margin_x <= float(probe["x"])
        <= float(box["x"] + box["width"]) - margin_x
        and float(box["y"]) + margin_y <= float(probe["y"])
        <= float(box["y"] + box["height"]) - margin_y
    ]
    if interior:
        return interior[0]

    observed = _candidate_more_options_hit_diagnostic(snapshot)
    detail = f"; hit_diagnostic={observed}" if observed else ""
    raise FlowAutomationError(
        "CANDIDATE_MORE_OPTIONS_OVERLAID: no bounded interior elementFromPoint "
        f"probe is structurally owned by the exact More options control{detail}."
    )


def _download_action_acceptance(report: dict[str, Any]) -> dict[str, Any]:
    """Classify actual acceptance separately from merely dispatching input."""
    events = report.get("events") or []
    ui = report.get("ui") or {}
    signals: list[str] = []
    if ui.get("menu_open_before_activation") is True and ui.get("menu_open_after_activation") is False:
        signals.append("menu_closed_or_removed")
    if ui.get("focus_left_menu") is True:
        signals.append("focus_left_menu")
    cdp = report.get("cdp") or {}
    if cdp.get("download_will_begin") or cdp.get("download_progress"):
        signals.append("native_download_event")
    if report.get("staging_files"):
        signals.append("staging_file")
    kinds = {event.get("kind") for event in events if isinstance(event, dict)}
    if {"popup", "new_page"} & kinds:
        signals.append("popup_or_new_page")
    if "frame_navigation" in kinds or report.get("page_url_changed") is True:
        signals.append("navigation")
    if "blob_created" in kinds:
        signals.append("blob_created")
    if {"anchor_programmatic_click", "anchor_activation", "anchor_added"} & kinds:
        signals.append("anchor_operation")
    if "dom_status" in kinds:
        signals.append("download_status_ui")
    relevant_network = any(
        isinstance(event, dict)
        and event.get("kind") in {"request", "response", "requestfinished", "fetch_call", "fetch_response"}
        and (
            event.get("candidate_bytes") is True
            or (event.get("url") or {}).get("candidate_id_in_path") is True
            or str(event.get("content_type") or "").lower().startswith("image/")
            or "attachment" in str(event.get("content_disposition") or "").lower()
        )
        for event in events
    )
    if relevant_network:
        signals.append("relevant_network_activity")
    accepted = bool(signals)
    return {
        "accepted": accepted,
        "code": "DOWNLOAD_ACTION_ACCEPTED" if accepted else "DOWNLOAD_ACTION_NOT_ACCEPTED",
        "signals": signals,
    }


def _observed_download_mechanisms(report: dict[str, Any]) -> list[str]:
    events = report.get("events") or []
    kinds = {event.get("kind") for event in events if isinstance(event, dict)}
    mechanisms = []
    cdp = report.get("cdp") or {}
    if cdp.get("download_will_begin") or cdp.get("download_progress"):
        mechanisms.append("native_browser_download")
    if report.get("staging_files"):
        mechanisms.append("staging_file")
    if {"popup", "new_page"} & kinds:
        mechanisms.append("popup_or_new_page")
    if "frame_navigation" in kinds or report.get("page_url_changed") is True:
        mechanisms.append("navigation")
    if "blob_created" in kinds:
        mechanisms.append("blob_url")
    if {"anchor_programmatic_click", "anchor_activation", "anchor_added"} & kinds:
        mechanisms.append("anchor")
    if {"fetch_call", "fetch_response", "fetch_failed"} & kinds:
        mechanisms.append("fetch")
    if {"request", "response", "requestfinished", "requestfailed"} & kinds:
        mechanisms.append("filtered_page_network")
    return mechanisms


def _manual_download_comparison(report: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, safe record that can be compared with automated traces."""
    events = report.get("in_page_events") or []
    download_inputs = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("kind") == "input_event"
        and event.get("target_scope") == "download"
    ]
    activations = [
        event
        for event in events
        if isinstance(event, dict) and event.get("kind") == "download_control_activation"
    ]
    targets = [
        {
            "event_type": event.get("event_type"),
            "tag": event.get("tag"),
            "role": event.get("role"),
            "label": event.get("target_label"),
        }
        for event in download_inputs
    ]
    return {
        "download_input_sequence": [event.get("event_type") for event in download_inputs],
        "download_input_targets": targets,
        "download_activation_event_count": len(activations),
        "all_download_inputs_trusted": (
            all(event.get("is_trusted") is True for event in download_inputs)
            if download_inputs
            else None
        ),
        "any_download_input_default_prevented": (
            any(event.get("default_prevented") is True for event in download_inputs)
            if download_inputs
            else None
        ),
        "menu_open_before_activation": (report.get("ui") or {}).get(
            "menu_open_before_activation"
        ),
        "menu_open_after_activation": (report.get("ui") or {}).get(
            "menu_open_after_activation"
        ),
        "focus_before_activation": (report.get("ui") or {}).get(
            "active_element_before"
        ),
        "focus_after_observation": (report.get("ui") or {}).get("active_element_after"),
        "observed_mechanisms": list(report.get("observed_mechanisms") or []),
    }


def _clean_text(value: str | None, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _safe_label(value: str | None) -> str:
    label = _clean_text(value)
    if re.search(r"\bGoogle Account\b", label, re.I):
        return "[redacted account control]"
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", label)


def _safe_current_generation_failure(value: str) -> str:
    """Expose fixed failure semantics, never arbitrary qualified DOM text."""
    # Preserve the existing unusual-activity normalization priority. Every other
    # recognized signature also gets fixed text; incidental UI content is private.
    for pattern, canonical in (
        (_UNUSUAL_ACTIVITY_GENERATION_FAILURE_PATTERN, _UNUSUAL_ACTIVITY_CANONICAL_FAILURE),
        (_FLOW_USAGE_LIMIT_PATTERN, "You've reached your usage limit."),
        (_POLICY_VIOLATION_GENERATION_FAILURE_PATTERN,
         "Failed. This generation might violate our policies. "
         "Please try a different prompt or send feedback. "
         "You have not been charged for this generation."),
        (_GENERIC_GENERATION_FAILURE_PATTERN,
         "Failed. Sorry, this image failed to generate. "
         "You have not been charged for this generation."),
        (_HIGH_DEMAND_GENERATION_FAILURE_PATTERN,
         "Failed. Flow is experiencing high demand. Please try again later. "
         "You have not been charged for this generation."),
    ):
        if pattern.search(value):
            return canonical
    return "Generation failed for the current attempt (unrecognized failure message)."


def _normalize_model_name(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _composer_model_labels(
    values: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[str, ...]:
    """Extract model names only from visible controls in the current composer."""
    matched: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        if value.get("connected") is not True or value.get("visible") is not True:
            continue
        evidence: list[Any] = [
            value.get("innerText"),
            value.get("ariaLabel"),
            value.get("labelledByText"),
            value.get("title"),
        ]
        nested = value.get("nestedVisibleText")
        if isinstance(nested, (list, tuple)):
            evidence.extend(nested)
        elif nested:
            evidence.append(nested)
        for raw_label in evidence:
            if not isinstance(raw_label, str):
                continue
            safe_label = _safe_label(raw_label)
            for match in _VISIBLE_MODEL_NAME_PATTERN.finditer(safe_label):
                model = re.sub(r"[_-]+", " ", _clean_text(match.group(0)))
                key = _normalize_model_name(model)
                if key not in matched or len(safe_label) > len(matched[key]):
                    matched[key] = safe_label
    return tuple(matched.values())


class FlowSession:
    """Conservative Playwright adapter for one Google Flow image prompt.

    Every selector, browser action, and Flow-specific interpretation lives here.
    The adapter deliberately fails instead of guessing when a visible control or
    terminal result cannot be identified unambiguously.
    """

    def __init__(
        self,
        flow_url: str,
        profile_dir: Path,
        *,
        debug_address: str = DEFAULT_DEBUG_ADDRESS,
        debug_port: int = DEFAULT_DEBUG_PORT,
        diagnostics_path: Path = Path("diagnostics/flow_inspect.png"),
        prestart_timing_debug: bool = False,
    ):
        if debug_address != DEFAULT_DEBUG_ADDRESS:
            raise ValueError("Chrome remote debugging must be bound to 127.0.0.1 only.")
        if not 1 <= int(debug_port) <= 65535:
            raise ValueError("Chrome remote debugging port must be between 1 and 65535.")
        if not isinstance(prestart_timing_debug, bool):
            raise ValueError("prestart_timing_debug must be true or false.")
        self.flow_url = flow_url
        self.profile_dir = profile_dir
        self.debug_address = debug_address
        self.debug_port = int(debug_port)
        self.diagnostics_path = diagnostics_path
        self.prestart_timing_debug = prestart_timing_debug
        self._playwright = None
        self._browser = None
        self._page_cdp_session = None
        self._focus_emulation_enabled = False
        self.page = None
        self._required_background_flags_present: bool | None = None
        self._missing_background_flags: tuple[str, ...] = ()
        self._page_lifecycle_keepalive_applied: bool | str = "unsupported"
        self._generation_result_observation: dict[str, Any] | None = None
        self._candidate_correlation_failure_observation: dict[str, Any] | None = None
        self._editor_readiness_observation: dict[str, Any] | None = None
        self._composer_generation_observation: dict[str, Any] | None = None
        self._prestart_timing: _PreStartTimingRecorder | None = None
        self._prestart_timing_observation: dict[str, Any] | None = None
        self._post_start_ack_timing: _PostStartAckTimingRecorder | None = None
        self._post_start_initial_composer_token: int | None = None
        self._last_submit_browser_clock_sample: dict[str, float] | None = None
        self._latency_pre_click_counts: dict[str, int | None] = {}
        self._latency_pre_click_durations: dict[str, float] = {}
        self._current_generation_failure_poll_observation: dict[str, Any] | None = None
        self._event_loop_sentinel: dict[str, Any] | None = None
        self._event_loop_sentinel_read = False
        self._generation_network_timing: _GenerationNetworkTiming | None = None
        self._generation_network_observation: dict[str, Any] | None = None
        self._window_recovery_events: list[dict[str, Any]] = []
        self._candidate_history_recovery: dict[str, Any] | None = None
        self._generation_interrupt_checkpoint: dict[str, Any] = {}
        self._reset_generation_interrupt_checkpoint()

    def _begin_prestart_timing(self) -> None:
        self._stop_generation_network_timing()
        self._prestart_timing = _PreStartTimingRecorder(time.perf_counter)
        self._prestart_timing_observation = self._prestart_timing.snapshot()
        self._post_start_ack_timing = None
        self._post_start_initial_composer_token = None
        self._last_submit_browser_clock_sample = None
        self._latency_pre_click_counts = {}
        self._latency_pre_click_durations = {}
        self._event_loop_sentinel = None
        self._event_loop_sentinel_read = False
        self._generation_network_observation = None

    def _prestart_mark(self, name: str) -> None:
        if self._prestart_timing is None:
            self._begin_prestart_timing()
        assert self._prestart_timing is not None
        self._prestart_timing.mark_once(name)
        self._prestart_timing_observation = self._prestart_timing.snapshot()

    def _prestart_polling(
        self,
        name: str,
        *,
        poll_count: int,
        poll_interval_ms: int,
        timeout_ms: int,
        success_immediate: bool,
        final_reason: str,
        remount_observed: bool | None = None,
    ) -> None:
        if self._prestart_timing is None:
            return
        self._prestart_timing.record_polling(
            name,
            poll_count=poll_count,
            poll_interval_ms=poll_interval_ms,
            timeout_ms=timeout_ms,
            success_immediate=success_immediate,
            final_reason=final_reason,
            remount_observed=remount_observed,
        )
        self._prestart_timing_observation = self._prestart_timing.snapshot()

    def _prestart_timing_snapshot(self) -> dict[str, Any]:
        if self._prestart_timing is None:
            return {
                "schema": "nano-banana-prestart-timing/v1",
                "clock": "monotonic_perf_counter",
                "timing_origin": "prompt_fill_start_ms",
                "origin_established": False,
                "pre_origin_phases": ["candidate_baseline", "progress_baseline"],
                "polling": {},
            }
        self._prestart_timing_observation = self._prestart_timing.snapshot()
        return dict(self._prestart_timing_observation)

    def _begin_post_start_ack_timing(self, *, origin_source: str) -> None:
        self._post_start_ack_timing = _PostStartAckTimingRecorder(
            time.perf_counter,
            initial_composer_token=self._post_start_initial_composer_token,
            origin_source=origin_source,
        )
        if self.prestart_timing_debug:
            self._post_start_ack_timing.set_browser_anchor(self._last_submit_browser_clock_sample)
            self._post_start_ack_timing.set_counts("pre_click", self._latency_pre_click_counts)
            for stage, duration in self._latency_pre_click_durations.items():
                self._post_start_ack_timing.record_latency(stage, duration)

    def _capture_latency_counts(self, phase: str) -> None:
        """Two opt-in snapshots per attempt; no text, identity, or DOM handles returned."""
        if not self.prestart_timing_debug:
            return
        cancellation_checkpoint("generation_latency_diagnostics")
        page = self._require_page()
        durations = {}
        raw = None
        for stage in ("cheap_round_trip", "dom_count_snapshot"):
            started = time.perf_counter()
            try:
                if stage == "cheap_round_trip":
                    page.evaluate("() => 1")
                else:
                    raw = page.evaluate(
                        r"""
                        () => {
                          const visible = (e) => {
                            const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                            return e.isConnected && r.width > 0 && r.height > 0 &&
                              s.visibility !== 'hidden' && s.display !== 'none';
                          };
                          const tiles = [...document.querySelectorAll('flow-image-tile')];
                          const visibleTiles = tiles.filter(visible).length;
                          // Evidence only: never used as a production selector/scroll action.
                          let feed = tiles[0]?.parentElement || null;
                          while (feed && !tiles.every((tile) => feed.contains(tile))) {
                            feed = feed.parentElement;
                          }
                          let scroll = feed;
                          while (scroll && !(scroll.clientHeight > 0 &&
                                 scroll.scrollHeight > scroll.clientHeight + 1 &&
                                 /auto|scroll|overlay/.test(getComputedStyle(scroll).overflowY))) {
                            scroll = scroll.parentElement;
                          }
                          const setSize = feed?.getAttribute('aria-setsize');
                          const registry = window.__nanoBatchGenerationObserverMetricsV1;
                          const state = window.__nanoBatchCurrentGenerationAttempt;
                          const walker = document.createTreeWalker(document, NodeFilter.SHOW_ALL);
                          let nodeCount = 0;
                          while (walker.nextNode()) nodeCount++;
                          return {
                            dom_node_count: nodeCount,
                            element_count: document.querySelectorAll('*').length,
                            candidate_tile_count: tiles.length,
                            visible_candidate_tile_count: visibleTiles,
                            hidden_candidate_tile_count: tiles.length - visibleTiles,
                            history_item_count: feed?.children.length ?? null,
                            result_card_count: feed
                              ? new Set(tiles.map((tile) => {
                                  let card = tile;
                                  while (card.parentElement && card.parentElement !== feed) card = card.parentElement;
                                  return card;
                                })).size : null,
                            data_media_id_count: document.querySelectorAll('[data-media-id]').length,
                            button_count: document.querySelectorAll('button,[role="button"]').length,
                            body_text_length: document.body?.innerText.length ?? null,
                            history_descendant_count: feed?.querySelectorAll('*').length ?? null,
                            history_scroll_height: (scroll || document.scrollingElement)?.scrollHeight ?? null,
                            virtualized_item_count: setSize && /^\d+$/.test(setSize) ? Number(setSize) : null,
                            active_observer_count: registry?.active ?? null,
                            observer_install_count: registry?.installed ?? null,
                            observer_disconnect_count: registry?.disconnected ?? null,
                            observer_peak_count: registry?.peak ?? null,
                            retained_failure_container_count: state?.failureContainers.length ?? null,
                            detached_failure_container_count: state
                              ? state.failureContainers.filter((e) => !e.isConnected).length : null,
                            observer_callback_count: state?.callbackCount ?? null,
                            observer_callback_total_ms: state?.callbackTotalMs ?? null,
                            observer_callback_max_ms: state?.callbackMaxMs ?? null,
                            first_mutation_at_ms: state?.firstMutationAtMs ?? null,
                            page_lifetime_ms: performance.now(),
                            composer_identity_counter: window.__nanoBatchComposerIdentityV1?.next ?? null,
                            history_surface_identity_counter: window.__nanoBatchCandidateHistorySurfaceRegistryV1?.next ?? null,
                          };
                        }
                        """
                    )
            except Exception:
                cancellation_checkpoint("generation_latency_diagnostics")
                # Missing telemetry cannot change acknowledgement or fail production.
                continue
            durations[stage] = max(0, (time.perf_counter() - started) * 1_000)
            cancellation_checkpoint("generation_latency_diagnostics")
        counts = _latency_counts(raw)
        if phase == "pre_click":
            self._latency_pre_click_counts = counts
            self._latency_pre_click_durations = durations
        elif self._post_start_ack_timing is not None:
            self._post_start_ack_timing.set_counts("post_ack", counts)
            self._post_start_ack_timing.observe_browser_metrics(raw)
            for stage, duration in durations.items():
                self._post_start_ack_timing.record_latency(stage, duration)

    @staticmethod
    def _format_order_latency_summary(order: int, timing: dict[str, Any]) -> str:
        def number(value: Any) -> str:
            safe = _latency_number(value)
            if safe is None:
                return "-"
            return str(int(safe)) if safe.is_integer() else str(round(safe, 3))
        checks = timing.get("check_latency_ms")
        checks = checks if isinstance(checks, dict) else {}
        cheap = checks.get("cheap_round_trip")
        cheap = cheap if isinstance(cheap, dict) else {}
        return (
            f"ORDER LATENCY SUMMARY order={number(order)}; "
            f"click->mutation={number(timing.get('first_mutation_callback_ms'))} ms; "
            f"click->ack={number(timing.get('ack_ms'))} ms; "
            f"DOM nodes={number(timing.get('post_ack_dom_node_count'))}; "
            f"candidate tiles={number(timing.get('candidate_tile_count'))}; "
            f"history items={number(timing.get('history_item_count'))}; "
            f"active observers={number(timing.get('active_observer_count'))}; "
            f"cheap eval p50/max={number(cheap.get('p50'))}/{number(cheap.get('max'))} ms"
        )

    @staticmethod
    def _format_ack_poll_breakdown(timing: dict[str, Any]) -> str:
        breakdown = timing.get("ack_poll_breakdown")
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        slowest = breakdown.get("slowest_polls")
        poll = slowest[0] if isinstance(slowest, list) and slowest and isinstance(slowest[0], dict) else {}
        calls = breakdown.get("calls")
        calls = calls if isinstance(calls, dict) else {}
        def number(value):
            safe = _latency_number(value)
            return str(round(safe, 3)) if safe is not None else "-"
        def name(value):
            return value if isinstance(value, str) and value in _ACK_CALL_BUDGETS else "-"
        lines = [
            "ACK POLL BREAKDOWN",
            f"  total polls: {number(breakdown.get('total_polls'))}",
            f"  slowest poll: {number(poll.get('poll_duration_ms'))} ms",
            f"  dominant stage: {name(poll.get('dominant_stage'))}",
            f"  dominant nested helper: {name(poll.get('dominant_nested_helper'))}",
            "  stage max:",
        ]
        for label, key in (
            ("failure", "failure_observation"), ("progress", "progress_observation"),
            ("candidate", "candidate_observation"), ("composer", "composer_resolution"),
            ("start state", "start_state_read"), ("prompt state", "prompt_state_read"),
            ("observer read", "observer_read"), ("poll wait", "poll_wait"),
        ):
            stats = calls.get(key)
            stats = stats if isinstance(stats, dict) else {}
            lines.append(f"    {label}: {number(stats.get('max_ms'))} ms")
        lines.append("  browser callback -> Python observation: " +
                     number(timing.get("callback_to_python_observation_gap_ms")) + " ms")
        return "\n".join(lines)

    @staticmethod
    def _format_observer_read_breakdown(timing: dict[str, Any]) -> str:
        breakdown = timing.get("observer_read_breakdown")
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        reads = breakdown.get("slowest_reads")
        read = reads[0] if isinstance(reads, list) and reads and isinstance(reads[0], dict) else {}
        counts = read.get("dom_counts")
        counts = counts if isinstance(counts, dict) else {}
        maxima = breakdown.get("internal_stage_max_ms")
        maxima = maxima if isinstance(maxima, dict) else {}
        def number(value):
            safe = _latency_number(value)
            return str(round(safe, 3)) if safe is not None else "-"
        def stage(value):
            return value if value in _OBSERVER_READ_STAGES else "-"
        lines = [
            "OBSERVER READ BREAKDOWN (slowest wall read)",
            f"  wall: {number(read.get('wall_ms'))} ms",
            f"  browser JS: {number(read.get('total_browser_js_ms'))} ms",
            f"  transport/queue/serialization gap: {number(read.get('transport_queue_serialization_gap_ms'))} ms (not separable)",
            f"  containers scanned/retained: {number(read.get('containers_scanned'))}/{number(read.get('retained_strong_container_count'))}",
            f"  container max/p50/p95: {number(read.get('max_container_ms'))}/{number(read.get('p50_container_ms'))}/{number(read.get('p95_container_ms'))} ms",
            f"  slowest container index/stage: {number(read.get('slowest_container_index'))}/{stage(read.get('slowest_container_stage'))}",
            f"  mutation records: {number(read.get('mutation_record_count'))}",
            f"  reused DOM nodes/tiles/history: {number(counts.get('dom_node_count'))}/{number(counts.get('candidate_tile_count'))}/{number(counts.get('history_item_count'))}",
            f"  dominant internal stage (self): {stage(read.get('dominant_internal_stage'))}",
            f"  estimated request->JS/JS->Python: {number(read.get('estimated_request_to_browser_enter_ms'))}/{number(read.get('estimated_browser_exit_to_python_ms'))} ms; anchor uncertainty: {number(read.get('clock_alignment_uncertainty_ms'))} ms",
            "  direct queue/serialization timing: unavailable",
            "  internal stage max (inclusive; do not sum):",
        ]
        for name in _OBSERVER_READ_STAGES:
            lines.append(f"    {name}: {number(maxima.get(name))} ms")
        return "\n".join(lines)

    def _post_start_ack_timing_snapshot(self) -> dict[str, Any]:
        if self._post_start_ack_timing is None:
            return {
                "schema": "nano-banana-post-start-ack-timing/v1",
                "clock": "monotonic_perf_counter",
                "timing_origin": "immediately_before_start_mouse_down",
                "origin_established": False,
            }
        result = self._post_start_ack_timing.snapshot()
        if self.prestart_timing_debug and self._event_loop_sentinel is not None:
            result["browser_event_loop_sentinel"] = dict(self._event_loop_sentinel)
        if self.prestart_timing_debug and self._generation_network_observation is not None:
            result["generation_network_timing"] = dict(self._generation_network_observation)
        return result

    def _stop_generation_network_timing(self, *, skip_rpc: bool = False) -> None:
        diagnostic = self._generation_network_timing
        if diagnostic is not None:
            diagnostic.stop(skip_rpc=skip_rpc)

    def _arm_generation_network_timing(self) -> None:
        if not self.prestart_timing_debug:
            return
        self._stop_generation_network_timing()
        previous = self._generation_network_timing
        # If local listener removal fails, never stack another attempt listener.
        if previous is not None and previous.listening:
            self._generation_network_observation = {"status": "previous_listener_removal_failed"}
            return
        self._generation_network_timing = None
        if self._page_cdp_session is None:
            self._generation_network_observation = {"status": "existing_page_cdp_unavailable"}
            return
        diagnostic = _GenerationNetworkTiming(self._page_cdp_session)
        self._generation_network_timing = diagnostic
        try:
            diagnostic.arm()
        except Exception:
            cancellation_checkpoint("pre_start_network_diagnostic")
            diagnostic.status = "network_setup_unavailable"
            diagnostic.stop()
        except KeyboardInterrupt:
            diagnostic.stop(skip_rpc=True)
            raise

    @staticmethod
    def _event_loop_sentinel_summary(raw: Any, reads: list[dict[str, Any]], wall_ms: float) -> dict[str, Any]:
        """Fixed numeric allowlist; browser-relative comparisons need no clock alignment."""
        raw = raw if isinstance(raw, dict) else {}
        anchor = _latency_number(raw.get("pointerdown_at_ms"))
        source = "pointerdown"
        if anchor is None:
            anchor = _latency_number(raw.get("mousedown_at_ms"))
            source = "mousedown" if anchor is not None else "unavailable"
        result = {"schema": "nano-banana-event-loop-sentinel/v1", "anchor": source,
                  "capture_scope": "first_trusted_primary_document_gesture_after_arm",
                  "sentinel_read_wall_ms": _latency_number(wall_ms)}
        for name in ("installation", "pointerdown", "mousedown", "click", "microtask",
                     "promise_microtask", "timeout_0", "raf_1", "raf_2", "mutation_callback", "read"):
            stamp = _latency_number(raw.get(name + "_at_ms"))
            delta = stamp - anchor if stamp is not None and anchor is not None else None
            result[name + "_ms"] = round(delta, 3) if delta is not None and (
                delta >= 0 or name == "installation") else None
        supported = raw.get("longtask_supported")
        supported = supported if isinstance(supported, bool) else None
        result["longtask_supported"] = supported
        for name in ("longtask_count", "longest_longtask_ms", "total_longtask_ms"):
            value = _latency_number(raw.get(name)) if supported else None
            result[name] = min(128 if name == "longtask_count" else 1_000_000_000, value) if value is not None else None
        first = _latency_number(raw.get("first_longtask_at_ms")) if supported else None
        result["first_longtask_start_ms"] = round(first - anchor, 3) if first is not None and anchor is not None else None
        longest = _latency_number(raw.get("longest_longtask_at_ms")) if supported else None
        result["longest_longtask_start_ms"] = round(longest - anchor, 3) if longest is not None and anchor is not None else None
        overlaps = raw.get("longtask_overlaps_ack_window")
        result["longtask_overlaps_ack_window"] = overlaps if supported and isinstance(overlaps, bool) else None
        result["longtask_window_scope"] = "pointerdown_or_mousedown_to_post_ack_read"
        result["longtask_entry_limit"] = 128
        result["longtask_truncated"] = raw.get("longtask_truncated") is True
        result["capture_expired"] = raw.get("capture_expired") is True
        read = max(reads, key=lambda item: item.get("wall_ms") or 0, default={})
        entered = _latency_number(read.get("evaluate_enter_browser_ms"))
        result["observer_evaluate_start_ms"] = round(entered - anchor, 3) if entered is not None and anchor is not None and entered >= anchor else None
        for target, key in (("observer_evaluate_wall_ms", "wall_ms"),
                            ("observer_browser_js_ms", "total_browser_js_ms")):
            result[target] = _latency_number(read.get(key))
        uncertainty = _latency_number(read.get("clock_alignment_uncertainty_ms"))
        result["clock_alignment_uncertainty_ms"] = uncertainty
        for target, key in (("estimated_request_to_js_ms", "estimated_request_to_browser_enter_ms"),
                            ("estimated_js_to_python_ms", "estimated_browser_exit_to_python_ms")):
            result[target] = _latency_number(read.get(key)) if uncertainty is not None and uncertainty <= 50 else None
        delay = result["observer_evaluate_start_ms"]
        callbacks = [result["timeout_0_ms"], result["raf_1_ms"]]
        body = result["observer_browser_js_ms"]
        hint = "insufficient_evidence"
        if delay is not None and delay >= 1000 and body is not None and body <= 250 and all(value is not None for value in callbacks):
            if max(callbacks) <= min(250, delay * .1):
                if (result["longest_longtask_ms"] or 0) >= max(1000, delay * .5):
                    hint = "mixed_event_loop_and_evaluate_delay_evidence"
                else:
                    hint = "renderer_responsive_external_evaluate_delay_evidence"
            elif min(callbacks) >= max(1000, delay * .5):
                hint = "delayed_event_loop_callbacks_blockage_or_throttling_evidence"
            else:
                hint = "mixed_event_loop_and_evaluate_delay_evidence"
        result["conclusion_hint"] = "insufficient_evidence" if result["capture_expired"] else hint
        return result

    def _capture_event_loop_sentinel(self) -> None:
        """One best-effort read after ACK exits, never from an ACK/result poll."""
        if not self.prestart_timing_debug or self._event_loop_sentinel_read or cancellation_requested():
            if cancellation_requested():
                self._stop_generation_network_timing(skip_rpc=True)
            return
        self._event_loop_sentinel_read = True
        started = time.perf_counter()
        interrupted = False
        try:
            raw = interruptible_call("post_ack_diagnostic", "event_loop_sentinel_read", self._require_page().evaluate,
                r"""
                () => {
                  const state = window.__nanoBatchCurrentGenerationAttempt;
                  const sentinel = state?.eventLoopSentinel;
                  return sentinel?.read(state.firstMutationAtMs) ?? null;
                }
                """
            )
        except KeyboardInterrupt:
            interrupted = True
            raise
        except Exception:
            cancellation_checkpoint("post_ack_diagnostic")
            raw = None
        finally:
            self._stop_generation_network_timing(skip_rpc=interrupted)
        reads = self._post_start_ack_timing._observer_reads if self._post_start_ack_timing else []
        self._event_loop_sentinel = self._event_loop_sentinel_summary(raw, reads, (time.perf_counter() - started) * 1000)
        if self._generation_network_timing is not None and self._generation_network_observation is None:
            try:
                self._generation_network_observation = self._generation_network_timing.snapshot(
                    raw, self._post_start_ack_timing, self._event_loop_sentinel)
            except Exception:
                cancellation_checkpoint("post_ack_diagnostic")
                self._generation_network_observation = {"status": "network_summary_unavailable"}

    @staticmethod
    def _format_event_loop_vs_cdp(timing: dict[str, Any]) -> str:
        raw = timing.get("browser_event_loop_sentinel")
        raw = raw if isinstance(raw, dict) else {}
        def number(key):
            value = raw.get(key)
            return str(round(value, 3)) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else "-"
        hints = {"renderer_responsive_external_evaluate_delay_evidence": "early renderer callbacks responsive; external evaluate delayed (later blockage not excluded)",
                 "delayed_event_loop_callbacks_blockage_or_throttling_evidence": "browser callbacks delayed; consistent with main-thread blockage or throttling",
                 "mixed_event_loop_and_evaluate_delay_evidence": "mixed callback/evaluate delays; cause unresolved"}
        lines = ["EVENT LOOP VS CDP (browser-relative; first trusted primary gesture)"]
        for label, key in (("installation", "installation_ms"), ("pointerdown", "pointerdown_ms"),
                           ("mousedown", "mousedown_ms"), ("click", "click_ms"), ("microtask", "microtask_ms"),
                           ("promise microtask", "promise_microtask_ms"), ("timeout(0)", "timeout_0_ms"),
                           ("raf1", "raf_1_ms"), ("raf2", "raf_2_ms"), ("mutation callback", "mutation_callback_ms"),
                           ("observer evaluate JS start", "observer_evaluate_start_ms"),
                           ("observer evaluate wall", "observer_evaluate_wall_ms"), ("browser JS duration", "observer_browser_js_ms"),
                           ("estimated request -> JS", "estimated_request_to_js_ms"),
                           ("estimated JS -> Python", "estimated_js_to_python_ms"),
                           ("alignment uncertainty", "clock_alignment_uncertainty_ms"),
                           ("first longtask start", "first_longtask_start_ms"),
                           ("sentinel read wall", "sentinel_read_wall_ms")):
            lines.append(f"  {label}: {number(key)} ms")
        lines.append(f"  longtask count/max/total: {number('longtask_count')}/{number('longest_longtask_ms')}/{number('total_longtask_ms')} (post-click to read)")
        def flag(key):
            value = raw.get(key)
            return "yes" if value is True else "no" if value is False else "unknown"
        lines.append(f"  longtask supported/truncated/overlap: {flag('longtask_supported')}/{flag('longtask_truncated')}/{flag('longtask_overlaps_ack_window')}")
        lines.append(f"  conclusion hint: {hints.get(raw.get('conclusion_hint'), 'insufficient evidence')}")
        return "\n".join(lines)

    @staticmethod
    def _format_generation_network_timing(timing: dict[str, Any]) -> str:
        raw = timing.get("generation_network_timing")
        raw = raw if isinstance(raw, dict) else {}

        def number(value):
            return str(round(value, 3)) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else "-"

        def host_class(event):
            host = event.get("host")
            return host if host in ("flow.google.com", "labs.google", "*.googleapis.com", "[other-host]") else "[other-host]"

        def fingerprint(event):
            value = event.get("endpoint_fp")
            return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{12}", value) else "-"

        def mime_type(event):
            value = event.get("mime_type")
            return value if isinstance(value, str) and value in _GENERATION_NETWORK_SAFE_MIME_TYPES else "-"

        def method_type(event):
            return "POST/" + (event.get("resource_type") if event.get("resource_type") in ("Fetch", "XHR") else "unknown")

        def body(event):
            value = event.get("body_present")
            return "yes" if value is True else "no" if value is False else "unknown"

        def relation(event):
            value = event.get("timing_relation")
            return value if value in ("before_longtask", "during_longtask", "after_longtask", "boundary_uncertain") else "unavailable"

        lines = ["GENERATION NETWORK TIMING (browser request start; not server receipt/work start)"]
        exact = raw.get("exact_generation_request")
        exact = exact if isinstance(exact, dict) and exact.get("identification_evidence") == "verified_endpoint" else None
        lines.append("  exact generation request: " + ("verified endpoint" if exact else "unresolved"))
        for label, key in (("pointerdown", "pointerdown_ms"), ("click", "click_ms"),
                           ("first longtask start", "first_longtask_start_ms"), ("longest longtask start", "longest_longtask_start_ms"),
                           ("longest longtask duration", "longest_longtask_ms"),
                           ("generation request start", "generation_request_start_relative_to_pointerdown_ms"),
                           ("observer evaluate JS start", "observer_evaluate_start_ms"), ("ACK (Python aligned estimate)", "ack_ms"),
                           ("timing alignment uncertainty (empirical)", "alignment_uncertainty_ms"),
                           ("Python alignment uncertainty", "python_clock_alignment_uncertainty_ms")):
            lines.append(f"  {label}: {number(raw.get(key))} ms")
        reasons = ("aligned", "clock_sample_or_pointerdown_unavailable", "clock_alignment_inconsistent_or_too_uncertain", "sentinel_capture_expired")
        reason = raw.get("alignment_reason")
        lines.append("  alignment: " + (reason if reason in reasons else "unavailable"))
        if exact:
            lines.extend((f"  request method/type: {method_type(exact)}", f"  endpoint fp/host: {fingerprint(exact)}/{host_class(exact)}",
                          f"  request body present: {body(exact)}", "  request payload length: -",
                          f"  Python event receive: {number(exact.get('python_event_receive_ms'))} ms",
                          f"  request timing relation (longest recorded task): {relation(exact)}"))
        candidates = raw.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        lines.append(f"  candidate count: {number(raw.get('candidate_count'))}; retained/printed limits: 32/10; truncated: {'yes' if raw.get('truncated') is True else 'no'}")
        evidence_names = ("verified_endpoint", "image_generation_rpc_semantics_unverified", "post_fetch_xhr_only")
        for event in candidates[:10]:
            if not isinstance(event, dict):
                continue
            evidence = event.get("identification_evidence")
            evidence = evidence if evidence in evidence_names else "unverified"
            initiator = event.get("initiator_type")
            initiator = initiator if initiator in ("parser", "script", "preload", "preflight", "other") else "unknown"
            lines.append(f"  candidate #{number(event.get('ordinal'))}: fp={fingerprint(event)}; start={number(event.get('request_start_ms'))} ms; response={number(event.get('response_received_ms'))} ms; finish={number(event.get('loading_finished_ms'))} ms; status={number(event.get('http_status'))}; mime={mime_type(event)}; encoded_bytes={number(event.get('encoded_response_size'))}; Python request/response/finish receive={number(event.get('python_event_receive_ms'))}/{number(event.get('python_response_event_receive_ms'))}/{number(event.get('python_finish_event_receive_ms'))} ms; {method_type(event)}; host={host_class(event)}; initiator={initiator}; body={body(event)}; {evidence}; {relation(event)}")
        hints = {"request_observed_before_recorded_longtask": "request began before the recorded longest task; server work not measured",
                 "request_observed_during_recorded_longtask": "request began during the recorded longest task; server work not measured",
                 "request_observed_after_recorded_longtask": "request began after the recorded longest task; cause unresolved"}
        lines.append("  conclusion hint: " + (hints.get(raw.get("conclusion_hint"), "exact generation request unresolved; inspect sanitized candidates") if exact else "exact generation request unresolved; inspect sanitized candidates"))
        return "\n".join(lines)

    @staticmethod
    def _format_prestart_timing(timing: dict[str, Any]) -> str:
        durations = (
            ("fill", "fill_duration_ms"),
            ("prompt verify", "prompt_verify_duration_ms"),
            ("composer reacquire", "composer_reacquire_duration_ms"),
            ("Start reacquire", "start_control_reacquire_duration_ms"),
            ("Start actionability", "start_actionability_duration_ms"),
            ("candidate baseline", "candidate_baseline_duration_ms"),
            ("progress baseline", "progress_baseline_duration_ms"),
            ("observer arm", "observer_arm_duration_ms"),
            ("final validation", "final_prestart_validation_duration_ms"),
            ("mouse positioning", "mouse_position_duration_ms"),
        )
        lines = ["PRE-START TIMING"]
        for label, key in durations:
            value = timing.get(key)
            lines.append(f"  {label + ':':24}{value if value is not None else '-':>7} ms")
        lines.extend(
            (
                "  " + "-" * 34,
                f"  {'fill end -> mouse down:':24}"
                f"{timing.get('fill_end_to_mouse_down_ms', '-'):>7} ms",
                f"  {'mouse down -> ack:':24}"
                f"{timing.get('mouse_down_to_ack_ms', '-'):>7} ms",
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _format_post_start_ack_timing(timing: dict[str, Any]) -> str:
        events: list[tuple[int, str]] = [(0, "mouse down")]
        labels = (
            ("start_mouse_up_ms", "mouse up"),
            ("first_post_start_mutation_ms", "first post-Start DOM mutation"),
            ("start_button_hidden_ms", "Start hidden"),
            ("start_button_disabled_ms", "Start disabled"),
            ("start_button_disappeared_ms", "Start disappeared"),
            ("prompt_editor_transition_ms", "prompt editor transitioned"),
            ("first_progress_signal_ms", "first progress signal"),
            ("first_candidate_signal_ms", "first candidate signal"),
            ("candidate_uuid_appeared_ms", "candidate UUID appeared"),
            ("composer_remounted_ms", "composer remounted"),
            ("explicit_failure_ms", "exact explicit failure"),
        )
        for key, label in labels:
            value = timing.get(key)
            if isinstance(value, int):
                events.append((value, label))
        ack_ms = timing.get("ack_ms")
        if isinstance(ack_ms, int):
            events.append((ack_ms, f"ACK: {timing.get('ack_signal') or 'unknown'}"))
        lines = ["POST-START ACK TIMING"]
        for elapsed, label in sorted(events, key=lambda event: event[0]):
            lines.append(f"  +{elapsed:>5} ms  {label}")
        trace = timing.get("state_change_trace")
        if isinstance(trace, list) and trace:
            lines.append("  state-change polls:")
            for entry in trace[:6]:
                state = entry.get("state") if isinstance(entry, dict) else None
                if not isinstance(state, dict):
                    continue
                summary = (
                    "Start="
                    f"{state.get('start_button_visible')}/"
                    f"{state.get('start_button_enabled')}/"
                    f"{state.get('start_button_actionable')}; "
                    f"progress={state.get('progress_signal_count')}; "
                    f"candidates={state.get('candidate_count')}; "
                    f"mutations={state.get('generation_dom_mutation_count')}"
                )
                lines.append(
                    f"    +{int(entry.get('first_elapsed_ms') or 0):>5} ms  "
                    f"poll {entry.get('from_poll')}..{entry.get('through_poll')}: "
                    f"{summary}"
                )
            if timing.get("trace_truncated"):
                lines.append("    (bounded trace truncated)")
        return "\n".join(lines)

    def _reset_generation_interrupt_checkpoint(self) -> None:
        self._generation_interrupt_checkpoint = {
            "interrupt_phase": "session_setup",
            "prompt_written": False,
            "start_mouse_down_sent": False,
            "start_acknowledged": False,
            "submission_state": GENERATION_SUBMISSION_NOT_SUBMITTED,
        }

    def _set_generation_interrupt_checkpoint(
        self,
        phase: str,
        *,
        prompt_written: bool | None = None,
        start_mouse_down_sent: bool | None = None,
        start_acknowledged: bool | None = None,
    ) -> None:
        safe_phase = (
            phase
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", phase)
            else "unknown"
        )
        checkpoint = self._generation_interrupt_checkpoint
        checkpoint["interrupt_phase"] = safe_phase
        if prompt_written is not None:
            checkpoint["prompt_written"] = bool(prompt_written)
        if start_mouse_down_sent is not None:
            checkpoint["start_mouse_down_sent"] = bool(start_mouse_down_sent)
        if start_acknowledged is not None:
            checkpoint["start_acknowledged"] = bool(start_acknowledged)
        checkpoint["submission_state"] = (
            GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT
            if checkpoint["start_mouse_down_sent"]
            else GENERATION_SUBMISSION_NOT_SUBMITTED
        )
        cancellation_checkpoint(safe_phase)

    def _attach_generation_interrupt_evidence(
        self,
        exc: KeyboardInterrupt,
    ) -> None:
        checkpoint = self._generation_interrupt_checkpoint
        exc.generation_interrupt_schema = GENERATION_INTERRUPT_EVIDENCE_SCHEMA
        exc.interrupt_phase = checkpoint["interrupt_phase"]
        exc.prompt_written = checkpoint["prompt_written"]
        exc.start_mouse_down_sent = checkpoint["start_mouse_down_sent"]
        exc.start_acknowledged = checkpoint["start_acknowledged"]
        exc.submission_state = checkpoint["submission_state"]

    @contextmanager
    def _generation_interrupt_scope(self):
        """Annotate Ctrl+C without converting it into an ordinary Flow error."""
        self._reset_generation_interrupt_checkpoint()
        try:
            yield
        except KeyboardInterrupt as exc:
            annotate_keyboard_interrupt(exc)
            self._attach_generation_interrupt_evidence(exc)
            raise

    @property
    def cdp_endpoint(self) -> str:
        return f"http://{self.debug_address}:{self.debug_port}"

    @staticmethod
    def _playwright_api():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `python -m pip install -r requirements.txt`. "
                "Bundled Chromium is not used; automation requires installed Google Chrome."
            ) from exc
        return sync_playwright

    def _profile_lock_markers(self) -> tuple[Path, ...]:
        return tuple(
            self.profile_dir / name
            for name in _PROFILE_LOCK_MARKERS
            if os.path.lexists(self.profile_dir / name)
        )

    def _profile_locked_error(self) -> FlowProfileLockedError:
        return FlowProfileLockedError(
            f"The dedicated Chrome profile is locked: {self.profile_dir.resolve()}. "
            "A Chrome process is using it without the required local debugging endpoint. "
            "Close that Chrome instance, then relaunch it with `python app.py open-flow-debug`."
        )

    @staticmethod
    def _is_flow_project_url(value: str | None) -> bool:
        if not value:
            return False
        parts = urlsplit(value)
        project_id = parts.path.removeprefix("/project/").strip("/")
        return (
            parts.scheme == "https"
            and parts.netloc.lower() == "flow.google.com"
            and parts.path.startswith("/project/")
            and bool(project_id)
            and "/" not in project_id
        )

    def _debug_endpoint_available(self) -> bool:
        try:
            with socket.create_connection((self.debug_address, self.debug_port), timeout=0.25):
                return True
        except OSError:
            return False

    def _debug_launch_command(self, chrome: Path) -> list[str]:
        safe_destination = _safe_visible_url(self.flow_url)
        if safe_destination != FLOW_HOME_URL and not self._is_flow_project_url(
            safe_destination
        ):
            raise FlowAutomationError(
                "Chrome startup destination must be canonical https://flow.google.com/ "
                "or a specific https://flow.google.com/project/<id> URL."
            )
        return [
            str(chrome),
            f"--user-data-dir={self.profile_dir.resolve()}",
            f"--remote-debugging-port={self.debug_port}",
            f"--remote-debugging-address={self.debug_address}",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=CalculateNativeWinOcclusion",
            safe_destination,
        ]

    @staticmethod
    def _background_flag_presence(arguments: Any) -> dict[str, bool] | None:
        """Inspect only required stability switches, never retain raw arguments."""
        if not isinstance(arguments, list):
            return None
        flags = {str(argument).casefold() for argument in arguments}
        disabled_features: set[str] = set()
        for argument in arguments:
            value = str(argument)
            if value.casefold().startswith("--disable-features="):
                disabled_features.update(
                    feature.strip().casefold()
                    for feature in value.split("=", 1)[1].split(",")
                    if feature.strip()
                )
        return {
            "disable_background_timer_throttling": (
                "--disable-background-timer-throttling" in flags
            ),
            "disable_backgrounding_occluded_windows": (
                "--disable-backgrounding-occluded-windows" in flags
            ),
            "disable_renderer_backgrounding": (
                "--disable-renderer-backgrounding" in flags
            ),
            "disable_calculate_native_win_occlusion": (
                _CALCULATE_NATIVE_WIN_OCCLUSION in disabled_features
            ),
        }

    def _verify_live_background_flags(self) -> None:
        """Reject only a live Chrome whose required switches are provably absent."""
        cdp = self._page_cdp_session
        self._required_background_flags_present = None
        self._missing_background_flags = ()
        if cdp is None:
            return
        try:
            command_line = cdp.send("Browser.getBrowserCommandLine")
            arguments = (
                command_line.get("arguments")
                if isinstance(command_line, dict)
                else None
            )
            presence = self._background_flag_presence(arguments)
        except Exception:
            presence = None
        if presence is None:
            warnings.warn(
                "Chrome did not expose its command line; required minimized/background "
                "stability flags are unknown. Close and relaunch the dedicated Chrome "
                "through the launcher for a verifiable session.",
                RuntimeWarning,
                stacklevel=2,
            )
            return
        missing_keys = tuple(key for key, present in presence.items() if not present)
        self._required_background_flags_present = not missing_keys
        if not missing_keys:
            return
        key_to_flag = {
            "disable_background_timer_throttling": _REQUIRED_BACKGROUND_FLAGS[0],
            "disable_backgrounding_occluded_windows": _REQUIRED_BACKGROUND_FLAGS[1],
            "disable_renderer_backgrounding": _REQUIRED_BACKGROUND_FLAGS[2],
            "disable_calculate_native_win_occlusion": _REQUIRED_BACKGROUND_FLAGS[3],
        }
        self._missing_background_flags = tuple(key_to_flag[key] for key in missing_keys)
        raise FlowAutomationError(
            "The attached dedicated Chrome is missing required minimized/background "
            "stability flags: "
            + ", ".join(self._missing_background_flags)
            + ". Close that dedicated Chrome, then relaunch it with `python app.py "
            "open-flow-debug`. No browser was terminated automatically."
        )

    def _enable_page_focus_emulation(self, page) -> None:
        """Keep the selected renderer internally active without foregrounding Chrome."""
        cdp = self._page_cdp_session
        if cdp is None:
            cdp = page.context.new_cdp_session(page)
            self._page_cdp_session = cdp
        try:
            cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception as exc:
            raise FlowAutomationError(
                "Flow page focus emulation could not be enabled through the local "
                "Chrome debugging session. Restart Chrome with `python app.py "
                "open-flow-debug` and retry."
            ) from exc
        self._focus_emulation_enabled = True

    def _enable_page_lifecycle_keepalive(self) -> None:
        """Ask CDP to keep only the selected Flow target in the active lifecycle."""
        cdp = self._page_cdp_session
        self._page_lifecycle_keepalive_applied = "unsupported"
        if cdp is None:
            return
        try:
            cdp.send("Page.setWebLifecycleState", {"state": "active"})
        except Exception:
            # This experimental CDP method is not present in every Chrome family.
            # Focus, window state, visibility, and input are deliberately untouched.
            return
        self._page_lifecycle_keepalive_applied = True

    def _exact_window_state(self) -> str:
        """Return only Browser-domain windowState; never infer from DOM focus."""
        cdp = self._page_cdp_session
        if cdp is None:
            return "unknown"
        try:
            result = cdp.send("Browser.getWindowForTarget")
            bounds = result.get("bounds") if isinstance(result, dict) else None
            state = bounds.get("windowState") if isinstance(bounds, dict) else None
        except Exception:
            return "unknown"
        return (
            str(state)
            if state in {"normal", "minimized", "maximized", "fullscreen"}
            else "unknown"
        )

    @staticmethod
    def _production_viewport_usable(viewport: dict[str, Any]) -> bool:
        return all(
            isinstance(viewport.get(key), (int, float))
            and not isinstance(viewport.get(key), bool)
            and float(viewport[key]) > 0
            for key in ("inner_width", "inner_height", "viewport_width", "viewport_height")
        )

    def _read_production_viewport_geometry(self) -> dict[str, int | float | None]:
        page = self._require_page()
        raw = page.evaluate(
            r"""
            () => {
              const visualViewport = window.visualViewport;
              return {
                inner_width: window.innerWidth,
                inner_height: window.innerHeight,
                viewport_width: visualViewport
                  ? visualViewport.width : document.documentElement.clientWidth,
                viewport_height: visualViewport
                  ? visualViewport.height : document.documentElement.clientHeight,
              };
            }
            """
        )
        source = raw if isinstance(raw, dict) else {}
        return {
            key: _safe_layout_number(source.get(key))
            for key in ("inner_width", "inner_height", "viewport_width", "viewport_height")
        }

    def _production_window_snapshot(self) -> dict[str, Any]:
        """Read only the selected Flow target's native window and viewport geometry."""
        cdp = self._page_cdp_session
        if cdp is None:
            raise FlowAutomationError(
                "PRODUCTION_WINDOW_NORMALIZATION_FAILED: no CDP session exists for "
                "the selected dedicated Flow Chrome target."
            )
        target = cdp.send("Browser.getWindowForTarget")
        window_id = target.get("windowId") if isinstance(target, dict) else None
        if isinstance(window_id, bool) or not isinstance(window_id, int):
            raise FlowAutomationError(
                "PRODUCTION_WINDOW_NORMALIZATION_FAILED: Chrome did not identify the "
                "native window containing the selected Flow target."
            )
        current = cdp.send("Browser.getWindowBounds", {"windowId": window_id})
        bounds = current.get("bounds") if isinstance(current, dict) else None
        state = bounds.get("windowState") if isinstance(bounds, dict) else None
        window_state = (
            str(state)
            if state in {"normal", "minimized", "maximized", "fullscreen"}
            else "unknown"
        )
        try:
            viewport = self._read_production_viewport_geometry()
        except Exception:
            # Renderer/Angular remount can transiently make the DOM unavailable.
            # Browser-domain window identity remains valid and is still recoverable.
            viewport = {
                "inner_width": None,
                "inner_height": None,
                "viewport_width": None,
                "viewport_height": None,
            }
        return {
            "window_id": window_id,
            "window_state": window_state,
            "viewport": viewport,
        }

    @staticmethod
    def _safe_production_window_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
        viewport = snapshot.get("viewport") if isinstance(snapshot, dict) else None
        viewport = viewport if isinstance(viewport, dict) else {}
        return {
            "window_state": str(snapshot.get("window_state") or "unknown"),
            "viewport": {
                "width": _safe_layout_number(viewport.get("viewport_width")),
                "height": _safe_layout_number(viewport.get("viewport_height")),
            },
        }

    def _record_window_recovery_event(self, event: dict[str, Any]) -> None:
        self._window_recovery_events.append(event)
        if len(self._window_recovery_events) > _WINDOW_RECOVERY_EVENT_LIMIT:
            del self._window_recovery_events[:-_WINDOW_RECOVERY_EVENT_LIMIT]

    def _wait_for_production_window_state(
        self,
        window_id: int,
        expected_state: str,
        *,
        require_usable_viewport: bool,
    ) -> dict[str, Any]:
        cdp = self._page_cdp_session
        if cdp is None:
            raise FlowAutomationError("Production window CDP session was lost.")
        page = self._require_page()
        last_snapshot: dict[str, Any] = {
            "window_id": window_id,
            "window_state": "unknown",
            "viewport": {},
        }
        for attempt in range(_PRODUCTION_WINDOW_VERIFY_ATTEMPTS):
            cancellation_checkpoint("window_state_wait")
            current = cdp.send("Browser.getWindowBounds", {"windowId": window_id})
            bounds = current.get("bounds") if isinstance(current, dict) else None
            state = bounds.get("windowState") if isinstance(bounds, dict) else None
            try:
                viewport = self._read_production_viewport_geometry()
            except Exception:
                viewport = {
                    "inner_width": None,
                    "inner_height": None,
                    "viewport_width": None,
                    "viewport_height": None,
                }
            last_snapshot = {
                "window_id": window_id,
                "window_state": (
                    str(state)
                    if state in {"normal", "minimized", "maximized", "fullscreen"}
                    else "unknown"
                ),
                "viewport": viewport,
            }
            if last_snapshot["window_state"] == expected_state and (
                not require_usable_viewport
                or self._production_viewport_usable(last_snapshot["viewport"])
            ):
                return last_snapshot
            if attempt + 1 < _PRODUCTION_WINDOW_VERIFY_ATTEMPTS:
                page.wait_for_timeout(_PRODUCTION_WINDOW_VERIFY_INTERVAL_MS)
        raise FlowAutomationError(
            "Production window transition did not verify as "
            f"{expected_state}; last_state={last_snapshot['window_state']}; "
            "viewport="
            f"{last_snapshot['viewport'].get('viewport_width')}x"
            f"{last_snapshot['viewport'].get('viewport_height')}."
        )

    def _post_window_recovery_dom_snapshot(
        self,
        *,
        candidate_id: str | None,
    ) -> dict[str, Any]:
        """Read privacy-safe semantic readiness after a native-window transition."""
        page = self._require_page()
        raw = page.evaluate(
            r"""
            (candidateId) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none';
              };
              const promptSurfaces = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter(visible).filter((element) => {
                const description = String([
                  element.getAttribute('aria-label'), element.getAttribute('placeholder'),
                  element.getAttribute('name'), element.getAttribute('contenteditable')
                ].join(' ')).replace(/\s+/g, ' ').trim();
                return /prompt|describe|what do you want to create|imagine|generate|contenteditable/i
                  .test(description) &&
                  !/search|email|phone|password|project\s+name|name\s+project/i
                  .test(description);
              });
              const progressSurfaces = [...document.querySelectorAll(
                '[role="progressbar"],[role="status"],[aria-busy="true"]'
              )].filter(visible);
              const resultTiles = [...document.querySelectorAll('flow-image-tile')]
                .filter((element) => element.isConnected);
              const exactCandidateMatches = candidateId
                ? [...document.querySelectorAll('img[data-media-id]')].filter((image) =>
                    image.getAttribute('data-media-id') === candidateId
                  ).length
                : 0;
              const genericControls = [...document.querySelectorAll(
                'button,[role="button"],[role="textbox"],textarea,[contenteditable="true"]'
              )].filter((element) => element.isConnected);
              const attempt = window.__nanoBatchCurrentGenerationAttempt;
              return {
                ready_state: document.readyState,
                document_connected: Boolean(document.documentElement?.isConnected),
                body_connected: Boolean(document.body?.isConnected),
                body_child_count: document.body?.children?.length || 0,
                generic_control_count: genericControls.length,
                prompt_surface_count: promptSurfaces.length,
                progress_surface_count: progressSurfaces.length,
                result_tile_count: resultTiles.length,
                exact_candidate_match_count: exactCandidateMatches,
                attempt_observer_present: Boolean(attempt?.observer),
                attempt_observer_active: Boolean(attempt?.observer && attempt?.active),
                attempt_observer_provenance_lost: Boolean(attempt?.observer &&
                  (typeof attempt.hasFailureProvenance !== 'function' ||
                   !attempt.hasFailureProvenance())),
              };
            }
            """,
            candidate_id,
        )
        source = raw if isinstance(raw, dict) else {}
        ready_state = str(source.get("ready_state") or "other")
        if ready_state not in {"loading", "interactive", "complete", "other"}:
            ready_state = "other"
        return {
            "ready_state": ready_state,
            "document_connected": bool(source.get("document_connected")),
            "body_connected": bool(source.get("body_connected")),
            "body_child_count": _safe_layout_count(
                source.get("body_child_count"), maximum=100_000
            ),
            "generic_control_count": _safe_layout_count(
                source.get("generic_control_count"), maximum=100_000
            ),
            "prompt_surface_count": _safe_layout_count(
                source.get("prompt_surface_count"), maximum=1_000
            ),
            "progress_surface_count": _safe_layout_count(
                source.get("progress_surface_count"), maximum=1_000
            ),
            "result_tile_count": _safe_layout_count(
                source.get("result_tile_count"), maximum=10_000
            ),
            "exact_candidate_match_count": _safe_layout_count(
                source.get("exact_candidate_match_count"), maximum=100
            ),
            "attempt_observer_present": bool(source.get("attempt_observer_present")),
            "attempt_observer_active": bool(source.get("attempt_observer_active")),
            "attempt_observer_provenance_lost": bool(source.get("attempt_observer_provenance_lost")),
        }

    def _rearm_generation_observer_after_window_recovery(
        self,
        snapshot: dict[str, Any],
    ) -> bool:
        """Restore only post-Start observation state; never submit or reset ownership."""
        if snapshot.get("attempt_observer_provenance_lost"):
            raise FlowFailureAttributionAmbiguous(
                "the submitted attempt's original failure baseline or provenance was lost."
            )
        if snapshot.get("attempt_observer_active"):
            return False
        if not snapshot.get("attempt_observer_present"):
            raise FlowFailureAttributionAmbiguous(
                "the submitted attempt's failure observer and provenance were lost."
            )
        self._arm_current_generation_attempt_observation(preserve_attempt=True)
        return True

    def _wait_for_post_window_recovery_settle(
        self,
        *,
        operation_phase: str,
        candidate_id: str | None,
        require_semantic_surface: bool,
        same_generation_attempt_active: bool,
        start_acknowledged: bool,
        timeout_seconds: float = _POST_WINDOW_RECOVERY_SETTLE_TIMEOUT_SECONDS,
        poll_interval_ms: int = _POST_WINDOW_RECOVERY_SETTLE_INTERVAL_MS,
    ) -> dict[str, Any]:
        """Boundedly wait for the same Flow operation after maximize/remount."""
        if operation_phase not in {"generation", "result_wait", "download"}:
            raise ValueError("Unsupported post-window-recovery operation phase.")
        if not 0.1 <= float(timeout_seconds) <= 5.0:
            raise ValueError("Post-window-recovery settle timeout must be 0.1-5 seconds.")
        if not 50 <= int(poll_interval_ms) <= 500:
            raise ValueError("Post-window-recovery poll interval must be 50-500 ms.")
        if candidate_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{8,100}", candidate_id
        ):
            raise ValueError("Candidate data-media-id is invalid.")

        page = self._require_page()
        expected_project = _canonical_flow_project_reference(self.flow_url)
        started = time.perf_counter()
        deadline = time.monotonic() + float(timeout_seconds)
        poll_count = 0
        observer_rearmed = False
        last_dom: dict[str, Any] = {}
        last_window: dict[str, Any] = {
            "window_state": "unknown",
            "viewport": {"width": None, "height": None},
        }
        last_exact_project = expected_project is None
        last_error: str | None = None

        while True:
            cancellation_checkpoint("window_recovery_settle")
            poll_count += 1
            try:
                window = self._production_window_snapshot()
                last_window = self._safe_production_window_evidence(window)
                dom = self._post_window_recovery_dom_snapshot(
                    candidate_id=candidate_id
                )
                last_dom = dom
                if same_generation_attempt_active and (
                    not dom["attempt_observer_present"]
                    or dom.get("attempt_observer_provenance_lost")
                ):
                    raise FlowFailureAttributionAmbiguous(
                        "the submitted attempt's failure observer and provenance were lost."
                    )
                current_project = _canonical_flow_project_reference(
                    page.url if isinstance(getattr(page, "url", None), str) else None
                )
                last_exact_project = (
                    expected_project is None or current_project == expected_project
                )
                document_ready = bool(
                    dom["ready_state"] in {"interactive", "complete"}
                    and dom["document_connected"]
                    and dom["body_connected"]
                    and dom["body_child_count"] > 0
                )
                if require_semantic_surface:
                    if operation_phase == "generation":
                        semantic_ready = dom["prompt_surface_count"] > 0
                    elif operation_phase == "result_wait":
                        semantic_ready = any(
                            dom[key] > 0
                            for key in (
                                "prompt_surface_count",
                                "progress_surface_count",
                                "result_tile_count",
                            )
                        )
                    else:
                        semantic_ready = bool(
                            dom["prompt_surface_count"] > 0
                            or dom["result_tile_count"] > 0
                        )
                else:
                    semantic_ready = dom["generic_control_count"] > 0
                window_ready = bool(
                    window["window_state"] == "maximized"
                    and self._production_viewport_usable(window["viewport"])
                )
                basic_ready = bool(
                    window_ready
                    and last_exact_project
                    and document_ready
                    and semantic_ready
                )
                if basic_ready and same_generation_attempt_active:
                    observer_rearmed = bool(
                        self._rearm_generation_observer_after_window_recovery(dom)
                        or observer_rearmed
                    )
                    if observer_rearmed:
                        dom = self._post_window_recovery_dom_snapshot(
                            candidate_id=candidate_id
                        )
                        last_dom = dom
                    basic_ready = bool(dom.get("attempt_observer_active"))
                if basic_ready:
                    return {
                        "outcome": "ready",
                        "operation_phase": operation_phase,
                        "elapsed_ms": max(
                            0, round((time.perf_counter() - started) * 1_000)
                        ),
                        "poll_count": poll_count,
                        "same_generation_attempt_active": same_generation_attempt_active,
                        "start_acknowledged": start_acknowledged,
                        "observer_rearmed": observer_rearmed,
                        "exact_project": last_exact_project,
                        "window": last_window,
                        "dom": last_dom,
                    }
                last_error = None
            except FlowFailureAttributionAmbiguous:
                cancellation_checkpoint("window_recovery_settle")
                raise
            except Exception as exc:
                last_error = _safe_trace_header(str(exc))

            if time.monotonic() >= deadline:
                diagnostics = {
                    "outcome": "timeout",
                    "operation_phase": operation_phase,
                    "elapsed_ms": max(
                        0, round((time.perf_counter() - started) * 1_000)
                    ),
                    "poll_count": poll_count,
                    "same_generation_attempt_active": same_generation_attempt_active,
                    "start_acknowledged": start_acknowledged,
                    "observer_rearmed": observer_rearmed,
                    "exact_project": last_exact_project,
                    "window": last_window,
                    "dom": last_dom,
                    "last_error": last_error,
                }
                raise FlowAutomationError(
                    "POST_WINDOW_RECOVERY_NOT_READY: the restored Flow page did not "
                    "become semantically ready within the bounded settle window. "
                    "settle="
                    f"{json.dumps(diagnostics, sort_keys=True, separators=(',', ':'))}"
                )
            page.wait_for_timeout(int(poll_interval_ms))

    def ensure_production_window_usable(
        self,
        *,
        reason: str = "startup_nonmaximized",
        candidate_id: str | None = None,
        require_semantic_surface: bool = True,
    ) -> dict[str, Any]:
        """Maximize the selected window, then semantically settle the same operation."""
        if reason not in _WINDOW_RECOVERY_REASONS:
            raise ValueError("Unsupported production-window recovery reason.")
        if candidate_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{8,100}", candidate_id
        ):
            raise ValueError("Candidate data-media-id is invalid.")

        before = self._production_window_snapshot()
        operation_phase = (
            "result_wait"
            if reason == "generation_wait_minimized"
            else "download"
            if candidate_id is not None
            or reason in {"download_control_zero_geometry", "download_control_absent"}
            else "generation"
        )
        same_generation_attempt_active = operation_phase == "result_wait"
        start_acknowledged = same_generation_attempt_active
        if before["window_state"] == "maximized" and self._production_viewport_usable(
            before["viewport"]
        ):
            return {
                "classification": "WINDOW_RECOVERY",
                "recovery_required": False,
                "recovery_success": True,
                "reason": reason,
                "operation_phase": operation_phase,
                "same_generation_attempt_active": same_generation_attempt_active,
                "start_acknowledged": start_acknowledged,
                "before": self._safe_production_window_evidence(before),
                "after": self._safe_production_window_evidence(before),
                "restoration_elapsed_ms": 0,
                "settle_elapsed_ms": 0,
                **({"candidate_id": candidate_id} if candidate_id is not None else {}),
            }

        recovery_started = time.perf_counter()
        event: dict[str, Any] = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": True,
            "recovery_success": False,
            "reason": reason,
            "operation_phase": operation_phase,
            "same_generation_attempt_active": same_generation_attempt_active,
            "start_acknowledged": start_acknowledged,
            "before": self._safe_production_window_evidence(before),
            "after": {"window_state": "unknown", "viewport": {"width": None, "height": None}},
            "restoration_elapsed_ms": 0,
            "settle_elapsed_ms": 0,
        }
        if candidate_id is not None:
            event["candidate_id"] = candidate_id
        cdp = self._page_cdp_session
        window_id = int(before["window_id"])
        window_restored = False
        try:
            if cdp is None:
                raise FlowAutomationError("Production window CDP session was lost.")
            if before["window_state"] in {"minimized", "fullscreen", "unknown"} or (
                before["window_state"] == "maximized"
                and not self._production_viewport_usable(before["viewport"])
            ):
                cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "normal"}},
                )
                self._wait_for_production_window_state(
                    window_id,
                    "normal",
                    require_usable_viewport=False,
                )
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "maximized"}},
            )
            after = self._wait_for_production_window_state(
                window_id,
                "maximized",
                require_usable_viewport=True,
            )
            event["after"] = self._safe_production_window_evidence(after)
            event["restoration_elapsed_ms"] = max(
                0, round((time.perf_counter() - recovery_started) * 1_000)
            )
            window_restored = True
            settle = self._wait_for_post_window_recovery_settle(
                operation_phase=operation_phase,
                candidate_id=candidate_id,
                require_semantic_surface=require_semantic_surface,
                same_generation_attempt_active=same_generation_attempt_active,
                start_acknowledged=start_acknowledged,
            )
            event["settle"] = settle
            event["settle_elapsed_ms"] = int(settle.get("elapsed_ms") or 0)
            event["recovery_success"] = True
            self._record_window_recovery_event(event)
            return event
        except Exception as exc:
            total_elapsed_ms = max(
                0, round((time.perf_counter() - recovery_started) * 1_000)
            )
            if not window_restored:
                event["restoration_elapsed_ms"] = total_elapsed_ms
                event["failure_stage"] = "window_normalization"
            else:
                event["settle_elapsed_ms"] = max(
                    0, total_elapsed_ms - int(event["restoration_elapsed_ms"])
                )
                event["failure_stage"] = "post_restore_settle"
            event["failure_detail"] = _safe_trace_header(str(exc))
            if not isinstance(exc, FlowFailureAttributionAmbiguous):
                try:
                    event["after"] = self._safe_production_window_evidence(
                        self._production_window_snapshot()
                    )
                except Exception:
                    pass
            self._record_window_recovery_event(event)
            if isinstance(exc, FlowFailureAttributionAmbiguous):
                raise
            raise FlowAutomationError(
                "PRODUCTION_WINDOW_NORMALIZATION_FAILED: the dedicated Flow Chrome "
                "could not be put into a usable maximized desktop layout. Close and "
                "relaunch it through Launch Chrome, then Resume current prompts. "
                "recovery="
                f"{json.dumps(event, sort_keys=True, separators=(',', ':'))}"
            ) from exc

    def _select_flow_project_page(self, pages: list):
        project_pages = [page for page in pages if self._is_flow_project_url(page.url)]
        configured_url = _safe_visible_url(self.flow_url)
        if self._is_flow_project_url(configured_url):
            matching = [
                page
                for page in project_pages
                if (_safe_visible_url(page.url) or "").rstrip("/")
                == configured_url.rstrip("/")
            ]
        else:
            matching = project_pages

        if not matching:
            raise FlowTabNotFoundError(
                "No existing Chrome tab matches the configured Flow project URL. "
                "Keep the project open in the Chrome instance started by `open-flow-debug`."
            )
        if len(matching) > 1:
            raise FlowTabAmbiguousError(
                f"Found {len(matching)} matching Flow project tabs. Close duplicates so exactly one remains."
            )
        return matching[0]

    @staticmethod
    def _select_flow_page_for_reconnaissance(pages: list):
        """Select one existing Flow tab without assuming its current project ID."""
        flow_pages = [
            page
            for page in pages
            if _flow_project_identity(getattr(page, "url", None))["is_flow_url"]
        ]
        if not flow_pages:
            raise FlowTabNotFoundError(
                "No existing flow.google.com tab is open in the dedicated Chrome session."
            )
        if len(flow_pages) > 1:
            raise FlowTabAmbiguousError(
                f"Found {len(flow_pages)} Flow tabs. Close duplicates so read-only "
                "reconnaissance cannot inspect the wrong tab."
            )
        return flow_pages[0]

    @staticmethod
    def _project_creation_control_snapshot(locator) -> dict[str, Any]:
        """Read only the structural/actionability facts used for project creation."""
        raw = locator.evaluate(
            r"""
            (element) => {
              const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const labelledBy = (element.getAttribute('aria-labelledby') || '')
                .split(/\s+/).filter(Boolean)
                .map((id) => document.getElementById(id))
                .filter(Boolean)
                .map((node) => node.textContent || '')
                .join(' ');
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return {
                tag: element.tagName.toLowerCase(),
                visible_text: clean(element.innerText || ''),
                accessible_name: clean(
                  element.getAttribute('aria-label') || labelledBy ||
                  element.getAttribute('title') || element.innerText || ''
                ),
                region: element.closest('[class*="project" i],[data-testid*="project" i]')
                  ? 'project_list' : 'other',
                connected: element.isConnected,
                visible: element.isConnected && style.visibility !== 'hidden' &&
                  style.display !== 'none' && Number(style.opacity || 1) > 0 &&
                  rect.width > 0 && rect.height > 0,
                enabled: !element.matches(':disabled') &&
                  element.getAttribute('aria-disabled') !== 'true',
                pointer_events: style.pointerEvents,
                width: rect.width,
                height: rect.height,
              };
            }
            """
        )
        if not isinstance(raw, dict):
            return {}
        try:
            width = float(raw.get("width") or 0)
            height = float(raw.get("height") or 0)
        except (TypeError, ValueError, OverflowError):
            width = height = 0.0
        return {
            "tag": str(raw.get("tag") or "").lower(),
            "visible_text": re.sub(
                r"\s+", " ", str(raw.get("visible_text") or "")
            ).strip(),
            "accessible_name": re.sub(
                r"\s+", " ", str(raw.get("accessible_name") or "")
            ).strip(),
            "region": str(raw.get("region") or "other").lower(),
            "connected": bool(raw.get("connected")),
            "visible": bool(raw.get("visible")),
            "enabled": bool(raw.get("enabled")),
            "pointer_events": str(raw.get("pointer_events") or "").lower(),
            "width": width,
            "height": height,
        }

    @staticmethod
    def _is_proven_project_creation_control(snapshot: dict[str, Any]) -> bool:
        proven_names = {
            str(snapshot.get("visible_text") or "").casefold(),
            str(snapshot.get("accessible_name") or "").casefold(),
        }
        return bool(
            snapshot.get("tag") == "button"
            and "add new project" in proven_names
            and snapshot.get("region") == "project_list"
            and snapshot.get("connected") is True
            and snapshot.get("visible") is True
            and snapshot.get("enabled") is True
            and snapshot.get("pointer_events") != "none"
            and float(snapshot.get("width") or 0) > 0
            and float(snapshot.get("height") or 0) > 0
        )

    def _matching_project_creation_controls(self) -> list:
        page = self._require_page()
        matches = []
        for locator in page.locator("button").all():
            try:
                snapshot = self._project_creation_control_snapshot(locator)
            except Exception:
                continue
            if self._is_proven_project_creation_control(snapshot):
                matches.append(locator)
        return matches

    def _unique_project_creation_control(self):
        matches = self._matching_project_creation_controls()
        if len(matches) != 1:
            raise FlowAutomationError(
                f"{FLOW_PROJECT_CREATE_CONTROL_UNPROVEN}: expected exactly one "
                "connected, visible, enabled project-list button named "
                f"'add New project'; found {len(matches)}. No project-creation click "
                "occurred."
            )
        return matches[0]

    def _wait_for_project_creation_control(self):
        page = self._require_page()
        for _ in range(_PROJECT_IDENTITY_POLL_ATTEMPTS):
            cancellation_checkpoint("project_creation_control_wait")
            matches = self._matching_project_creation_controls()
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise FlowAutomationError(
                    f"{FLOW_PROJECT_CREATE_CONTROL_UNPROVEN}: found multiple "
                    "connected, visible, enabled project-list buttons named "
                    "'add New project'. No project-creation click occurred."
                )
            page.wait_for_timeout(_PROJECT_IDENTITY_POLL_INTERVAL_MS)
        raise FlowAutomationError(
            f"{FLOW_PROJECT_CREATE_CONTROL_UNPROVEN}: no connected, visible, enabled "
            "project-list button named 'add New project' became available on Flow "
            "home. No project-creation click occurred."
        )

    def _open_current_flow_page_for_project_operation(self) -> "FlowSession":
        """Attach to the one current Flow tab and apply existing production hardening."""
        self.open_read_only_project_reconnaissance()
        page = self._require_page()
        try:
            self._page_cdp_session = page.context.new_cdp_session(page)
            self._verify_live_background_flags()
            self._enable_page_focus_emulation(page)
            self._enable_page_lifecycle_keepalive()
            return self
        except Exception:
            self.close()
            raise

    def _wait_for_flow_home(self) -> None:
        page = self._require_page()
        stable_polls = 0
        for _ in range(_PROJECT_IDENTITY_POLL_ATTEMPTS):
            cancellation_checkpoint("flow_home_wait")
            raw_url = getattr(page, "url", None)
            safe_url = _safe_visible_url(raw_url if isinstance(raw_url, str) else None)
            if safe_url == FLOW_HOME_URL:
                stable_polls += 1
                if stable_polls >= 3:
                    return
            else:
                identity = _flow_project_identity(
                    raw_url if isinstance(raw_url, str) else None
                )
                if not identity.get("is_flow_url"):
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_CREATION_FAILED}: navigation to Flow home "
                        "left flow.google.com. No project-creation click occurred."
                    )
                stable_polls = 0
            page.wait_for_timeout(_PROJECT_IDENTITY_POLL_INTERVAL_MS)
        raise FlowAutomationError(
            f"{FLOW_PROJECT_CREATION_FAILED}: the browser did not stabilize on "
            f"canonical Flow home {FLOW_HOME_URL}. No project-creation click occurred."
        )

    def _wait_for_new_project_reference(self) -> FlowProjectReference:
        page = self._require_page()
        observed_new: FlowProjectReference | None = None
        stable_polls = 0
        for _ in range(_PROJECT_IDENTITY_POLL_ATTEMPTS):
            cancellation_checkpoint("new_project_identity_wait")
            raw_url = getattr(page, "url", None)
            current = _canonical_flow_project_reference(raw_url)
            if _safe_visible_url(raw_url) == FLOW_HOME_URL:
                if observed_new is not None:
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: the URL returned to Flow "
                        "home after a possible project transition. The creation "
                        "control will not be clicked again."
                    )
                stable_polls = 0
            elif current is None:
                identity = _flow_project_identity(raw_url if isinstance(raw_url, str) else None)
                code = (
                    FLOW_PROJECT_IDENTITY_UNPROVEN
                    if identity.get("is_project_url")
                    else FLOW_PROJECT_CREATION_FAILED
                )
                raise FlowAutomationError(
                    f"{code}: project creation left Flow home but did not "
                    "produce a valid https://flow.google.com/project/<uuid> identity. "
                    "The creation control will not be clicked again."
                )
            else:
                if observed_new is None:
                    observed_new = current
                    stable_polls = 1
                elif current != observed_new:
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: more than one possible new "
                        "project identity was observed. The creation control will not be "
                        "clicked again."
                    )
                else:
                    stable_polls += 1
                if observed_new is not None and stable_polls >= 3:
                    return observed_new
            page.wait_for_timeout(_PROJECT_IDENTITY_POLL_INTERVAL_MS)
        if observed_new is None:
            raise FlowAutomationError(
                f"{FLOW_PROJECT_CREATION_FAILED}: the URL remained on Flow home "
                "after one normal creation click. The control will not be "
                "clicked again."
            )
        raise FlowAutomationError(
            f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: the new project identity did not "
            "stabilize after one creation click. The control will not be clicked again."
        )

    def _wait_for_saved_project_reference(
        self,
        expected: FlowProjectReference,
    ) -> None:
        page = self._require_page()
        stable_polls = 0
        for _ in range(_PROJECT_IDENTITY_POLL_ATTEMPTS):
            cancellation_checkpoint("saved_project_identity_wait")
            raw_url = getattr(page, "url", None)
            current = _canonical_flow_project_reference(raw_url)
            if current == expected:
                stable_polls += 1
                if stable_polls >= 3:
                    return
            else:
                identity = _flow_project_identity(raw_url if isinstance(raw_url, str) else None)
                if not identity.get("is_flow_url"):
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_REOPEN_FAILED}: navigation left flow.google.com; "
                        "no generation or download was attempted."
                    )
                stable_polls = 0
            page.wait_for_timeout(_PROJECT_IDENTITY_POLL_INTERVAL_MS)
        raise FlowAutomationError(
            f"{FLOW_PROJECT_REOPEN_FAILED}: the browser did not stabilize on the "
            f"saved Flow project {expected.project_id}; no generation or download "
            "was attempted."
        )

    def create_flow_project(self) -> FlowProjectReference:
        """Create exactly one project from the proven Flow-home project-list control."""
        self._open_current_flow_page_for_project_operation()
        try:
            page = self._require_page()
            current_identity = _flow_project_identity(page.url)
            if not current_identity.get("is_flow_url"):
                raise FlowAutomationError(
                    f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: project creation requires "
                    "exactly one current flow.google.com tab."
                )
            self.ensure_production_window_usable(
                reason="startup_nonmaximized",
                require_semantic_surface=False,
            )
            try:
                page.goto(
                    FLOW_HOME_URL,
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
            except Exception as exc:
                cancellation_checkpoint("project_creation_home_navigation")
                raise FlowAutomationError(
                    f"{FLOW_PROJECT_CREATION_FAILED}: canonical Flow home could not "
                    "be opened. No project-creation click occurred."
                ) from exc
            self._wait_for_flow_home()
            control = self._wait_for_project_creation_control()
            try:
                control.click(timeout=10_000)
            except Exception as exc:
                cancellation_checkpoint("project_creation_activation")
                raise FlowAutomationError(
                    f"{FLOW_PROJECT_CREATION_FAILED}: the unique Flow-home "
                    "'add New project' button could not be activated by one normal "
                    "Playwright click. It will not be clicked again."
                ) from exc
            project = self._wait_for_new_project_reference()
            self.flow_url = project.project_url
            return project
        finally:
            self.close(intentional_cancellation=cancellation_requested())

    def prepare_flow_project(
        self,
        *,
        project: FlowProjectReference,
        settings: GenerationSettings | None,
    ) -> None:
        """Open the exact saved project and reuse the production readiness gate."""
        project.validate()
        expected = _canonical_flow_project_reference(project.project_url)
        if expected != project:
            raise FlowAutomationError(
                f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: saved Flow project metadata is "
                "not an exact normalized project reference."
            )
        self._open_current_flow_page_for_project_operation()
        try:
            page = self._require_page()
            if _canonical_flow_project_reference(page.url) != project:
                try:
                    page.goto(
                        project.project_url,
                        wait_until="domcontentloaded",
                        timeout=15_000,
                    )
                except Exception as exc:
                    cancellation_checkpoint("saved_project_navigation")
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_REOPEN_FAILED}: the saved Flow project could "
                        "not be opened; no generation or download was attempted."
                    ) from exc
            self._wait_for_saved_project_reference(project)
            self.flow_url = project.project_url
            self.ensure_production_window_usable(
                reason="startup_nonmaximized",
                require_semantic_surface=False,
            )
            if settings is not None:
                inspection = self._wait_for_authenticated_image_editor(project)
                self.configure_generation(settings, inspection=inspection)
        finally:
            self.close(intentional_cancellation=cancellation_requested())

    def _recover_unusual_activity_in_open_session(
        self,
        *,
        project: FlowProjectReference,
    ) -> FlowInspection:
        """Return the current page to home and reopen its exact saved project."""
        project.validate()
        expected = _canonical_flow_project_reference(project.project_url)
        if expected != project:
            raise FlowAutomationError(
                f"{FLOW_PROJECT_IDENTITY_UNPROVEN}: unusual-activity recovery "
                "requires an exact normalized saved project reference."
            )
        page = self._require_page()
        browser = self._browser
        page_cdp_session = self._page_cdp_session
        if browser is None or page_cdp_session is None:
            raise FlowAutomationError(
                f"{FLOW_PROJECT_REOPEN_FAILED}: unusual-activity recovery requires "
                "the existing open browser and page CDP session."
            )
        self._set_generation_interrupt_checkpoint(
            "unusual_activity_home_navigation"
        )
        try:
            page.goto(
                FLOW_HOME_URL,
                wait_until="domcontentloaded",
                timeout=15_000,
            )
        except Exception as exc:
            cancellation_checkpoint("unusual_activity_home_navigation")
            raise FlowAutomationError(
                f"{FLOW_PROJECT_REOPEN_FAILED}: Flow home could not be opened in the "
                "existing tab after unusual activity."
            ) from exc
        self._wait_for_flow_home()
        self._set_generation_interrupt_checkpoint(
            "unusual_activity_project_reopen"
        )
        try:
            page.goto(
                project.project_url,
                wait_until="domcontentloaded",
                timeout=15_000,
            )
        except Exception as exc:
            cancellation_checkpoint("unusual_activity_project_reopen")
            raise FlowAutomationError(
                f"{FLOW_PROJECT_REOPEN_FAILED}: the exact saved Flow project could "
                "not be reopened in the existing tab after unusual activity."
            ) from exc
        self._wait_for_saved_project_reference(project)
        if (
            self.page is not page
            or self._browser is not browser
            or self._page_cdp_session is not page_cdp_session
        ):
            raise FlowAutomationError(
                f"{FLOW_PROJECT_REOPEN_FAILED}: browser, page, or CDP session identity "
                "changed during unusual-activity recovery."
            )
        self.flow_url = project.project_url
        self._set_generation_interrupt_checkpoint(
            "unusual_activity_editor_readiness"
        )
        self.ensure_production_window_usable(
            reason="startup_nonmaximized",
            require_semantic_surface=False,
        )
        return self._wait_for_authenticated_image_editor(project)

    def open(self) -> "FlowSession":
        if self.page is not None:
            return self
        sync_playwright = self._playwright_api()
        try:
            self._playwright = sync_playwright().start()
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception:
            if cancellation_requested():
                self.close(intentional_cancellation=True)
                cancellation_checkpoint("session_playwright_start")
            raise
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.cdp_endpoint,
                timeout=10_000,
                is_local=True,
                no_defaults=True,
            )
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception as exc:
            self.close(intentional_cancellation=cancellation_requested())
            cancellation_checkpoint("session_cdp_attach")
            raise FlowDebugUnavailableError(
                f"Chrome's local debugging endpoint is unavailable at {self.cdp_endpoint}. "
                "Run `python app.py open-flow-debug`, keep that Chrome window open, and retry."
            ) from exc

        try:
            pages = [page for context in self._browser.contexts for page in context.pages]
            self.page = self._select_flow_project_page(pages)
            self._page_cdp_session = self.page.context.new_cdp_session(self.page)
            self._verify_live_background_flags()
            self._enable_page_focus_emulation(self.page)
            self._enable_page_lifecycle_keepalive()
            return self
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception:
            self.close(intentional_cancellation=cancellation_requested())
            cancellation_checkpoint("session_setup")
            raise

    def open_read_only_project_reconnaissance(self) -> "FlowSession":
        """Attach to exactly one Flow tab without focus, lifecycle, or page actions."""
        if self.page is not None:
            return self
        sync_playwright = self._playwright_api()
        try:
            self._playwright = sync_playwright().start()
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception:
            if cancellation_requested():
                self.close(intentional_cancellation=True)
                cancellation_checkpoint("read_only_session_playwright_start")
            raise
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.cdp_endpoint,
                timeout=10_000,
                is_local=True,
                no_defaults=True,
            )
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception as exc:
            self.close(intentional_cancellation=cancellation_requested())
            cancellation_checkpoint("read_only_session_cdp_attach")
            raise FlowDebugUnavailableError(
                f"Chrome's local debugging endpoint is unavailable at {self.cdp_endpoint}. "
                "Run `python app.py open-flow-debug`, keep that Chrome window open, and retry."
            ) from exc

        try:
            pages = [page for context in self._browser.contexts for page in context.pages]
            self.page = self._select_flow_page_for_reconnaissance(pages)
            return self
        except KeyboardInterrupt:
            self.close(intentional_cancellation=True)
            raise
        except Exception:
            self.close(intentional_cancellation=cancellation_requested())
            cancellation_checkpoint("read_only_session_setup")
            raise

    def close(self, *, intentional_cancellation: bool = False) -> None:
        # Existing session detach/disconnect owns domain teardown at close.
        self._stop_generation_network_timing(skip_rpc=True)
        page_cdp_session = self._page_cdp_session
        focus_emulation_enabled = self._focus_emulation_enabled
        browser = self._browser
        playwright = self._playwright
        self.page = None
        self._page_cdp_session = None
        self._focus_emulation_enabled = False
        self._browser = None
        self._playwright = None
        asyncio_logger = logging.getLogger("asyncio")
        teardown_filter = _IntentionalCancellationTransportNoiseFilter()
        if intentional_cancellation:
            asyncio_logger.addFilter(teardown_filter)
            mark_cleanup_started()
        cleanup_error: BaseException | None = None
        try:
            if intentional_cancellation:
                # The first Ctrl+C has already made the active Playwright call
                # non-authoritative. Avoid starting more synchronous CDP/browser
                # RPCs during unwind; stopping Playwright disconnects its driver
                # and externally attached Chrome without closing that Chrome.
                if playwright is not None:
                    try:
                        playwright.stop()
                    except BaseException as exc:
                        cleanup_error = exc
                        if not isinstance(exc, KeyboardInterrupt):
                            logging.getLogger(__name__).error(
                                "Playwright teardown failed during intentional "
                                "cancellation: %s",
                                type(exc).__name__,
                            )
                return
            try:
                if page_cdp_session is not None:
                    try:
                        if focus_emulation_enabled:
                            page_cdp_session.send(
                                "Emulation.setFocusEmulationEnabled",
                                {"enabled": False},
                            )
                    except Exception:
                        pass
                    try:
                        page_cdp_session.detach()
                    except Exception:
                        pass
            finally:
                try:
                    if browser is not None:
                        # Browser.close() disconnects from this externally launched Chrome.
                        browser.close()
                finally:
                    if playwright is not None:
                        playwright.stop()
        finally:
            if intentional_cancellation:
                asyncio_logger.removeFilter(teardown_filter)
                mark_cleanup_completed(error=cleanup_error)

    def __enter__(self) -> "FlowSession":
        return self.open()

    def __exit__(self, exc_type, exc, traceback) -> None:
        interrupt_is_active = bool(
            exc_type is not None and issubclass(exc_type, KeyboardInterrupt)
        )
        intentional_cancellation = interrupt_is_active or cancellation_requested()
        self.close(intentional_cancellation=intentional_cancellation)
        if intentional_cancellation and not interrupt_is_active:
            cancellation_checkpoint("session_exit")

    def _require_page(self):
        if self.page is None:
            raise RuntimeError("Flow session is not open.")
        return self.page

    def open_for_manual_reconnaissance(self) -> None:
        """Launch normal Chrome for user-controlled login; Playwright is never attached."""
        chrome = find_installed_chrome()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        command = [
            str(chrome),
            f"--user-data-dir={self.profile_dir.resolve()}",
            self.flow_url,
        ]
        subprocess.Popen(command, shell=False)
        print("\nNormal installed Google Chrome opened for manual authentication.")
        print(f"Chrome: {chrome}")
        print(f"Dedicated profile: {self.profile_dir.resolve()}")
        print("This command does not attach Playwright or automate Google login.")
        print("Log in normally, confirm the Flow project works, then close this bootstrap window.")
        print("Next run `python app.py open-flow-debug` for the attachable session.\n")

    def open_for_debugging(self) -> None:
        """Launch normal headed Chrome with loopback-only CDP; never attach or automate login."""
        if self._debug_endpoint_available():
            print(f"Chrome debugging is already available at {self.cdp_endpoint}.")
            print(
                "Required minimized/background flags will be verified when a Flow "
                "session attaches; status is reported as unknown if Chrome cannot "
                "expose its command line."
            )
            print("Run `python app.py inspect-flow` to inspect the existing Flow tab.")
            return
        if self._profile_lock_markers():
            raise self._profile_locked_error()
        chrome = find_installed_chrome()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        command = self._debug_launch_command(chrome)
        subprocess.Popen(command, shell=False)
        print("\nNormal installed Google Chrome opened with local debugging enabled.")
        print(f"Chrome: {chrome}")
        print(f"Dedicated profile: {self.profile_dir.resolve()}")
        print(f"Debug endpoint: {self.cdp_endpoint} (loopback only)")
        print(f"Flow destination: {_safe_visible_url(self.flow_url)}")
        print("Required minimized/background flags will be verified on session attachment.")
        print("Keep this Chrome instance open and run `python app.py inspect-flow`.")
        print("No login, page interaction, or generation was automated.\n")

    def _stabilize_page(self, timeout_ms: int = 12_000) -> dict[str, Any]:
        """Wait only for a bounded, read-only SPA snapshot to become stable."""
        page = self._require_page()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5_000))
        except Exception:
            pass
        deadline = time.monotonic() + timeout_ms / 1_000
        prior = None
        stable_polls = 0
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            cancellation_checkpoint("page_stabilization_wait")
            latest = page.evaluate(
                """
                () => ({
                  readyState: document.readyState,
                  bodyTextLength: (document.body?.innerText || '').length,
                  controls: document.querySelectorAll(
                    'button,[role="button"],[role="textbox"],textarea,[contenteditable="true"]'
                  ).length,
                })
                """
            )
            signature = (
                latest.get("readyState"),
                latest.get("bodyTextLength"),
                latest.get("controls"),
            )
            stable_polls = stable_polls + 1 if signature == prior else 1
            if latest.get("readyState") in {"interactive", "complete"} and stable_polls >= 3:
                break
            prior = signature
            page.wait_for_timeout(500)
        return latest

    def _capture_diagnostic_screenshot(self) -> tuple[str | None, str | None, str | None]:
        """Capture pixels read-only without allowing screenshot work to hang inspection."""
        page = self._require_page()
        self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(self.diagnostics_path.resolve())
        try:
            page.screenshot(path=screenshot_path, full_page=False, timeout=5_000)
            return screenshot_path, "playwright", None
        except Exception:
            if self.diagnostics_path.is_file():
                return (
                    screenshot_path,
                    "existing",
                    "New screenshot capture timed out or failed; the prior diagnostic was retained.",
                )
            return None, None, "Diagnostic screenshot capture timed out or failed."

    @_ack_profiled("_read_active_composer_generation_resolution")
    def _read_active_composer_generation_resolution(self) -> dict[str, Any]:
        """Resolve Start only through the unique active prompt composer's structure."""
        page = self._require_page()
        raw = _ack_call("composer_page_evaluate", page.evaluate,
            r"""
            () => {
              const limit = 30;
              const clean = (value) => String(value || '')
                .replace(/\s+/g, ' ').trim().slice(0, 120);
              const generationPattern = /^(?:(?:start\s+)?generation|generate|create|submit|send)(?:\s+(?:image|images))?$/i;
              const composerSettingPattern = /\b(?:nano[\s_-]+banana|imagen|gemini|veo)\b|(?:^|\s)\d+:\d+(?:$|\s)|\bx\s*\d+\b/i;
              const promptPattern = /prompt|describe|what do you want to create|imagine|generate|idea/i;
              const excludedPromptPattern = /search|email|phone|password/i;
              const box = (element) => {
                const rect = element.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  Number.parseFloat(style.opacity || '1') > 0.01;
              };
              const enabled = (element) => !Boolean(element.disabled) &&
                String(element.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
              const controlLabel = (element) => clean(
                element.getAttribute('aria-label') || element.getAttribute('title') ||
                element.innerText || element.textContent || ''
              );
              const structural = (element) => visible(element);
              const actionable = (element) => structural(element) && enabled(element) &&
                getComputedStyle(element).pointerEvents !== 'none';
              const editables = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter((element) => visible(element) && enabled(element));
              const promptEditors = editables.filter((element) => {
                const description = clean([
                  element.getAttribute('aria-label'), element.getAttribute('placeholder'),
                  element.getAttribute('name'), element.getAttribute('contenteditable')
                ].join(' '));
                return element.getAttribute('contenteditable') === 'true' ||
                  (promptPattern.test(description) && !excludedPromptPattern.test(description));
              });
              const generationControls = [...document.querySelectorAll(
                'button,[role="button"]'
              )].filter((element) => element.isConnected &&
                generationPattern.test(controlLabel(element)));
              const scanTruncated = generationControls.length > limit;
              const boundedControls = generationControls.slice(0, limit);
              const prompt = promptEditors.length === 1 ? promptEditors[0] : null;
              const settingEvidence = [...document.querySelectorAll(
                'button,[role="button"],[role="combobox"],[role="radio"],'
                + '[aria-haspopup],span,label'
              )].filter((element) => visible(element) &&
                (!prompt || !prompt.contains(element)) &&
                composerSettingPattern.test(controlLabel(element))).slice(0, 100);
              const ancestorCandidates = [];
              if (prompt) {
                for (let node = prompt.parentElement, depth = 1;
                     node && node !== document.body && node !== document.documentElement && depth <= 20;
                     node = node.parentElement, depth += 1) {
                  const owned = boundedControls.filter((control) => node.contains(control));
                  if (!owned.length) continue;
                  const role = clean(node.getAttribute('role')).toLowerCase();
                  const tag = node.tagName.toLowerCase();
                  const identity = clean([
                    tag, role, node.getAttribute('aria-label'), node.getAttribute('title'),
                    node.getAttribute('id'), node.getAttribute('data-testid'),
                    typeof node.className === 'string' ? node.className : ''
                  ].join(' '));
                  const semantic = tag === 'form' || role === 'form' ||
                    tag.includes('composer') ||
                    /composer|prompt[\s_-]*(?:input|bar|area|composer)|generation[\s_-]*(?:input|bar|composer)|creation[\s_-]*(?:input|bar|composer)/i.test(identity);
                  ancestorCandidates.push({
                    node, depth, owned, semantic,
                    settingCount: settingEvidence.filter((item) => node.contains(item)).length,
                    structurallyOwned: owned.filter(structural),
                    actionableOwned: owned.filter(actionable),
                  });
                }
              }
              const selected = ancestorCandidates.find((item) =>
                item.settingCount > 0 && item.structurallyOwned.length > 0
              ) || ancestorCandidates.find((item) =>
                item.semantic && item.structurallyOwned.length > 0
              ) || ancestorCandidates.find((item) =>
                item.structurallyOwned.length > 0 && item.depth <= 10 &&
                !['main', 'section', 'article'].includes(item.node.tagName.toLowerCase()) &&
                clean(item.node.getAttribute('role')).toLowerCase() !== 'main'
              ) || null;
              let tokenState = window.__nanoBatchComposerIdentityV1;
              if (!tokenState?.tokens || !(tokenState.tokens instanceof WeakMap)) {
                tokenState = {tokens: new WeakMap(), next: 1};
                Object.defineProperty(window, '__nanoBatchComposerIdentityV1', {
                  value: tokenState, configurable: true,
                });
              }
              const composerToken = selected ? (() => {
                let token = tokenState.tokens.get(selected.node);
                if (!token) {
                  token = Math.min(1000000000, tokenState.next++);
                  tokenState.tokens.set(selected.node, token);
                }
                return token;
              })() : null;
              const structurallyOwned = selected?.structurallyOwned || [];
              const actionableOwned = selected?.actionableOwned || [];
              const acceptedOwned = actionableOwned.length === 1
                ? actionableOwned
                : actionableOwned.length === 0 && structurallyOwned.length === 1
                ? structurallyOwned
                : [];
              const promptValue = prompt
                ? ('value' in prompt ? prompt.value : prompt.textContent)
                : '';
              const candidateFacts = boundedControls.map((element, index) => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                const ariaDisabled = String(
                  element.getAttribute('aria-disabled') || ''
                ).toLowerCase() === 'true';
                const inactiveReasons = [];
                if (!element.isConnected) inactiveReasons.push('disconnected');
                if (!visible(element)) inactiveReasons.push('hidden_or_zero_geometry');
                if (Boolean(element.disabled)) inactiveReasons.push('disabled_attribute');
                if (ariaDisabled) inactiveReasons.push('aria_disabled');
                if (style.pointerEvents === 'none') inactiveReasons.push('pointer_events_none');
                const relationship = selected?.node.contains(element)
                  ? 'descendant' : 'outside';
                return {
                  candidate_index: index,
                  tag: element.tagName.toLowerCase(),
                  role: clean(element.getAttribute('role')).toLowerCase() || null,
                  relationship,
                  connected: element.isConnected,
                  visible: visible(element),
                  enabled: enabled(element),
                  positive_geometry: rect.width > 0 && rect.height > 0,
                  pointer_events: style.pointerEvents,
                  inactive_reasons: inactiveReasons,
                  bounding_box: box(element),
                };
              });
              let failureReason = 'ready';
              if (promptEditors.length !== 1) failureReason = 'prompt_editor_ambiguous';
              else if (scanTruncated) failureReason = 'control_scan_truncated';
              else if (!selected) failureReason = 'active_composer_unproven';
              else if (structurallyOwned.length === 0) {
                failureReason = 'owned_generation_control_missing';
              } else if (acceptedOwned.length !== 1) {
                failureReason = 'owned_generation_control_ambiguous';
              }
              const structuralReady = failureReason === 'ready';
              const generationControlActionable = structuralReady &&
                actionableOwned.length === 1 && acceptedOwned[0] === actionableOwned[0];
              return {
                schema: 'nano-banana-composer-generation/v1',
                prompt_editor_count: promptEditors.length,
                global_generation_control_count: generationControls.length,
                global_visible_generation_control_count:
                  generationControls.filter(visible).length,
                active_composer_count: selected ? 1 : 0,
                active_composer_generation_control_count: selected?.owned.length || 0,
                structurally_owned_generation_control_count: structurallyOwned.length,
                actionable_owned_generation_control_count: actionableOwned.length,
                ignored_generation_control_count: Math.max(
                  0, generationControls.length - structurallyOwned.length
                ),
                composer_depth: selected?.depth || null,
                composer_token: composerToken,
                composer_semantic: Boolean(
                  selected && (selected.semantic || selected.settingCount > 0)
                ),
                scan_truncated: scanTruncated,
                structural_generation_control_ready: structuralReady,
                generation_control_actionable: generationControlActionable,
                ready: structuralReady,
                failure_reason: failureReason,
                owned_control_label: structuralReady
                  ? controlLabel(acceptedOwned[0]) : null,
                prompt_populated: Boolean(clean(promptValue)),
                candidates: candidateFacts,
              };
            }
            """
        )
        return _normalize_composer_generation_resolution(raw)

    def inspect_environment(
        self,
        *,
        capture_screenshot: bool = False,
        stabilize: bool = True,
    ) -> FlowInspection:
        page = self._require_page()
        if stabilize:
            self._stabilize_page()
        raw = page.evaluate(
            """
            () => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const label = (element) => (
                element.getAttribute('aria-label') || element.getAttribute('title') ||
                element.getAttribute('placeholder') || element.innerText ||
                element.textContent || ''
              ).replace(/\\s+/g, ' ').trim().slice(0, 180);
              const clean = (value) => String(value || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 180);
              const visibleTextEvidence = (element) => {
                const labelledByText = clean(
                  (element.getAttribute('aria-labelledby') || '')
                    .split(/\\s+/).filter(Boolean)
                    .map((id) => document.getElementById(id))
                    .filter((node) => node && visible(node))
                    .map((node) => node.innerText || node.textContent || '')
                    .join(' ')
                );
                const nestedVisibleText = [...element.querySelectorAll(
                  'span,label,[role="img"],[aria-label],[title],[aria-labelledby]'
                )].filter(visible).slice(0, 40).flatMap((node) => [
                  clean(node.innerText || node.textContent || ''),
                  clean(node.getAttribute('aria-label')),
                  clean(node.getAttribute('title')),
                ]).filter(Boolean);
                return {
                  tag: element.tagName.toLowerCase(),
                  role: element.getAttribute('role') || '',
                  connected: element.isConnected,
                  visible: visible(element),
                  innerText: clean(element.innerText || element.textContent || ''),
                  ariaLabel: clean(element.getAttribute('aria-label')),
                  labelledByText,
                  title: clean(element.getAttribute('title')),
                  nestedVisibleText,
                };
              };
              const controls = [...document.querySelectorAll(
                'button,[role="button"],[role="tab"],[role="radio"],[role="option"],'
                + '[role="combobox"],[role="menu"],[role="menuitem"],select'
              )].filter(visible).map((element) => ({
                tag: element.tagName.toLowerCase(),
                role: element.getAttribute('role') || '',
                label: label(element),
                selected: ['true', 'on'].includes(element.getAttribute('aria-selected')) ||
                          ['true', 'on'].includes(element.getAttribute('aria-pressed')) ||
                          ['true', 'on'].includes(element.getAttribute('aria-checked')) ||
                          ['checked', 'active', 'on'].includes(element.getAttribute('data-state')),
              })).filter((item) => item.label);
              const editables = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter(visible).map((element) => ({
                tag: element.tagName.toLowerCase(),
                role: element.getAttribute('role'),
                label: element.getAttribute('aria-label'),
                placeholder: element.getAttribute('placeholder'),
                name: element.getAttribute('name'),
                contenteditable: element.getAttribute('contenteditable'),
              }));
              const modelName = /\\b(?:nano[\\s_-]+banana(?:[\\s_-]+(?:lite|pro|flash|\\d+(?:\\.\\d+)?))?|imagen(?:[\\s_-]+\\d+(?:\\.\\d+)?)?|gemini(?:[\\s_-]+\\d+(?:\\.\\d+)?)?|veo(?:[\\s_-]+\\d+(?:\\.\\d+)?)?)\\b/i;
              const generationAction = /^(?:(?:start\\s+)?generation|generate|create|submit|send)(?:\\s+(?:image|images))?$/i;
              const editableElements = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter(visible);
              const promptElements = editableElements.filter((element) => {
                const description = [
                  element.getAttribute('aria-label'), element.getAttribute('placeholder'),
                  element.getAttribute('name')
                ].map(clean).join(' ');
                return element.getAttribute('contenteditable') === 'true' ||
                  (/prompt|describe|what do you want to create|imagine|generate/i.test(description) &&
                   !/search|email|phone|password/i.test(description));
              });
              const generationActions = [...document.querySelectorAll(
                'button,[role="button"]'
              )].filter(visible).filter((element) => generationAction.test(label(element)));
              let composerRoot = null;
              if (promptElements.length === 1 && generationActions.length === 1) {
                for (let node = promptElements[0].parentElement;
                     node && node !== document.body;
                     node = node.parentElement) {
                  if (node.contains(generationActions[0])) {
                    composerRoot = node;
                    break;
                  }
                }
              }
              const composerControlSelector = [
                'button', '[role="button"]', '[role="combobox"]', '[role="radio"]',
                '[aria-haspopup]', '[aria-controls]', '[aria-labelledby]',
                '[tabindex]:not([tabindex="-1"])', '[data-state]', '[data-testid*="model" i]'
              ].join(',');
              const composerModelControls = [];
              if (composerRoot) {
                const candidates = [...composerRoot.querySelectorAll(composerControlSelector)];
                for (const textNode of composerRoot.querySelectorAll('span,label,[role="img"]')) {
                  if (!visible(textNode)) continue;
                  const textEvidence = visibleTextEvidence(textNode);
                  const textCombined = [
                    textEvidence.innerText, textEvidence.ariaLabel,
                    textEvidence.labelledByText, textEvidence.title,
                    ...textEvidence.nestedVisibleText
                  ].join(' ');
                  if (!modelName.test(textCombined)) continue;
                  const owner = textNode.closest(composerControlSelector);
                  candidates.push(owner && composerRoot.contains(owner) ? owner : textNode);
                }
                for (const element of [...new Set(candidates)]) {
                  if (!element.isConnected || !visible(element)) continue;
                  const evidence = visibleTextEvidence(element);
                  const combined = [
                    evidence.innerText, evidence.ariaLabel, evidence.labelledByText,
                    evidence.title, ...evidence.nestedVisibleText
                  ].join(' ');
                  if (modelName.test(combined)) composerModelControls.push(evidence);
                }
              }
              const media = [...document.querySelectorAll('img,[role="img"],video,canvas')]
                .filter(visible).map((element) => {
                  const rect = element.getBoundingClientRect();
                  return {
                    kind: element.tagName.toLowerCase(),
                    label: label(element),
                    src: element.currentSrc || element.src || null,
                    width: element.naturalWidth || element.videoWidth || element.width || Math.round(rect.width),
                    height: element.naturalHeight || element.videoHeight || element.height || Math.round(rect.height),
                  };
                }).slice(0, 60);
              const iframes = [...document.querySelectorAll('iframe')].filter(visible).map((element) => ({
                title: element.getAttribute('title'),
                name: element.getAttribute('name'),
                src: element.src || element.getAttribute('src'),
              })).slice(0, 30);
              const diagnosticText = [...document.querySelectorAll(
                'button,[role],[aria-live],p,span,label,h1,h2,h3,h4'
              )].filter(visible).map(label).filter((text) =>
                text && /(nano banana|imagen|gemini|veo|video mode|text to video|frames to video|model|aspect ratio|crop[_\\s-]*\\d+[_:]\\d+|(?:^|\\s)\\d+:\\d+(?:$|\\s)|generate|create)/i.test(text)
              ).slice(0, 120);
              const terminalStateText = [...document.querySelectorAll(
                'h1,h2,h3,[role="alert"]'
              )].filter(visible).map(label).filter(Boolean).slice(0, 30);
              const terminalStates = [];
              if (terminalStateText.some((text) =>
                /^(?:access|permission) denied$|^you (?:do not|don't) have access(?: to this project)?$/i.test(text)
              )) terminalStates.push('access_denied');
              if (terminalStateText.some((text) =>
                /^(?:this )?project (?:is )?(?:unavailable|not found)$/i.test(text)
              )) terminalStates.push('project_unavailable');
              return {
                readyState: document.readyState,
                bodyTextLength: (document.body?.innerText || '').length,
                controls,
                editables,
                composerModelControls,
                media,
                iframes,
                diagnosticText,
                terminalStates,
              };
            }
            """
        )
        try:
            generation_resolution = (
                self._read_active_composer_generation_resolution()
            )
        except Exception:
            generation_resolution = _normalize_composer_generation_resolution(
                {"failure_reason": "dom_unavailable"}
            )
        controls = raw["controls"]
        labels = tuple(dict.fromkeys(_safe_label(item["label"]) for item in controls if item["label"]))
        role_labels = lambda role: tuple(
            dict.fromkeys(
                _safe_label(item["label"])
                for item in controls
                if item["role"] == role and item["label"]
            )
        )
        selected = tuple(
            dict.fromkeys(_safe_label(item["label"]) for item in controls if item["selected"])
        )
        diagnostic_text = tuple(
            dict.fromkeys(_safe_label(item) for item in raw["diagnosticText"] if item)
        )
        generation = tuple(
            label
            for label in labels
            if re.search(r"image|video|model|aspect|ratio|variant|output|generate|create", label, re.I)
        )
        generation_actions = tuple(
            label
            for label in labels
            if re.search(
                r"\b(?:(?:start\s+)?generation|generate|create)(?:\s+(?:image|images))?\b",
                label,
                re.I,
            )
        )
        models = _composer_model_labels(raw.get("composerModelControls") or [])
        model_text = models
        aspects = tuple(
            label
            for label in (*labels, *diagnostic_text)
            if re.search(r"aspect ratio|crop[_\s-]*\d+[_:]\d+|\b\d+:\d+\b", label, re.I)
        )
        downloads = tuple(label for label in labels if re.search(r"download|save image", label, re.I))
        editables = tuple(
            {
                "tag": item.get("tag"),
                "role": _safe_label(item.get("role")) or None,
                "label": _safe_label(item.get("label")) or None,
                "placeholder": _safe_label(item.get("placeholder")) or None,
                "name": _safe_label(item.get("name")) or None,
                "contenteditable": item.get("contenteditable"),
            }
            for item in raw["editables"][:30]
        )
        prompt_candidates = tuple(
            item
            for item in editables
            if item["contenteditable"] == "true"
            or (
                re.search(
                    r"prompt|describe|what do you want to create|imagine|generate",
                    " ".join(
                        str(item.get(key) or "")
                        for key in ("label", "placeholder", "name")
                    ),
                    re.I,
                )
                and not re.search(
                    r"search|email|phone|password",
                    " ".join(
                        str(item.get(key) or "")
                        for key in ("label", "placeholder", "name")
                    ),
                    re.I,
                )
            )
        )
        media = tuple(
            {
                "kind": item["kind"],
                "label": _safe_label(item.get("label")),
                "url": _safe_visible_url(item.get("src"))
                if int(item.get("width") or 0) >= 192 and int(item.get("height") or 0) >= 192
                else None,
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            }
            for item in raw["media"]
        )
        assets = tuple(
            item for item in media if item["kind"] in {"img", "canvas"} and item["width"] >= 192 and item["height"] >= 192
        )
        iframes = tuple(
            {
                "title": _safe_label(item.get("title")) or None,
                "name": _safe_label(item.get("name")) or None,
                "url": _safe_visible_url(item.get("src")),
            }
            for item in raw["iframes"]
        )
        screenshot_path = None
        screenshot_method = None
        screenshot_error = None
        if capture_screenshot:
            screenshot_path, screenshot_method, screenshot_error = (
                self._capture_diagnostic_screenshot()
            )

        url = _safe_visible_url(page.url) or "(redacted)"
        lower_labels = " ".join(labels).lower()
        body_length = int(raw.get("bodyTextLength") or 0)
        sign_in_evidence = bool(
            any(
                re.fullmatch(
                    r"(?:sign|log)\s*in|continue\s+with\s+google",
                    label,
                    re.I,
                )
                for label in labels
            )
            or any(
                re.search(
                    r"email|phone|password|sign\s*in|log\s*in",
                    " ".join(
                        str(item.get(key) or "")
                        for key in ("label", "placeholder", "name")
                    ),
                    re.I,
                )
                for item in editables
            )
        )
        if "accounts.google." in url or (
            self._is_flow_project_url(url)
            and not prompt_candidates
            and sign_in_evidence
        ):
            authentication = "sign_in_required"
        elif "create with google flow" in lower_labels or "try google flow" in lower_labels:
            authentication = "public_landing_page"
        elif self._is_flow_project_url(url) and body_length == 0:
            authentication = "project_dom_empty"
        elif self._is_flow_project_url(url) and editables:
            authentication = "editor_visible"
        else:
            authentication = "authenticated_or_unknown"
        return FlowInspection(
            current_url=url,
            title=page.title(),
            document_ready_state=str(raw.get("readyState") or "unknown"),
            visible_body_text_length=body_length,
            authentication_state=authentication,
            visible_buttons=tuple(
                dict.fromkeys(
                    _safe_label(item["label"])
                    for item in controls
                    if item["role"] == "button" or item["tag"] == "button"
                )
            )[:100],
            editable_controls=editables,
            prompt_control_candidates=prompt_candidates,
            comboboxes=role_labels("combobox")[:40],
            tabs=role_labels("tab")[:40],
            menus=tuple(dict.fromkeys((*role_labels("menu"), *role_labels("menuitem"))))[:60],
            selected_controls=selected[:30],
            generation_controls=generation[:40],
            generation_action_controls=generation_actions[:30],
            model_labels=models[:20],
            model_related_text=model_text[:40],
            aspect_ratio_controls=tuple(dict.fromkeys(aspects))[:40],
            download_controls=downloads[:20],
            rendered_assets=assets,
            media=media,
            iframes=iframes,
            loading_or_error_states=tuple(
                state
                for state in raw.get("terminalStates", [])
                if state in {"access_denied", "project_unavailable"}
            )[:10],
            generation_control_resolution=generation_resolution,
            screenshot_path=screenshot_path,
            screenshot_method=screenshot_method,
            screenshot_error=screenshot_error,
        )

    def print_environment_report(self) -> FlowInspection:
        inspection = self.inspect_environment(capture_screenshot=True)
        # Escaping non-ASCII keeps diagnostics printable in legacy Windows consoles.
        print(json.dumps(inspection.to_dict(), ensure_ascii=True, indent=2))
        return inspection

    def _read_only_exact_window_state(self) -> str:
        """Read Browser-domain windowState without changing focus or window bounds."""
        page = self._require_page()
        cdp = self._page_cdp_session
        temporary_session = cdp is None
        if temporary_session:
            try:
                cdp = page.context.new_cdp_session(page)
            except Exception:
                return "unknown"
        try:
            result = cdp.send("Browser.getWindowForTarget")
            bounds = result.get("bounds") if isinstance(result, dict) else None
            state = bounds.get("windowState") if isinstance(bounds, dict) else None
            return (
                str(state)
                if state in {"normal", "minimized", "maximized", "fullscreen"}
                else "unknown"
            )
        except Exception:
            return "unknown"
        finally:
            if temporary_session:
                try:
                    cdp.detach()
                except Exception:
                    pass

    def _download_layout_dom_snapshot(
        self,
        *,
        candidate_limit: int = _DOWNLOAD_LAYOUT_MAX_CANDIDATES,
        target_candidate_id: str | None = None,
    ) -> dict[str, Any]:
        """Read bounded window/candidate structure without producing browser input."""
        if not 1 <= int(candidate_limit) <= _DOWNLOAD_LAYOUT_MAX_CANDIDATES:
            raise ValueError(
                "Download-layout inspection limit must be 1 to "
                f"{_DOWNLOAD_LAYOUT_MAX_CANDIDATES}."
            )
        if target_candidate_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{8,100}", target_candidate_id
        ):
            raise ValueError("Target candidate data-media-id is invalid.")
        page = self._require_page()
        raw = page.evaluate(
            r"""
            ({limit, targetCandidateId}) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  Number.parseFloat(style.opacity || '1') > 0.01;
              };
              const box = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return null;
                const rect = element.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const labelledBy = (element) => clean(
                (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
                  .map((id) => document.getElementById(id))
                  .filter(Boolean)
                  .map((node) => node.textContent || '')
                  .join(' ')
              );
              const accessibleName = (element) => clean(
                element.getAttribute('aria-label') || labelledBy(element) ||
                element.getAttribute('title') || element.innerText || ''
              );
              const moreOptions = (tile) => [...tile.querySelectorAll(
                'button,[role="button"]'
              )].filter((element) => accessibleName(element) === 'More options');
              const candidateFacts = (tile) => {
                if (!(tile instanceof Element)) return null;
                const images = [...tile.querySelectorAll('img[data-media-id]')];
                const ids = [...new Set(images.map((image) =>
                  image.getAttribute('data-media-id') || ''
                ).filter(Boolean))];
                const controls = moreOptions(tile);
                return {
                  candidate_id: ids.length === 1 ? ids[0] : null,
                  data_media_id_count: images.length,
                  connected: tile.isConnected,
                  visible: visible(tile),
                  bounding_box: box(tile),
                  more_options_count: controls.length,
                  more_options_controls: controls.slice(0, 3).map((control) => ({
                    connected: control.isConnected,
                    visible: visible(control),
                    enabled: !control.matches(':disabled') &&
                      control.getAttribute('aria-disabled') !== 'true',
                    bounding_box: box(control),
                  })),
                };
              };
              const tiles = [...document.querySelectorAll('flow-image-tile')];
              const visibleTiles = tiles.filter(visible);
              let targetCandidate = null;
              if (targetCandidateId) {
                const exactImages = [...document.querySelectorAll('img[data-media-id]')]
                  .filter((image) =>
                    image.getAttribute('data-media-id') === targetCandidateId
                  );
                const targetTile = exactImages.length === 1
                  ? exactImages[0].closest('flow-image-tile')
                  : null;
                targetCandidate = candidateFacts(targetTile);
              }
              const visualViewport = window.visualViewport;
              return {
                outer_width: window.outerWidth,
                outer_height: window.outerHeight,
                inner_width: window.innerWidth,
                inner_height: window.innerHeight,
                viewport_width: visualViewport
                  ? visualViewport.width : document.documentElement.clientWidth,
                viewport_height: visualViewport
                  ? visualViewport.height : document.documentElement.clientHeight,
                devicePixelRatio: window.devicePixelRatio,
                visible_candidate_tile_count: visibleTiles.length,
                candidate_tiles_with_data_media_id_count: tiles.filter((tile) =>
                  Boolean(tile.querySelector('img[data-media-id]'))
                ).length,
                candidates: visibleTiles.slice(0, limit + 1).map(candidateFacts),
                truncated: visibleTiles.length > limit,
                target_candidate: targetCandidate,
              };
            }
            """,
            {"limit": int(candidate_limit), "targetCandidateId": target_candidate_id},
        )
        return _normalize_download_layout_snapshot(raw, int(candidate_limit))

    def inspect_flow_download_layout(
        self,
        *,
        candidate_limit: int = _DOWNLOAD_LAYOUT_MAX_CANDIDATES,
    ) -> dict[str, Any]:
        """Report privacy-bounded layout evidence without changing Flow or Chrome."""
        page = self._require_page()
        identity = _flow_project_identity(page.url)
        snapshot = self._download_layout_dom_snapshot(candidate_limit=candidate_limit)
        browser_window = {
            "window_state": self._read_only_exact_window_state(),
            **snapshot["browser_window"],
        }
        project_id = identity.get("project_id")
        page_identity = {
            **identity,
            "is_project_uuid_url": bool(
                isinstance(project_id, str)
                and _FLOW_PROJECT_UUID_PATTERN.fullmatch(project_id)
            ),
        }
        candidates = snapshot["candidate_summary"]["candidates"]
        return {
            "schema": "nano-banana-flow-download-layout-inspection/v1",
            "inspection_mode": "read_only_dom_and_cdp_window_state",
            "browser_window": browser_window,
            "page": page_identity,
            "candidate_summary": snapshot["candidate_summary"],
            "layout_evidence": {
                "viewport_width": browser_window["viewport_width"],
                "viewport_height": browser_window["viewport_height"],
                "more_options_present_on_visible_candidates": any(
                    candidate["more_options_visible"] for candidate in candidates
                ),
            },
            # No stable non-obfuscated responsive marker is proven yet. Width alone
            # is deliberately not converted into a layout verdict.
            "responsive_layout_signal": None,
        }

    def inspect_flow_model_menu(
        self,
        *,
        control_limit: int = _MODEL_MENU_RECON_MAX_CONTROLS,
    ) -> dict[str, Any]:
        """Inspect one already-open model menu without producing browser input."""
        if not 1 <= int(control_limit) <= _MODEL_MENU_RECON_MAX_CONTROLS:
            raise ValueError(
                "Model-menu inspection limit must be 1 to "
                f"{_MODEL_MENU_RECON_MAX_CONTROLS}."
            )
        page = self._require_page()
        raw = page.evaluate(
            r"""
            (limit) => {
              const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const modelName = /\b(?:nano[\s_-]+banana(?:[\s_-]+(?:lite|pro|flash|\d+(?:\.\d+)?))?|imagen(?:[\s_-]+\d+(?:\.\d+)?)?|gemini(?:[\s_-]+\d+(?:\.\d+)?)?|veo(?:[\s_-]+\d+(?:\.\d+)?)?)\b/i;
              const sensitive = /account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|email|gmail|manage\s+your\s+google|password|cookie|token|session|prompt|what\s+do\s+you\s+want|@|https?:\/\//i;
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  Number.parseFloat(style.opacity || '1') > 0.01;
              };
              const box = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return null;
                const rect = element.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const labelledByText = (element) => clean(
                (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
                  .map((id) => document.getElementById(id))
                  .filter(Boolean)
                  .map((node) => node.textContent || '')
                  .join(' ')
              );
              const visibleText = (element) => clean(element.innerText || '');
              const accessibleName = (element) => clean(
                element.getAttribute('aria-label') || labelledByText(element) ||
                element.getAttribute('title') || visibleText(element)
              );
              const safeModelText = (value) => {
                const text = clean(value);
                return text && text.length <= 120 && modelName.test(text) &&
                  !sensitive.test(text) ? text : null;
              };
              const stableAttributes = (element) => {
                const allowed = new Set([
                  'data-testid', 'data-test-id', 'data-control', 'data-control-name',
                  'data-state', 'data-value', 'data-type', 'data-model'
                ]);
                const result = {};
                for (const attribute of [...element.attributes]) {
                  const name = attribute.name.toLowerCase();
                  const value = clean(attribute.value);
                  if (allowed.has(name) && value && value.length <= 100 &&
                      !sensitive.test(value) && /^[A-Za-z0-9_.:\- ]+$/.test(value)) {
                    result[name] = value;
                  }
                }
                return result;
              };
              const enabled = (element) => !element.matches(':disabled') &&
                element.getAttribute('aria-disabled') !== 'true';
              const selected = (element) =>
                ['true', 'on'].includes(element.getAttribute('aria-selected')) ||
                ['true', 'on'].includes(element.getAttribute('aria-pressed')) ||
                ['true', 'on'].includes(element.getAttribute('aria-checked')) ||
                ['checked', 'active', 'on'].includes(element.getAttribute('data-state')) ||
                (element instanceof HTMLInputElement && element.checked);
              const generationAction = /^(?:(?:start\s+)?generation|generate|create|submit|send)(?:\s+(?:image|images))?$/i;
              const editables = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter(visible);
              const promptElements = editables.filter((element) => {
                const description = clean([
                  element.getAttribute('aria-label'), element.getAttribute('placeholder'),
                  element.getAttribute('name'), element.getAttribute('contenteditable')
                ].join(' '));
                return element.getAttribute('contenteditable') === 'true' ||
                  (/prompt|describe|what do you want to create|imagine|generate/i.test(description) &&
                   !/search|email|phone|password/i.test(description));
              });
              const generationActions = [...document.querySelectorAll(
                'button,[role="button"]'
              )].filter(visible).filter((element) =>
                generationAction.test(accessibleName(element))
              );
              let composerRoot = null;
              if (promptElements.length === 1 && generationActions.length === 1) {
                for (let node = promptElements[0].parentElement;
                     node && node !== document.body;
                     node = node.parentElement) {
                  if (node.contains(generationActions[0])) {
                    composerRoot = node;
                    break;
                  }
                }
              }
              const triggerSelector = [
                'button', '[role="button"]', '[role="combobox"]', '[aria-haspopup]',
                '[data-testid*="model" i]', '[data-test-id*="model" i]'
              ].join(',');
              const triggerSet = new Set();
              if (composerRoot) {
                for (const element of composerRoot.querySelectorAll(triggerSelector)) {
                  const evidence = clean([
                    visibleText(element), accessibleName(element),
                    element.getAttribute('aria-label'), element.getAttribute('title')
                  ].join(' '));
                  if (visible(element) && modelName.test(evidence) && !sensitive.test(evidence)) {
                    triggerSet.add(element);
                  }
                }
                for (const textNode of composerRoot.querySelectorAll('span,label,[role="img"]')) {
                  const evidence = clean([
                    visibleText(textNode), textNode.getAttribute('aria-label'),
                    textNode.getAttribute('title')
                  ].join(' '));
                  if (!visible(textNode) || !modelName.test(evidence) || sensitive.test(evidence)) continue;
                  const owner = textNode.closest(triggerSelector);
                  if (owner && composerRoot.contains(owner) && visible(owner)) triggerSet.add(owner);
                }
              }
              const semanticAncestor = (element) => {
                let owner = element.parentElement && element.parentElement.closest(
                  'form,[role="group"],[role="toolbar"],[role="region"],[role="dialog"]'
                );
                if (!owner || !composerRoot || !composerRoot.contains(owner)) owner = composerRoot;
                if (!owner) return null;
                const ownerName = clean(
                  owner.getAttribute('aria-label') || owner.getAttribute('title') || ''
                );
                return {
                  tag: owner.tagName.toLowerCase(),
                  role: clean(owner.getAttribute('role')).toLowerCase(),
                  accessible_name: safeModelText(ownerName),
                  is_composer_root: owner === composerRoot,
                };
              };
              const namedFacts = (element) => ({
                tag: element.tagName.toLowerCase(),
                role: clean(element.getAttribute('role')).toLowerCase(),
                visible_text: safeModelText(visibleText(element)),
                accessible_name: safeModelText(accessibleName(element)),
                aria_label: safeModelText(element.getAttribute('aria-label')),
                connected: element.isConnected,
                visible: visible(element),
                enabled: enabled(element),
                bounding_box: box(element),
                aria_haspopup: clean(element.getAttribute('aria-haspopup')) || null,
                aria_expanded: clean(element.getAttribute('aria-expanded')).toLowerCase() || null,
                aria_controls: clean(element.getAttribute('aria-controls')) || null,
                aria_owns: clean(element.getAttribute('aria-owns')) || null,
                selected: selected(element),
                aria_selected: clean(element.getAttribute('aria-selected')).toLowerCase() || null,
                aria_checked: clean(element.getAttribute('aria-checked')).toLowerCase() || null,
                checked: element instanceof HTMLInputElement ? element.checked : null,
                stable_attributes: stableAttributes(element),
                semantic_ancestor: semanticAncestor(element),
              });
              const triggers = [...triggerSet].slice(0, limit + 1);
              const uniqueTrigger = triggers.length === 1 ? triggers[0] : null;
              const popupSelector = [
                '[role="menu"]', '[role="listbox"]', '[role="dialog"]', '[role="group"]',
                'mat-menu-panel', '.mat-mdc-menu-panel', '.mat-menu-panel'
              ].join(',');
              const popupRelations = new Map();
              const addPopup = (element, relationship) => {
                if (!(element instanceof Element) || !visible(element) || popupRelations.has(element)) return;
                popupRelations.set(element, relationship);
              };
              if (uniqueTrigger) {
                for (const [attribute, relationship] of [
                  ['aria-controls', 'aria-controls'], ['aria-owns', 'aria-owns']
                ]) {
                  const ids = clean(uniqueTrigger.getAttribute(attribute)).split(/\s+/).filter(Boolean);
                  for (const id of ids) {
                    const controlled = document.getElementById(id);
                    if (!controlled) continue;
                    addPopup(controlled, relationship);
                    for (const descendant of controlled.querySelectorAll(popupSelector)) {
                      addPopup(descendant, `${relationship}-descendant`);
                    }
                  }
                }
                const triggerId = clean(uniqueTrigger.id);
                if (triggerId) {
                  for (const popup of document.querySelectorAll(popupSelector)) {
                    const labelledBy = clean(popup.getAttribute('aria-labelledby'))
                      .split(/\s+/).filter(Boolean);
                    if (labelledBy.includes(triggerId)) {
                      addPopup(popup, 'popup-aria-labelledby');
                    }
                  }
                }
              }
              const popupEntries = [...popupRelations.entries()].slice(0, Math.min(limit + 1, 11));
              const popupFacts = (element, relationship) => ({
                tag: element.tagName.toLowerCase(),
                role: clean(element.getAttribute('role')).toLowerCase(),
                id: clean(element.id) || null,
                connected: element.isConnected,
                visible: visible(element),
                bounding_box: box(element),
                ownership_relationship: relationship,
                stable_attributes: stableAttributes(element),
              });
              const optionSelector = 'button,[role="option"],[role="menuitem"],[role="radio"]';
              const optionFacts = [];
              const seenOptions = new Set();
              for (const [popup, relationship] of popupEntries) {
                for (const option of popup.querySelectorAll(optionSelector)) {
                  if (optionFacts.length >= limit + 1 || seenOptions.has(option) || !visible(option)) continue;
                  const evidence = clean([
                    visibleText(option), accessibleName(option),
                    option.getAttribute('aria-label'), option.getAttribute('title')
                  ].join(' '));
                  if (!modelName.test(evidence) || sensitive.test(evidence)) continue;
                  seenOptions.add(option);
                  optionFacts.push({
                    ...namedFacts(option),
                    popup_id: clean(popup.id) || null,
                    ownership_relationship: relationship,
                  });
                }
              }
              return {
                trigger_candidates: triggers.map(namedFacts),
                popups: popupEntries.map(([element, relationship]) =>
                  popupFacts(element, relationship)
                ),
                model_options: optionFacts,
                truncated: triggers.length > limit || popupEntries.length > 10 ||
                  optionFacts.length > limit,
              };
            }
            """,
            int(control_limit),
        )
        return _normalize_model_menu_recon_snapshot(raw, int(control_limit))

    def inspect_project_ui_controls(self, *, limit: int = 50) -> dict[str, Any]:
        """Read a privacy-bounded inventory of possible project UI controls."""
        if not 1 <= int(limit) <= _PROJECT_UI_RECON_MAX_CANDIDATES:
            raise ValueError(
                f"Project UI inspection limit must be 1 to {_PROJECT_UI_RECON_MAX_CANDIDATES}."
            )
        page = self._require_page()
        identity = _flow_project_identity(page.url)
        raw = page.evaluate(
            r"""
            (limit) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
              };
              const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const safeUiLabel = (value) => {
                const text = clean(value);
                if (!text || text.length > 80 ||
                    /account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|email|gmail|manage\s+your\s+google|@/i.test(text)) {
                  return '';
                }
                return /^(?:\+|plus|add|new|create|projects?|(?:add|new|create)(?:\s+a)?(?:\s+new)?\s+projects?|(?:back|go)\s+to\s+projects?|all\s+projects?|open\s+projects?|projects?\s+(?:menu|navigation|list)|projects?\s+(?:name|title)|name\s+(?:a\s+)?projects?|name\s+your\s+projects?|title|save|cancel|close|done|continue)$/i.test(text) ? text : '';
              };
              const labelledBy = (element) => clean(
                (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
                  .map((id) => document.getElementById(id))
                  .filter(Boolean)
                  .map((node) => node.textContent || '')
                  .join(' ')
              );
              const rawName = (element) => clean(
                element.getAttribute('aria-label') || labelledBy(element) ||
                element.getAttribute('title') || element.innerText || ''
              );
              const privacyContext = (element) => {
                for (let node = element; node && node !== document.body; node = node.parentElement) {
                  const evidence = clean([
                    node.getAttribute('aria-label'), node.getAttribute('title'),
                    node.getAttribute('alt'), node.id,
                  ].join(' '));
                  if (/account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|email|gmail|manage\s+your\s+google|@/i.test(evidence)) {
                    return true;
                  }
                }
                return false;
              };
              const safeHref = (element) => {
                if (!(element instanceof HTMLAnchorElement) || !element.href) return null;
                try {
                  const url = new URL(element.href, location.href);
                  if (url.protocol !== 'https:' || url.hostname !== 'flow.google.com') return null;
                  if (url.pathname !== '/' && !/^\/project\/[^/]+\/?$/.test(url.pathname)) return null;
                  return `${url.origin}${url.pathname}`;
                } catch (_) {
                  return null;
                }
              };
              const region = (element) => {
                if (element.closest('[role="dialog"],dialog,mat-dialog-container')) return 'dialog';
                if (element.closest('header,[role="banner"]')) return 'header';
                if (element.closest('aside')) return 'sidebar';
                if (element.closest('nav,[role="navigation"]')) return 'navigation';
                if (element.closest('[class*="project" i],[data-testid*="project" i]')) return 'project_list';
                return 'other';
              };
              const semanticAncestor = (element) => {
                const owner = element.parentElement && element.parentElement.closest(
                  '[role="dialog"],[role="listitem"],[role="menu"],[role="navigation"],[role="tablist"]'
                );
                if (!owner) return null;
                return {
                  role: clean(owner.getAttribute('role')).toLowerCase(),
                  name: safeUiLabel(rawName(owner)) || null,
                };
              };
              const selector = [
                'button', 'a', '[role="button"]', '[role="link"]', '[role="menuitem"]',
                '[aria-label]', '[title]'
              ].join(',');
              const nodes = [...document.querySelectorAll(selector)].slice(0, 1000);
              const matches = [];
              for (const element of nodes) {
                if (matches.length >= limit + 1 || !visible(element)) continue;
                const tag = element.tagName.toLowerCase();
                const role = clean(element.getAttribute('role')).toLowerCase();
                if (!['button', 'a'].includes(tag) &&
                    !['button', 'link', 'menuitem'].includes(role)) continue;
                if (privacyContext(element)) continue;
                const ariaLabel = clean(element.getAttribute('aria-label'));
                const title = clean(element.getAttribute('title'));
                const visibleText = clean(element.innerText || '');
                const accessibleName = rawName(element);
                const combined = clean([ariaLabel, title, visibleText, accessibleName].join(' '));
                if (/account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|email|gmail|manage\s+your\s+google|@/i.test(combined)) continue;
                const href = safeHref(element);
                const creationLabel = /\b(?:new|create|add|plus)\b|(?:^|\s)\+(?:\s|$)|\bprojects?\b/i.test(combined);
                if (!creationLabel && !href) continue;
                const rect = element.getBoundingClientRect();
                const reasons = [];
                if (creationLabel) reasons.push('creation_label');
                if (/^(?:\+|plus|add)$/i.test(combined)) reasons.push('plus_control');
                if (href) reasons.push('project_navigation');
                matches.push({
                  tag,
                  role,
                  accessible_name: safeUiLabel(accessibleName) || null,
                  aria_label: safeUiLabel(ariaLabel) || null,
                  title: safeUiLabel(title) || null,
                  visible_text: safeUiLabel(visibleText) || null,
                  connected: element.isConnected,
                  visible: true,
                  enabled: !element.matches(':disabled') && element.getAttribute('aria-disabled') !== 'true',
                  bounding_box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                  semantic_ancestor: semanticAncestor(element),
                  region: region(element),
                  href,
                  reasons,
                  sensitive_context: false,
                });
              }
              return {candidates: matches, truncated: matches.length > limit};
            }
            """,
            int(limit),
        )
        raw_candidates = raw.get("candidates", []) if isinstance(raw, dict) else []
        candidates = _bounded_project_ui_controls(raw_candidates, int(limit))
        return {
            "schema": "nano-banana-flow-project-ui-inspection/v1",
            "inspection_mode": "read_only_dom",
            **identity,
            "candidate_limit": int(limit),
            "candidate_count": len(candidates),
            "truncated": bool(raw.get("truncated")) if isinstance(raw, dict) else False,
            "candidates": list(candidates),
        }

    def inspect_current_flow_project(self, *, dialog_limit: int = 25) -> dict[str, Any]:
        """Report current project identity and bounded project-dialog readiness."""
        if not 1 <= int(dialog_limit) <= _PROJECT_UI_RECON_MAX_DIALOG_CONTROLS:
            raise ValueError(
                "Current-project dialog limit must be 1 to "
                f"{_PROJECT_UI_RECON_MAX_DIALOG_CONTROLS}."
            )
        page = self._require_page()
        identity = _flow_project_identity(page.url)
        raw = page.evaluate(
            r"""
            (limit) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
              };
              const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const safeUiLabel = (value) => {
                const text = clean(value);
                if (!text || text.length > 80 ||
                    /account|profile|avatar|sign\s*(?:in|out)|log\s*(?:in|out)|email|gmail|manage\s+your\s+google|@/i.test(text)) {
                  return '';
                }
                return /^(?:\+|plus|add|new|create|projects?|(?:add|new|create)(?:\s+a)?(?:\s+new)?\s+projects?|(?:back|go)\s+to\s+projects?|all\s+projects?|open\s+projects?|projects?\s+(?:menu|navigation|list)|projects?\s+(?:name|title)|name\s+(?:a\s+)?projects?|name\s+your\s+projects?|title|save|cancel|close|done|continue)$/i.test(text) ? text : '';
              };
              const rawName = (element) => clean(
                element.getAttribute('aria-label') || element.getAttribute('title') ||
                element.getAttribute('placeholder') || element.innerText || ''
              );
              const promptSurfaces = [...document.querySelectorAll(
                'textarea,input:not([type="hidden"]),[contenteditable="true"],[role="textbox"]'
              )].filter(visible).filter((element) => {
                const description = clean([
                  element.getAttribute('aria-label'), element.getAttribute('placeholder'),
                  element.getAttribute('name'), element.getAttribute('contenteditable')
                ].join(' '));
                return /prompt|describe|what do you want to create|imagine|generate|contenteditable/i.test(description) &&
                       !/search|email|phone|password|project\s+name|name\s+project/i.test(description);
              });
              const dialogs = [...document.querySelectorAll(
                '[role="dialog"],dialog,mat-dialog-container'
              )].filter(visible).slice(0, 20);
              const namingDialogs = dialogs.filter((dialog) => {
                const headings = [...dialog.querySelectorAll('h1,h2,h3,[role="heading"]')]
                  .filter(visible).slice(0, 5).map(rawName);
                const controls = [...dialog.querySelectorAll(
                  'button,a,input,[role="button"],[role="link"],[role="menuitem"],[role="textbox"]'
                )].filter(visible).slice(0, 30).map(rawName);
                const evidence = clean([
                  dialog.getAttribute('aria-label'), dialog.getAttribute('title'),
                  ...headings, ...controls
                ].join(' '));
                return /\bprojects?\b|\bname\b.*\bprojects?\b|\b(?:new|create)\b/i.test(evidence) &&
                       !/account|profile|email|sign\s*(?:in|out)|@/i.test(evidence);
              });
              const controls = [];
              for (const dialog of namingDialogs) {
                const dialogName = safeUiLabel(rawName(dialog)) || null;
                const elements = [...dialog.querySelectorAll(
                  'button,a,input,[role="button"],[role="link"],[role="menuitem"],[role="textbox"]'
                )].filter(visible).slice(0, 100);
                for (const element of elements) {
                  if (controls.length >= limit + 1) break;
                  const ariaLabel = clean(element.getAttribute('aria-label'));
                  const title = clean(element.getAttribute('title'));
                  const name = rawName(element);
                  const rect = element.getBoundingClientRect();
                  if (/account|profile|avatar|email|gmail|sign\s*(?:in|out)|@/i.test(name)) continue;
                  controls.push({
                    tag: element.tagName.toLowerCase(),
                    role: clean(element.getAttribute('role')).toLowerCase(),
                    accessible_name: safeUiLabel(name) || null,
                    aria_label: safeUiLabel(ariaLabel) || null,
                    title: safeUiLabel(title) || null,
                    visible_text: safeUiLabel(element.innerText || '') || null,
                    connected: element.isConnected,
                    visible: true,
                    enabled: !element.matches(':disabled') && element.getAttribute('aria-disabled') !== 'true',
                    bounding_box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                    semantic_ancestor: {role: 'dialog', name: dialogName},
                    region: 'dialog',
                    href: null,
                    reasons: ['dialog_control'],
                    sensitive_context: false,
                  });
                }
              }
              return {
                ready_state: ['interactive', 'complete'].includes(document.readyState) ? document.readyState : 'other',
                prompt_surface_count: promptSurfaces.length,
                visible_dialog_count: dialogs.length,
                naming_dialog_present: namingDialogs.length > 0,
                modal_controls: controls,
                modal_controls_truncated: controls.length > limit,
              };
            }
            """,
            int(dialog_limit),
        )
        raw = raw if isinstance(raw, dict) else {}
        modal_controls = _bounded_project_ui_controls(
            raw.get("modal_controls", []),
            int(dialog_limit),
        )
        ready_state = str(raw.get("ready_state") or "other")
        prompt_surface_count = max(0, int(raw.get("prompt_surface_count") or 0))
        return {
            "schema": "nano-banana-current-flow-project-inspection/v1",
            "inspection_mode": "read_only_dom",
            **identity,
            "document_ready_state": ready_state,
            "prompt_surface_count": prompt_surface_count,
            "editor_ready": bool(
                identity["is_project_url"]
                and ready_state in {"interactive", "complete"}
                and prompt_surface_count > 0
            ),
            "visible_dialog_count": min(20, max(0, int(raw.get("visible_dialog_count") or 0))),
            "naming_dialog_present": bool(raw.get("naming_dialog_present")),
            "dialog_control_limit": int(dialog_limit),
            "dialog_control_count": len(modal_controls),
            "dialog_controls_truncated": bool(raw.get("modal_controls_truncated")),
            "dialog_controls": list(modal_controls),
        }

    @staticmethod
    def _assert_inspection_ready(inspection: FlowInspection) -> None:
        if inspection.authentication_state == "sign_in_required":
            raise FlowAutomationError(
                "Google sign-in is required. Run `python app.py open-flow` and authenticate manually."
            )
        if inspection.authentication_state == "public_landing_page":
            raise FlowAutomationError(
                "The public Flow landing page is open, not an authenticated editor. "
                "Run `python app.py open-flow` first."
            )
        if inspection.authentication_state == "project_dom_empty":
            raise FlowAutomationError(
                "The attached Flow project tab has an empty visible DOM after stabilization. "
                "Run `python app.py inspect-flow` and review diagnostics/flow_inspect.png."
            )
        if (
            inspection.authentication_state != "editor_visible"
            or inspection.document_ready_state not in {"interactive", "complete"}
            or inspection.visible_body_text_length <= 0
        ):
            raise FlowAutomationError(
                "Image-mode preflight failed because the authenticated Flow editor is not ready."
            )

    def _assert_editor_available(self) -> FlowInspection:
        inspection = self.inspect_environment()
        self._assert_inspection_ready(inspection)
        return inspection

    @staticmethod
    def _inspection_generation_resolution(
        inspection: FlowInspection,
    ) -> dict[str, Any]:
        raw = inspection.generation_control_resolution
        if isinstance(raw, dict) and raw.get("schema") == (
            "nano-banana-composer-generation/v1"
        ):
            return _normalize_composer_generation_resolution(raw)
        # Test/legacy FlowInspection values predate composer ownership. Preserve
        # their former single-control behavior without making production permissive.
        prompt_count = len(inspection.prompt_control_candidates)
        generation_count = len(inspection.generation_action_controls)
        ready = prompt_count == 1 and generation_count == 1
        return _normalize_composer_generation_resolution(
            {
                "prompt_editor_count": prompt_count,
                "global_generation_control_count": generation_count,
                "global_visible_generation_control_count": generation_count,
                "active_composer_count": int(prompt_count == 1 and generation_count > 0),
                "active_composer_generation_control_count": generation_count,
                "structurally_owned_generation_control_count": generation_count,
                "actionable_owned_generation_control_count": generation_count,
                "ignored_generation_control_count": 0,
                "composer_depth": 1 if generation_count else None,
                "composer_semantic": False,
                "structural_generation_control_ready": ready,
                "generation_control_actionable": ready,
                "ready": ready,
                "failure_reason": (
                    "ready"
                    if ready
                    else "prompt_editor_ambiguous"
                    if prompt_count != 1
                    else "owned_generation_control_missing"
                    if generation_count == 0
                    else "owned_generation_control_ambiguous"
                ),
                "owned_control_label": (
                    inspection.generation_action_controls[0] if ready else None
                ),
                "prompt_populated": False,
            }
        )

    def _composer_generation_diagnostics(
        self,
        inspection: FlowInspection | None,
        resolution: dict[str, Any],
        *,
        elapsed_ms: int,
        poll_count: int,
        remount_observed: bool,
        actionability_wait: bool = False,
    ) -> dict[str, Any]:
        observed_url = (
            inspection.current_url
            if inspection is not None
            else _safe_visible_url(getattr(self._require_page(), "url", None))
        )
        identity = _flow_project_identity(observed_url)
        try:
            window = self._safe_production_window_evidence(
                self._production_window_snapshot()
            )
        except Exception:
            window = {
                "window_state": "unknown",
                "viewport": {"width": None, "height": None},
            }
        safe_resolution = _normalize_composer_generation_resolution(resolution)
        safe_resolution["settle_elapsed_ms"] = max(0, int(elapsed_ms))
        safe_resolution["settle_poll_count"] = max(0, int(poll_count))
        safe_resolution["remount_observed"] = bool(remount_observed)
        safe_resolution["post_input_actionability_wait_elapsed_ms"] = (
            max(0, int(elapsed_ms)) if actionability_wait else 0
        )
        return {
            "schema": "nano-banana-composer-generation-settle/v1",
            "current_project_id": identity.get("project_id"),
            "exact_project": (
                _canonical_flow_project_reference(observed_url)
                == _canonical_flow_project_reference(self.flow_url)
            ),
            "window": window,
            "resolution": safe_resolution,
        }

    def _wait_for_active_composer_generation_inspection(
        self,
        initial: FlowInspection,
        *,
        timeout_ms: int = _COMPOSER_GENERATION_SETTLE_TIMEOUT_MS,
        poll_interval_ms: int = _COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
    ) -> FlowInspection:
        """Boundedly reacquire one structural Start owner before prompt input."""
        if not 500 <= int(timeout_ms) <= 5_000:
            raise ValueError("Composer generation settle must be 500-5000 ms.")
        if not 50 <= int(poll_interval_ms) <= 250:
            raise ValueError("Composer generation settle interval must be 50-250 ms.")
        started = time.perf_counter()
        poll_limit = max(1, math.ceil(timeout_ms / poll_interval_ms) + 1)
        latest = initial
        previous_token: int | None = None
        remount_observed = False
        self._composer_generation_observation = None

        initial_resolution = self._inspection_generation_resolution(initial)
        if initial_resolution.get("ready"):
            self._composer_generation_observation = (
                self._composer_generation_diagnostics(
                    initial,
                    initial_resolution,
                    elapsed_ms=0,
                    poll_count=1,
                    remount_observed=False,
                )
            )
            return initial

        page = self._require_page()

        for poll_index in range(poll_limit):
            cancellation_checkpoint("composer_structure_wait")
            resolution = self._inspection_generation_resolution(latest)
            token = resolution.get("composer_token")
            if previous_token is not None and token is not None and token != previous_token:
                remount_observed = True
            if token is not None:
                previous_token = int(token)
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1_000))
            diagnostics = self._composer_generation_diagnostics(
                latest,
                resolution,
                elapsed_ms=elapsed_ms,
                poll_count=poll_index + 1,
                remount_observed=remount_observed,
            )
            self._composer_generation_observation = diagnostics
            if resolution.get("ready"):
                return latest
            if poll_index + 1 >= poll_limit:
                break
            page.wait_for_timeout(int(poll_interval_ms))
            try:
                latest = self.inspect_environment(stabilize=False)
            except Exception:
                remount_observed = True

        raise FlowGenerationPreflightError(
            "the active prompt composer did not expose exactly one structurally "
            "owned generation control within the bounded settle. No generation "
            "was submitted. "
            "diagnostics="
            f"{json.dumps(self._composer_generation_observation, sort_keys=True, separators=(',', ':'))}"
        )

    def _editor_readiness_diagnostics(
        self,
        expected: FlowProjectReference,
        inspection: FlowInspection | None,
        *,
        elapsed_ms: int,
        poll_count: int,
        outcome: str,
        composer_settle_elapsed_ms: int = 0,
        composer_settle_poll_count: int = 0,
        composer_remount_observed: bool = False,
    ) -> dict[str, Any]:
        page = self._require_page()
        observed_url = (
            inspection.current_url
            if inspection is not None
            else (_safe_visible_url(getattr(page, "url", None)) or "(unavailable)")
        )
        identity = _flow_project_identity(observed_url)
        contradictions = (
            self._video_contradictions(inspection) if inspection is not None else ()
        )
        try:
            window = self._safe_production_window_evidence(
                self._production_window_snapshot()
            )
        except Exception:
            window = {
                "window_state": "unknown",
                "viewport": {"width": None, "height": None},
            }
        resolution = (
            self._inspection_generation_resolution(inspection)
            if inspection is not None
            else _normalize_composer_generation_resolution(
                {"failure_reason": "unavailable"}
            )
        )
        prompt_count = resolution["prompt_editor_count"]
        generation_count = resolution["global_generation_control_count"]
        return {
            "schema": "nano-banana-editor-readiness/v1",
            "outcome": outcome,
            "expected_project_id": expected.project_id,
            "current_url": identity.get("current_url"),
            "current_project_id": identity.get("project_id"),
            "exact_project": _canonical_flow_project_reference(observed_url) == expected,
            "document_ready_state": (
                inspection.document_ready_state if inspection is not None else "unavailable"
            ),
            "authentication_state": (
                inspection.authentication_state if inspection is not None else "unavailable"
            ),
            "prompt_editor_count": prompt_count,
            "generation_control_count": generation_count,
            "active_composer_count": resolution["active_composer_count"],
            "active_composer_generation_control_count": resolution[
                "active_composer_generation_control_count"
            ],
            "structurally_owned_generation_control_count": resolution[
                "structurally_owned_generation_control_count"
            ],
            "actionable_owned_generation_control_count": resolution[
                "actionable_owned_generation_control_count"
            ],
            "ignored_generation_control_count": resolution[
                "ignored_generation_control_count"
            ],
            "generation_control_candidates": resolution["candidates"],
            "document_ready": bool(
                inspection is not None
                and inspection.document_ready_state in {"interactive", "complete"}
            ),
            "authenticated_editor": bool(
                inspection is not None
                and inspection.authentication_state == "editor_visible"
            ),
            "prompt_editor_ready": prompt_count == 1,
            "prompt_already_populated": bool(resolution["prompt_populated"]),
            "structural_generation_control_ready": bool(
                resolution["structural_generation_control_ready"]
            ),
            "generation_control_actionable": bool(
                resolution["generation_control_actionable"]
            ),
            # Compatibility alias for existing persisted diagnostics/readers.
            "generation_control_ready": bool(
                resolution["structural_generation_control_ready"]
            ),
            "composer_failure_reason": resolution["failure_reason"],
            "composer_remount_observed": bool(composer_remount_observed),
            "composer_settle_elapsed_ms": max(0, int(composer_settle_elapsed_ms)),
            "composer_settle_poll_count": max(0, int(composer_settle_poll_count)),
            "video_mode_contradiction": bool(contradictions),
            "terminal_states": list(
                inspection.loading_or_error_states if inspection is not None else ()
            )[:10],
            "window": window,
            "elapsed_ms": max(0, int(elapsed_ms)),
            "poll_count": max(0, int(poll_count)),
        }

    def _wait_for_authenticated_image_editor(
        self,
        expected: FlowProjectReference,
        *,
        timeout_seconds: float = _EDITOR_READINESS_TIMEOUT_SECONDS,
        poll_interval_ms: int = _EDITOR_READINESS_POLL_INTERVAL_MS,
    ) -> FlowInspection:
        """Passively reacquire semantic editor readiness after project navigation."""
        expected.validate()
        page = self._require_page()
        timeout_seconds = max(0.0, float(timeout_seconds))
        poll_interval_ms = max(1, int(poll_interval_ms))
        poll_limit = max(1, math.ceil(timeout_seconds * 1_000 / poll_interval_ms))
        started = time.monotonic()
        latest: FlowInspection | None = None
        self._editor_readiness_observation = None
        composer_settle_started: float | None = None
        composer_settle_poll_count = 0
        composer_token: int | None = None
        composer_remount_observed = False

        for poll_index in range(poll_limit):
            cancellation_checkpoint("editor_readiness_wait")
            safe_url = _safe_visible_url(getattr(page, "url", None))
            if safe_url and "accounts.google." in safe_url.casefold():
                raise FlowAutomationError(
                    "Google sign-in is required. Authenticate manually, then retry the "
                    "same saved Flow project."
                )
            if _canonical_flow_project_reference(safe_url) != expected:
                raise FlowAutomationError(
                    f"{FLOW_PROJECT_REOPEN_FAILED}: the browser left saved Flow project "
                    f"{expected.project_id} while its editor was loading; no generation "
                    "or download was attempted."
                )

            try:
                latest = self.inspect_environment(stabilize=False)
            except Exception:
                # Angular may replace the composer between observations. Reacquire the
                # entire semantic inspection on the next bounded poll.
                latest = None

            if latest is not None:
                if _canonical_flow_project_reference(latest.current_url) != expected:
                    raise FlowAutomationError(
                        f"{FLOW_PROJECT_REOPEN_FAILED}: the authenticated editor no "
                        f"longer belongs to saved Flow project {expected.project_id}; "
                        "no generation or download was attempted."
                    )
                if latest.authentication_state in {
                    "sign_in_required",
                    "public_landing_page",
                }:
                    self._assert_inspection_ready(latest)
                if "access_denied" in latest.loading_or_error_states:
                    raise FlowAutomationError(
                        "Flow access to the saved project was denied; no generation or "
                        "download was attempted."
                    )
                if "project_unavailable" in latest.loading_or_error_states:
                    raise FlowAutomationError(
                        "The saved Flow project is unavailable; no generation or download "
                        "was attempted."
                    )
                contradictions = self._video_contradictions(latest)
                if contradictions:
                    raise FlowAutomationError(
                        "Image-mode preflight rejected contradictory selected/current "
                        f"video evidence: {contradictions[0]!r}."
                    )
                base_ready = bool(
                    latest.authentication_state == "editor_visible"
                    and latest.document_ready_state in {"interactive", "complete"}
                    and latest.visible_body_text_length > 0
                )
                resolution = self._inspection_generation_resolution(latest)
                current_token = resolution.get("composer_token")
                if (
                    composer_token is not None
                    and current_token is not None
                    and current_token != composer_token
                ):
                    composer_remount_observed = True
                if current_token is not None:
                    composer_token = int(current_token)
                if base_ready and not resolution[
                    "structural_generation_control_ready"
                ]:
                    if composer_settle_started is None:
                        composer_settle_started = time.monotonic()
                    composer_settle_poll_count += 1
                ready = bool(
                    base_ready
                    and resolution["structural_generation_control_ready"]
                )
                if ready:
                    elapsed_ms = round((time.monotonic() - started) * 1_000)
                    composer_elapsed_ms = (
                        round((time.monotonic() - composer_settle_started) * 1_000)
                        if composer_settle_started is not None
                        else 0
                    )
                    self._editor_readiness_observation = (
                        self._editor_readiness_diagnostics(
                            expected,
                            latest,
                            elapsed_ms=elapsed_ms,
                            poll_count=poll_index + 1,
                            outcome="ready",
                            composer_settle_elapsed_ms=composer_elapsed_ms,
                            composer_settle_poll_count=composer_settle_poll_count,
                            composer_remount_observed=composer_remount_observed,
                        )
                    )
                    return latest
                if (
                    base_ready
                    and composer_settle_poll_count
                    >= math.ceil(
                        _COMPOSER_GENERATION_SETTLE_TIMEOUT_MS
                        / _COMPOSER_GENERATION_SETTLE_INTERVAL_MS
                    )
                ):
                    break

            if poll_index + 1 < poll_limit:
                page.wait_for_timeout(
                    _COMPOSER_GENERATION_SETTLE_INTERVAL_MS
                    if composer_settle_started is not None
                    else poll_interval_ms
                )

        elapsed_ms = round((time.monotonic() - started) * 1_000)
        diagnostics = self._editor_readiness_diagnostics(
            expected,
            latest,
            elapsed_ms=elapsed_ms,
            poll_count=poll_index + 1,
            outcome="timeout",
            composer_settle_elapsed_ms=(
                round((time.monotonic() - composer_settle_started) * 1_000)
                if composer_settle_started is not None
                else 0
            ),
            composer_settle_poll_count=composer_settle_poll_count,
            composer_remount_observed=composer_remount_observed,
        )
        self._editor_readiness_observation = diagnostics
        raise FlowAutomationError(
            "Image-mode preflight failed because the authenticated Flow editor was "
            f"not ready after a bounded {timeout_seconds:g}-second wait. diagnostics="
            f"{json.dumps(diagnostics, sort_keys=True, separators=(',', ':'))}"
        )

    @staticmethod
    def _prompt_control_evidence(candidate: dict[str, Any]) -> str:
        tag = str(candidate.get("tag") or "control")
        if candidate.get("contenteditable") == "true":
            description = f'{tag}[contenteditable="true"]'
        else:
            description = tag
        visible_name = next(
            (
                _clean_text(str(candidate.get(key) or ""))
                for key in ("label", "placeholder", "name")
                if candidate.get(key)
            ),
            "",
        )
        return f"{description} ({visible_name})" if visible_name else description

    @staticmethod
    def _video_contradictions(inspection: FlowInspection) -> tuple[str, ...]:
        selected_video = [
            label
            for label in inspection.selected_controls
            if re.search(r"\bvideo\b|\bveo(?:\s*\d+(?:\.\d+)?)?\b", label, re.I)
        ]
        current_video_model = [
            label
            for label in (*inspection.model_labels, *inspection.model_related_text)
            if re.search(
                r"\bveo(?:\s*\d+(?:\.\d+)?)?\b|video model|text[ _-]*to[ _-]*video|frames?[ _-]*to[ _-]*video",
                label,
                re.I,
            )
        ]
        return tuple(dict.fromkeys((*selected_video, *current_video_model)))

    @staticmethod
    def _visible_selected_models(inspection: FlowInspection) -> tuple[str, ...]:
        """Extract only visible selected/current model-control labels."""
        matched: dict[str, str] = {}
        for label in inspection.model_labels:
            for match in _VISIBLE_MODEL_NAME_PATTERN.finditer(label):
                if not _MODEL_CONTROL_METADATA_SUFFIX_PATTERN.fullmatch(
                    label[match.end() :]
                ):
                    continue
                model = re.sub(r"[_-]+", " ", _clean_text(match.group(0)))
                matched.setdefault(_normalize_model_name(model), model)
        return tuple(matched.values())

    @classmethod
    def _informational_selected_model(
        cls,
        inspection: FlowInspection,
    ) -> str | None:
        """Return one safely detected current model without enforcing its name."""
        observed_models = cls._visible_selected_models(inspection)
        if len(observed_models) == 1:
            return observed_models[0]
        return "unknown" if inspection.model_labels else None

    @staticmethod
    def _matching_aspect_ratios(
        inspection: FlowInspection, configured_aspect_ratio: str
    ) -> tuple[str, ...]:
        configured_parts = re.findall(r"\d+", configured_aspect_ratio)
        if len(configured_parts) != 2:
            return ()
        configured = (int(configured_parts[0]), int(configured_parts[1]))
        matches: dict[tuple[int, int], str] = {}
        ratio_pattern = re.compile(
            r"(?P<raw>(?:crop[_\s-]*)?(?P<left>\d+)[_:\-x](?P<right>\d+))",
            re.I,
        )
        for label in inspection.aspect_ratio_controls:
            for match in ratio_pattern.finditer(label):
                ratio = (int(match.group("left")), int(match.group("right")))
                matches.setdefault(ratio, match.group("raw"))
        if len(matches) != 1 or configured not in matches:
            return ()
        return (matches[configured],)

    def detect_image_mode(
        self,
        settings: GenerationSettings,
        *,
        inspection: FlowInspection | None = None,
    ) -> ModeDetection:
        """Classify image mode from visible DOM evidence without changing the page."""
        settings.validate()
        inspection = inspection or self._assert_editor_available()
        self._assert_inspection_ready(inspection)
        selected_model = self._informational_selected_model(inspection)

        contradictions = self._video_contradictions(inspection)
        if contradictions:
            raise FlowAutomationError(
                "Image-mode preflight rejected contradictory selected/current video evidence: "
                f"{contradictions[0]!r}."
            )

        prompt_candidates = inspection.prompt_control_candidates
        generation_resolution = self._inspection_generation_resolution(inspection)
        if len(prompt_candidates) != 1 or generation_resolution[
            "prompt_editor_count"
        ] != 1:
            diagnostics = self._composer_generation_diagnostics(
                inspection,
                generation_resolution,
                elapsed_ms=0,
                poll_count=1,
                remount_observed=False,
            )
            raise FlowGenerationPreflightError(
                "image-mode preflight could not prove exactly one active prompt editor. "
                "No generation was submitted. diagnostics="
                f"{json.dumps(diagnostics, sort_keys=True, separators=(',', ':'))}"
            )
        if not generation_resolution["structural_generation_control_ready"]:
            diagnostics = self._composer_generation_diagnostics(
                inspection,
                generation_resolution,
                elapsed_ms=0,
                poll_count=1,
                remount_observed=False,
            )
            raise FlowGenerationPreflightError(
                "image-mode preflight could not prove exactly one generation control "
                "structurally owned by the active prompt composer. No generation was "
                "submitted. "
                "diagnostics="
                f"{json.dumps(diagnostics, sort_keys=True, separators=(',', ':'))}"
            )

        explicit_controls = tuple(
            label
            for label in inspection.selected_controls
            if re.fullmatch(r"(?:create\s+)?image(?:\s+generation)?", label, re.I)
        )
        if len(explicit_controls) > 1:
            raise FlowAutomationError(
                "Image-mode preflight found multiple selected Image controls."
            )

        aspects = self._matching_aspect_ratios(inspection, settings.aspect_ratio)
        if len(aspects) != 1:
            observed = tuple(
                dict.fromkeys(
                    match.group("raw")
                    for label in inspection.aspect_ratio_controls
                    for match in re.finditer(
                        r"(?P<raw>(?:crop[_\s-]*)?\d+[_:\-x]\d+)", label, re.I
                    )
                )
            )
            detail = f"; observed {observed}" if observed else ""
            raise FlowAutomationError(
                "Image-mode preflight could not identify one current aspect ratio matching "
                f"configured value {settings.aspect_ratio!r}{detail}."
            )

        current_evidence = {
            "aspect_ratio": aspects[0],
            "prompt_control": self._prompt_control_evidence(prompt_candidates[0]),
            "generation_control": (
                generation_resolution.get("owned_control_label")
                or inspection.generation_action_controls[0]
            ),
        }
        if selected_model is not None:
            current_evidence["model"] = selected_model
        variant_values = tuple(
            dict.fromkeys(
                match.group(0)
                for label in (
                    *inspection.model_labels,
                    *inspection.model_related_text,
                    *inspection.aspect_ratio_controls,
                )
                for match in re.finditer(r"\bx\s*\d+\b", label, re.I)
            )
        )
        if len(variant_values) == 1:
            current_evidence["variants"] = variant_values[0]

        if len(explicit_controls) == 1:
            return ModeDetection(
                mode="image",
                confidence="explicit",
                evidence={"image_control": explicit_controls[0], **current_evidence},
            )

        return ModeDetection(
            mode="image",
            confidence="inferred",
            evidence=current_evidence,
        )

    @staticmethod
    def _selected_state(locator) -> bool:
        return bool(
            locator.evaluate(
                """
                (element) => ['true', 'on'].includes(element.getAttribute('aria-selected')) ||
                  ['true', 'on'].includes(element.getAttribute('aria-pressed')) ||
                  ['true', 'on'].includes(element.getAttribute('aria-checked')) ||
                  ['checked', 'active', 'on'].includes(element.getAttribute('data-state'))
                """
            )
        )

    def _visible_role_matches(self, roles: tuple[str, ...], pattern: re.Pattern[str]):
        page = self._require_page()
        matches = []
        for role in roles:
            for locator in page.get_by_role(role, name=pattern).all():
                if locator.is_visible():
                    matches.append(locator)
        return matches

    def _unique_match(self, matches: list, description: str):
        if len(matches) != 1:
            raise FlowAutomationError(
                f"Expected one visible {description}, found {len(matches)}. "
                "Run `python app.py inspect-flow` and review the current controls."
            )
        return matches[0]

    def ensure_image_mode(self, settings: GenerationSettings) -> ModeDetection:
        """Establish image mode before any prompt is entered or submitted."""
        inspection = self._assert_editor_available()
        inspection = self._wait_for_active_composer_generation_inspection(inspection)
        return self.detect_image_mode(settings, inspection=inspection)

    def _choose_named_setting(self, trigger_pattern: re.Pattern[str], value: str, description: str) -> str:
        value_pattern = re.compile(rf"^{re.escape(value)}$", re.I)
        visible_values = self._visible_role_matches(("option", "menuitem", "radio", "button"), value_pattern)
        selected_values = [locator for locator in visible_values if self._selected_state(locator)]
        if len(selected_values) == 1:
            return value

        triggers = self._visible_role_matches(("button", "combobox"), trigger_pattern)
        trigger = self._unique_match(triggers, f"{description} selector")
        trigger_label = _clean_text(trigger.inner_text() or trigger.get_attribute("aria-label"))
        if re.search(rf"\b{re.escape(value)}\b", trigger_label, re.I):
            return value
        trigger.click()
        choices = self._visible_role_matches(("option", "menuitem", "radio", "button"), value_pattern)
        choice = self._unique_match(choices, f"{description} value {value}")
        choice.click()
        return _clean_text(choice.inner_text() or choice.get_attribute("aria-label")) or value

    def _configure_variants_if_exposed(self, variants: int) -> int | None:
        trigger_pattern = re.compile(r"variant|number of (?:images|outputs)|outputs?", re.I)
        triggers = self._visible_role_matches(("button", "combobox"), trigger_pattern)
        if not triggers:
            return None
        trigger = self._unique_match(triggers, "variant-count selector")
        label = _clean_text(trigger.inner_text() or trigger.get_attribute("aria-label"))
        if re.search(rf"\b{variants}\b", label):
            return variants
        trigger.click()
        value_pattern = re.compile(rf"^{variants}(?:\s+(?:images|outputs|variants))?$", re.I)
        choices = self._visible_role_matches(("option", "menuitem", "radio"), value_pattern)
        choice = self._unique_match(choices, f"variant count {variants}")
        choice.click()
        return variants

    def configure_generation(
        self,
        settings: GenerationSettings,
        *,
        inspection: FlowInspection | None = None,
    ) -> dict[str, Any]:
        settings.validate()
        mode_detection = (
            self.detect_image_mode(settings, inspection=inspection)
            if inspection is not None
            else self.ensure_image_mode(settings)
        )
        visible_variants = mode_detection.evidence.get("variants")
        if not visible_variants or not re.fullmatch(
            rf"x\s*{settings.variants}", visible_variants, re.I
        ):
            observed = visible_variants or "(unreadable)"
            raise FlowAutomationError(
                "Generation preflight requires the visible Flow variant count to match "
                f"x{settings.variants}. Observed: {observed}. Change it manually in Flow "
                "before running this command."
            )
        return {
            "mode": mode_detection.mode,
            "mode_detection": mode_detection.to_dict(),
            "aspect_ratio": mode_detection.evidence["aspect_ratio"],
            "model": mode_detection.evidence.get("model"),
            "requested_variants": settings.variants,
            "configured_variants": settings.variants,
            "variant_note": None,
        }

    @_ack_profiled("_asset_snapshot")
    def _asset_snapshot(self) -> list[dict[str, Any]]:
        page = self._require_page()
        raw = _ack_call("candidate_snapshot_page_evaluate", page.evaluate,
            """
            () => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const path = (element) => {
                const parts = [];
                for (let node = element; node && node !== document.body && parts.length < 6; node = node.parentElement) {
                  const siblings = node.parentElement ? [...node.parentElement.children].indexOf(node) : 0;
                  parts.push(`${node.tagName.toLowerCase()}:${siblings}`);
                }
                return parts.reverse().join('/');
              };
              const assets = [];
              for (const element of document.querySelectorAll('img,[role="img"]')) {
                if (!visible(element)) continue;
                const rect = element.getBoundingClientRect();
                const width = element.naturalWidth || Math.round(rect.width);
                const height = element.naturalHeight || Math.round(rect.height);
                if (width < 256 || height < 256) continue;
                const src = element.tagName === 'IMG' ? element.currentSrc || element.src : null;
                const name = element.getAttribute('aria-label') || element.getAttribute('alt') ||
                             element.getAttribute('title') || '';
                assets.push({
                  kind: 'rendered-image',
                  src,
                  mediaId: element.getAttribute('data-media-id'),
                  name,
                  width,
                  height,
                  path: path(element),
                });
              }
              for (const element of document.querySelectorAll('button,[role="button"],a')) {
                if (!visible(element)) continue;
                const name = (element.getAttribute('aria-label') || element.getAttribute('title') ||
                              element.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!/(download|save image)/i.test(name)) continue;
                assets.push({
                  kind: 'download-control',
                  src: null,
                  mediaId: null,
                  name,
                  width: 0,
                  height: 0,
                  path: path(element),
                });
              }
              return assets;
            }
            """
        )
        result = []
        seen = set()
        for asset in raw:
            fingerprint_source = "|".join(
                str(asset.get(key) or "")
                for key in ("kind", "src", "mediaId", "name", "width", "height", "path")
            )
            fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            result.append({**asset, "fingerprint": fingerprint})
        return result

    @_ack_profiled("discover_candidates")
    def discover_candidates(
        self,
        baseline_fingerprints: set[str] | None = None,
        *,
        baseline_candidate_ids: set[str] | None = None,
        forbidden_candidate_ids: set[str] | frozenset[str] | None = None,
    ) -> tuple[DiscoveredCandidate, ...]:
        baseline_fingerprints = baseline_fingerprints or set()
        excluded_candidate_ids = set(baseline_candidate_ids or ()) | set(
            forbidden_candidate_ids or ()
        )
        new_assets = [
            asset for asset in self._asset_snapshot() if asset["fingerprint"] not in baseline_fingerprints
        ]
        selected_assets = [
            asset
            for asset in new_assets
            if asset["kind"] == "rendered-image"
            and re.fullmatch(
                r"[A-Za-z0-9_-]{8,100}",
                str(asset.get("mediaId") or ""),
            )
            and asset.get("mediaId") not in excluded_candidate_ids
        ]
        deduplicated = []
        seen = set()
        for asset in selected_assets:
            key = asset["mediaId"]
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(asset)
        return tuple(
            DiscoveredCandidate(
                source_kind=asset["kind"],
                candidate_id=(
                    asset.get("mediaId")
                    if re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(asset.get("mediaId") or ""))
                    else None
                ),
                accessible_name=_safe_label(asset.get("name")) or None,
                asset_url=_safe_candidate_asset_url(asset.get("src")),
                signal=(
                    f"visible {asset['width']}x{asset['height']} rendered asset"
                    if asset["kind"] == "rendered-image"
                    else "visible user-facing download control"
                ),
            )
            for asset in deduplicated
        )

    def _candidate_correlation_observation(
        self,
        *,
        baseline_fingerprints: set[str],
        baseline_candidate_ids: set[str],
        forbidden_candidate_ids: set[str] | frozenset[str],
        observed_count: int,
        expected_count: int,
    ) -> dict[str, Any]:
        """Count candidate identity classes only; never persist UUIDs or DOM content."""
        assets = self._asset_snapshot()
        candidate_assets = [
            asset for asset in assets
            if asset["kind"] == "rendered-image"
            and re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(asset.get("mediaId") or ""))
        ]
        post_ids = {asset["mediaId"] for asset in candidate_assets}
        fingerprint_new_ids = {
            asset["mediaId"] for asset in candidate_assets
            if asset["fingerprint"] not in baseline_fingerprints
        }
        owned_ids = set(forbidden_candidate_ids)
        id_counts: dict[str, int] = {}
        for asset in candidate_assets:
            candidate_id = asset["mediaId"]
            id_counts[candidate_id] = id_counts.get(candidate_id, 0) + 1
        result: dict[str, Any] = {
            "scan_scope": "whole_document",
            "ownership_scope": "current_run_only",
            "expected_count": expected_count,
            "observed_count_at_failure": observed_count,
            "pre_start_candidate_uuid_count": len(baseline_candidate_ids),
            "run_owned_uuid_count": len(owned_ids),
            "post_start_visible_candidate_node_count": len(candidate_assets),
            "post_start_visible_candidate_uuid_count": len(post_ids),
            "post_start_baseline_overlap_uuid_count": len(post_ids & baseline_candidate_ids),
            "post_start_owned_overlap_uuid_count": len(post_ids & owned_ids),
            "post_start_genuinely_unowned_uuid_count": len(
                post_ids - baseline_candidate_ids - owned_ids
            ),
            "post_start_fingerprint_new_uuid_count": len(fingerprint_new_ids),
            "post_start_claimable_uuid_count": len(
                fingerprint_new_ids - baseline_candidate_ids - owned_ids
            ),
            "visible_duplicate_uuid_count": sum(count > 1 for count in id_counts.values()),
            "visible_duplicate_extra_node_count": sum(
                count - 1 for count in id_counts.values() if count > 1
            ),
        }
        try:
            raw_dom = self._require_page().evaluate(
                """
                ({ baselineIds, ownedIds }) => {
                  const nodes = document.querySelectorAll('img,[role="img"]');
                  const limit = 5000;
                  const baseline = new Set(baselineIds);
                  const owned = new Set(ownedIds);
                  const attempt = window.__nanoBatchCurrentGenerationAttempt;
                  const ancestry = Array.from({ length: 6 }, () => ({
                    tile: 0, section: 0, article: 0, main: 0,
                    group: 0, other: 0, absent: 0,
                  }));
                  const groups = new Map();
                  const outerGroups = new Map();
                  const groupSummary = (collection) => {
                    const summary = {
                      count: collection.size, largest_candidate_count: 0,
                      mixed_baseline_unowned_count: 0,
                      mixed_owned_unowned_count: 0,
                      containing_progress_ancestor_count: 0,
                      containing_post_start_subtree_count: 0,
                    };
                    for (const group of collection.values()) {
                      summary.largest_candidate_count = Math.max(
                        summary.largest_candidate_count, group.count
                      );
                      if (group.baseline && group.unowned) summary.mixed_baseline_unowned_count++;
                      if (group.owned && group.unowned) summary.mixed_owned_unowned_count++;
                      if (group.progress) summary.containing_progress_ancestor_count++;
                      if (group.added) summary.containing_post_start_subtree_count++;
                    }
                    return summary;
                  };
                  const addToGroup = (collection, container, identity, progress, added) => {
                    if (!container) return;
                    const group = collection.get(container) || {
                      count: 0, baseline: 0, owned: 0, unowned: 0,
                      progress: false, added: false,
                    };
                    group.count++;
                    group[identity]++;
                    group.progress ||= progress;
                    group.added ||= added;
                    collection.set(container, group);
                  };
                  const counts = {
                    scanned_nodes: 0, valid_uuid_nodes: 0, hidden_or_zero_rect_nodes: 0,
                    below_size_gate_nodes: 0, production_visible_gate_nodes: 0,
                    offscreen_visible_gate_nodes: 0, zero_opacity_visible_gate_nodes: 0,
                    pointer_events_enabled_nodes: 0, center_hit_test_owned_nodes: 0,
                    duplicate_uuid_count: 0, duplicate_extra_node_count: 0,
                    truncated: nodes.length > limit,
                  };
                  const structure = {
                    capture_available: Boolean(attempt?.active),
                    progress_regions_seen: attempt?.progressRegionCount || 0,
                    post_start_added_roots_seen: attempt?.addedRootCount || 0,
                    progress_region_tracking_truncated: Boolean(attempt?.progressTruncated),
                    added_root_tracking_truncated: Boolean(attempt?.addedRootTruncated),
                    eligible_baseline_nodes: 0, eligible_owned_nodes: 0,
                    eligible_unowned_nodes: 0, eligible_unowned_offscreen_nodes: 0,
                    eligible_with_tile_nodes: 0, eligible_without_tile_nodes: 0,
                    eligible_post_start_subtree_nodes: 0,
                    eligible_unowned_post_start_subtree_nodes: 0,
                    eligible_progress_ancestor_nodes: 0,
                    eligible_unowned_progress_ancestor_nodes: 0,
                    progress_ancestor_distance: Array(9).fill(0),
                    ancestry_levels: ancestry,
                  };
                  const byId = new Map();
                  for (const element of nodes) {
                    if (counts.scanned_nodes >= limit) break;
                    counts.scanned_nodes += 1;
                    const id = element.getAttribute('data-media-id');
                    if (!/^[A-Za-z0-9_-]{8,100}$/.test(String(id || ''))) continue;
                    counts.valid_uuid_nodes += 1;
                    byId.set(id, (byId.get(id) || 0) + 1);
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    if (style.visibility === 'hidden' || style.display === 'none' ||
                        rect.width <= 0 || rect.height <= 0) {
                      counts.hidden_or_zero_rect_nodes += 1;
                      continue;
                    }
                    const width = element.naturalWidth || Math.round(rect.width);
                    const height = element.naturalHeight || Math.round(rect.height);
                    if (width < 256 || height < 256) {
                      counts.below_size_gate_nodes += 1;
                      continue;
                    }
                    counts.production_visible_gate_nodes += 1;
                    const onScreen = rect.right > 0 && rect.bottom > 0 &&
                      rect.left < innerWidth && rect.top < innerHeight;
                    if (!onScreen) counts.offscreen_visible_gate_nodes += 1;
                    const identity = baseline.has(id) ? 'baseline' :
                      owned.has(id) ? 'owned' : 'unowned';
                    structure[`eligible_${identity}_nodes`]++;
                    if (!onScreen && identity === 'unowned') {
                      structure.eligible_unowned_offscreen_nodes++;
                    }
                    const tile = element.closest('flow-image-tile');
                    if (tile) structure.eligible_with_tile_nodes++;
                    else structure.eligible_without_tile_nodes++;
                    let added = false;
                    let progressDistance = 8;
                    for (let ancestor = element, depth = 0;
                         ancestor && ancestor !== document.body && depth < 8;
                         ancestor = ancestor.parentElement, depth++) {
                      if (attempt?.addedRoots?.has(ancestor)) added = true;
                      if (progressDistance === 8 &&
                          attempt?.progressAncestors?.has(ancestor)) {
                        progressDistance = depth;
                      }
                    }
                    if (added) {
                      structure.eligible_post_start_subtree_nodes++;
                      if (identity === 'unowned') {
                        structure.eligible_unowned_post_start_subtree_nodes++;
                      }
                    }
                    structure.progress_ancestor_distance[progressDistance]++;
                    if (progressDistance < 8) {
                      structure.eligible_progress_ancestor_nodes++;
                      if (identity === 'unowned') {
                        structure.eligible_unowned_progress_ancestor_nodes++;
                      }
                    }
                    let ancestor = element;
                    for (const level of ancestry) {
                      const category = !ancestor ? 'absent' :
                        ancestor.localName === 'flow-image-tile' ? 'tile' :
                        ancestor.localName === 'section' ? 'section' :
                        ancestor.localName === 'article' ? 'article' :
                        ancestor.localName === 'main' ? 'main' :
                        ancestor.getAttribute('role') === 'group' ? 'group' : 'other';
                      level[category]++;
                      ancestor = ancestor?.parentElement;
                    }
                    const groupRoot = (tile || element).parentElement;
                    addToGroup(groups, groupRoot, identity, progressDistance < 8, added);
                    addToGroup(
                      outerGroups, groupRoot?.parentElement, identity,
                      progressDistance < 8, added
                    );
                    if (Number(style.opacity) === 0) counts.zero_opacity_visible_gate_nodes += 1;
                    if (style.pointerEvents !== 'none') counts.pointer_events_enabled_nodes += 1;
                    if (onScreen) {
                      const top = document.elementFromPoint(
                        rect.left + rect.width / 2, rect.top + rect.height / 2
                      );
                      if (top && (top === element || element.contains(top))) {
                        counts.center_hit_test_owned_nodes += 1;
                      }
                    }
                  }
                  for (const count of byId.values()) {
                    if (count > 1) {
                      counts.duplicate_uuid_count += 1;
                      counts.duplicate_extra_node_count += count - 1;
                    }
                  }
                  structure.tile_parent_groups = groupSummary(groups);
                  structure.outer_container_groups = groupSummary(outerGroups);
                  counts.structure = structure;
                  return counts;
                }
                """,
                {
                    "baselineIds": sorted(baseline_candidate_ids),
                    "ownedIds": sorted(owned_ids),
                },
            )
            result["raw_dom"] = raw_dom if isinstance(raw_dom, dict) else {"capture": "unavailable"}
        except Exception:
            result["raw_dom"] = {"capture": "unavailable"}
        return result

    @_ack_profiled("_prompt_input")
    def _prompt_input(self):
        page = self._require_page()
        candidates = []
        for locator in _ack_call("prompt_textboxes_all", page.get_by_role("textbox").all):
            if not _ack_call("prompt_is_visible", locator.is_visible) or not _ack_call("prompt_is_enabled", locator.is_enabled):
                continue
            label = " ".join(
                filter(
                    None,
                    [
                        _ack_call("prompt_get_attribute", locator.get_attribute, "aria-label"),
                        _ack_call("prompt_get_attribute", locator.get_attribute, "placeholder"),
                        _ack_call("prompt_get_attribute", locator.get_attribute, "name"),
                    ],
                )
            )
            if re.search(r"search|email|phone|password", label, re.I):
                continue
            if re.search(r"prompt|describe|imagine|create|generate|idea", label, re.I):
                candidates.append(locator)
        if not candidates:
            candidates = [
                locator
                for locator in _ack_call("prompt_fallback_all", page.locator('textarea,[contenteditable="true"]').all)
                if _ack_call("prompt_is_visible", locator.is_visible) and _ack_call("prompt_is_enabled", locator.is_enabled)
            ]
        return self._unique_match(candidates, "prompt input")

    @staticmethod
    def _prompt_control_text(control) -> str:
        value = control.evaluate(
            r"""
            (element) => {
              if ("value" in element) return String(element.value || "");
              return String(element.textContent || "");
            }
            """,
            timeout=1_000,
        )
        return str(value)

    @staticmethod
    @_ack_profiled("_prompt_control_post_start_state")
    def _prompt_control_post_start_state(
        control,
        intended_prompt: str,
    ) -> dict[str, bool]:
        """Read only prompt transition booleans; never return prompt content."""
        raw = _ack_call("prompt_state_evaluate", control.evaluate,
            r"""
            (element, intendedPrompt) => {
              const value = "value" in element
                ? String(element.value || "")
                : String(element.textContent || "");
              const ariaDisabled = String(
                element.getAttribute("aria-disabled") || ""
              ).toLowerCase() === "true";
              return {
                connected: element.isConnected,
                changed: value !== intendedPrompt,
                cleared: value.length === 0,
                disabled: Boolean(element.disabled) || ariaDisabled,
              };
            }
            """,
            intended_prompt,
            timeout=1_000,
        )
        if not isinstance(raw, dict):
            raise TypeError("Prompt transition state was unavailable.")
        return {
            "connected": bool(raw.get("connected")),
            "changed": bool(raw.get("changed")),
            "cleared": bool(raw.get("cleared")),
            "disabled": bool(raw.get("disabled")),
        }

    @staticmethod
    def _post_start_button_state(
        buttons: list[Any],
        facts: dict[str, Any],
        *,
        live: dict[str, Any] | None = None,
    ) -> dict[str, bool | None]:
        """Normalize already-read composer facts without another browser query."""
        active_count = int(facts.get("active_composer_generation_control_count") or 0)
        actionable_count = int(
            facts.get("actionable_owned_generation_control_count") or 0
        )
        structural_count = int(
            facts.get(
                "structurally_owned_generation_control_count",
                facts.get("fresh_structurally_owned_locator_count", len(buttons)),
            )
            or 0
        )
        candidates = facts.get("candidates")
        owned = (
            [
                item
                for item in candidates
                if isinstance(item, dict) and item.get("relationship") == "descendant"
            ]
            if isinstance(candidates, list)
            else []
        )
        visible: bool | None
        enabled: bool | None
        if owned:
            visible_owned = [item for item in owned if item.get("visible") is True]
            visible = bool(visible_owned)
            enabled = (
                any(item.get("enabled") is True for item in visible_owned)
                if visible_owned
                else None
            )
        elif active_count == 0:
            visible = False
            enabled = None
        else:
            visible = structural_count > 0 or bool(buttons)
            enabled = True if actionable_count > 0 or bool(buttons) else None
        actionable = actionable_count > 0 and bool(buttons)
        if live is not None:
            visible = bool(live.get("visible"))
            enabled = bool(live.get("enabled"))
            actionable = bool(
                live.get("connected")
                and visible
                and enabled
                and live.get("pointer_events") != "none"
            )
        return {
            "start_button_visible": visible,
            "start_button_enabled": enabled,
            "start_button_actionable": actionable,
            "start_button_disappeared": active_count == 0,
        }

    @_ack_profiled("_current_generation_submit_buttons")
    def _current_generation_submit_buttons(
        self,
    ) -> tuple[Any | None, list[Any], dict[str, Any]]:
        """Freshly resolve structural and actionable Start state for one composer."""
        resolution = self._read_active_composer_generation_resolution()
        facts: dict[str, Any] = {
            **resolution,
            "semantic_match_count": resolution[
                "global_generation_control_count"
            ],
            "connected_match_count": sum(
                int(item["connected"]) for item in resolution["candidates"]
            ),
            "visible_match_count": resolution[
                "global_visible_generation_control_count"
            ],
            "fresh_owned_locator_count": 0,
            "fresh_structurally_owned_locator_count": 0,
            "fresh_actionable_owned_locator_count": 0,
            "dom_changed_during_reacquisition": False,
        }
        depth = resolution.get("composer_depth")
        if resolution["prompt_editor_count"] != 1 or depth is None:
            return None, [], facts
        try:
            prompt_input = self._prompt_input()
        except Exception:
            facts["dom_changed_during_reacquisition"] = True
            return None, [], facts

        composer = prompt_input
        for _ in range(int(depth)):
            composer = composer.locator("xpath=..")
        matches = _ack_call("composer_buttons_all", composer.get_by_role(
            "button", name=_GENERATION_SUBMIT_PATTERN,
        ).all)
        structural: list[Any] = []
        actionable: list[Any] = []
        facts["fresh_owned_locator_count"] = len(matches)
        for control in matches:
            try:
                current = self._live_control_facts(control)
            except Exception:
                continue
            box = current.get("bounding_box") or {}
            structurally_ready = bool(
                current.get("connected")
                and current.get("visible")
                and float(box.get("width") or 0) > 0
                and float(box.get("height") or 0) > 0
            )
            if structurally_ready:
                structural.append(control)
            if (
                structurally_ready
                and current.get("enabled")
                and current.get("pointer_events") != "none"
            ):
                actionable.append(control)
        facts["fresh_structurally_owned_locator_count"] = len(structural)
        facts["fresh_actionable_owned_locator_count"] = len(actionable)
        if (
            len(structural)
            != resolution["structurally_owned_generation_control_count"]
            or len(actionable)
            != resolution["actionable_owned_generation_control_count"]
        ):
            facts["dom_changed_during_reacquisition"] = True
            return prompt_input, [], facts
        if not resolution["structural_generation_control_ready"]:
            return prompt_input, [], facts
        return prompt_input, actionable, facts

    def _wait_for_owned_generation_control_structure(
        self,
        *,
        timeout_ms: int = _COMPOSER_GENERATION_SETTLE_TIMEOUT_MS,
    ) -> tuple[Any, dict[str, Any]]:
        """Prove prompt/composer/Start structure without requiring Start actionability."""
        if not 500 <= timeout_ms <= 5_000:
            raise ValueError(
                "Generation structural settle wait must be between 500 and 5000 ms."
            )
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        started = time.perf_counter()
        last_facts: dict[str, Any] = {}
        prior_token: int | None = None
        remount_observed = False
        poll_count = 0
        while time.monotonic() < deadline:
            cancellation_checkpoint("composer_structure_wait")
            poll_count += 1
            prompt_input, _actionable, last_facts = (
                self._current_generation_submit_buttons()
            )
            token = last_facts.get("composer_token")
            if prior_token is not None and token is not None and token != prior_token:
                remount_observed = True
            if token is not None:
                prior_token = int(token)
            if (
                prompt_input is not None
                and last_facts.get("structural_generation_control_ready")
                and not last_facts.get("dom_changed_during_reacquisition")
            ):
                self._composer_generation_observation = (
                    self._composer_generation_diagnostics(
                        None,
                        last_facts,
                        elapsed_ms=max(
                            0,
                            round((time.perf_counter() - started) * 1_000),
                        ),
                        poll_count=poll_count,
                        remount_observed=remount_observed,
                    )
                )
                return prompt_input, last_facts
            page.wait_for_timeout(_COMPOSER_GENERATION_SETTLE_INTERVAL_MS)

        self._composer_generation_observation = self._composer_generation_diagnostics(
            None,
            last_facts,
            elapsed_ms=max(0, round((time.perf_counter() - started) * 1_000)),
            poll_count=poll_count,
            remount_observed=remount_observed,
        )
        raise FlowGenerationPreflightError(
            "the current prompt editor and its structurally owned Start control "
            f"could not be proven within {timeout_ms}ms. No generation was "
            "submitted. diagnostics="
            f"{json.dumps(self._composer_generation_observation, sort_keys=True, separators=(',', ':'))}"
        )

    def _inspect_generation_submit_button(
        self,
        control,
        *,
        sample_count: int = _GENERATION_SUBMIT_GEOMETRY_SAMPLE_COUNT,
        sample_interval_ms: int = _GENERATION_SUBMIT_GEOMETRY_SAMPLE_INTERVAL_MS,
    ) -> dict[str, Any]:
        """Collect bounded structural and hit-test facts for Start generation."""
        if not 2 <= sample_count <= 8:
            raise ValueError("Generation submit inspection requires 2 to 8 samples.")
        if not 50 <= sample_interval_ms <= 250:
            raise ValueError(
                "Generation submit inspection interval must be 50 to 250 ms."
            )
        raw = control.evaluate(
            r"""
            async (element, args) => {
              const toBox = (rect) => ({
                x: rect.x, y: rect.y, width: rect.width, height: rect.height,
              });
              const initial = element.getBoundingClientRect();
              const center = {
                x: initial.left + initial.width / 2,
                y: initial.top + initial.height / 2,
              };
              const top = document.elementFromPoint(center.x, center.y);
              const style = getComputedStyle(element);
              const result = {
                tag: element.tagName.toLowerCase(),
                role: element.getAttribute("role") ||
                      (element.tagName.toLowerCase() === "button" ? "button" : null),
                aria_label: element.getAttribute("aria-label"),
                disabled: Boolean(element.disabled),
                aria_disabled: element.getAttribute("aria-disabled"),
                pointer_events: style.pointerEvents,
                connected: element.isConnected,
                visible: element.isConnected && initial.width > 0 && initial.height > 0 &&
                         style.visibility !== "hidden" && style.display !== "none",
                bounding_box: toBox(initial),
                center_top_belongs_to_control: Boolean(
                  top && (top === element || element.contains(top))
                ),
                bounding_box_samples: [],
              };
              for (let index = 0; index < args.sampleCount; index += 1) {
                result.bounding_box_samples.push(
                  toBox(element.getBoundingClientRect())
                );
                if (index + 1 < args.sampleCount) {
                  await new Promise((resolve) =>
                    setTimeout(resolve, args.sampleIntervalMs)
                  );
                }
              }
              result.connected = element.isConnected;
              return result;
            }
            """,
            {"sampleCount": sample_count, "sampleIntervalMs": sample_interval_ms},
            timeout=_GENERATION_SUBMIT_GEOMETRY_TIMEOUT_MS,
        )
        return _normalize_download_control_snapshot(raw)

    def _current_generation_submit_hit(
        self,
        control,
        prior_box: dict[str, float],
        *,
        tolerance: float = 1.0,
    ) -> dict[str, Any]:
        diagnostic_started = time.perf_counter() if self.prestart_timing_debug else None
        raw = control.evaluate(
            r"""
            (element, diagnosticPreDown) => {
              const rect = element.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const top = document.elementFromPoint(x, y);
              const style = getComputedStyle(element);
              const box = {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              const browserNow = performance.now();
              if (diagnosticPreDown) {
                const state = window.__nanoBatchCurrentGenerationAttempt;
                if (state) state.firstMutationAtMs = null;
              }
              return {
                connected: element.isConnected,
                visible: element.isConnected && rect.width > 0 && rect.height > 0 &&
                         style.visibility !== "hidden" && style.display !== "none",
                disabled: Boolean(element.disabled),
                aria_disabled: element.getAttribute("aria-disabled"),
                pointer_events: style.pointerEvents,
                center_top_belongs_to_control: Boolean(
                  top && (top === element || element.contains(top))
                ),
                bounding_box: box,
                bounding_box_samples: [box, box],
                browser_now_ms: browserNow,
              };
            }
            """,
            self.prestart_timing_debug and not self._generation_interrupt_checkpoint.get("start_mouse_down_sent"),
            timeout=1_000,
        )
        if diagnostic_started is not None and isinstance(raw, dict):
            ended = time.perf_counter()
            browser_now = _latency_number(raw.get("browser_now_ms"))
            if browser_now is not None:
                self._last_submit_browser_clock_sample = {
                    "browser_now_ms": browser_now,
                    "python_midpoint": (diagnostic_started + ended) / 2,
                    "uncertainty_ms": max(0, (ended - diagnostic_started) * 1_000),
                }
        snapshot = _normalize_download_control_snapshot(raw)
        current = _stable_generation_submit_box(snapshot, tolerance=tolerance)
        if any(
            abs(current[key] - prior_box[key]) > tolerance
            for key in ("x", "y", "width", "height")
        ):
            raise FlowAutomationError(
                "GENERATION_SUBMIT_BBOX_UNSTABLE: box changed between stability "
                "check and input."
            )
        return current

    def _wait_for_generation_submit_button(
        self,
        *,
        intended_prompt: str | None = None,
        timeout_ms: int = _COMPOSER_GENERATION_SETTLE_TIMEOUT_MS,
    ) -> tuple[Any, Any, dict[str, Any], dict[str, float]]:
        """After prompt entry, reacquire one owned Start until it is actionable."""
        if not 500 <= timeout_ms <= 5_000:
            raise ValueError(
                "Generation submit settle wait must be between 500 and 5000 ms."
            )
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        started = time.perf_counter()
        last_error: Exception | None = None
        last_facts: dict[str, Any] = {}
        prior_token: int | None = None
        remount_observed = False
        poll_count = 0
        prompt_verified_poll: int | None = None
        composer_reacquired_poll: int | None = None
        start_control_reacquired_poll: int | None = None
        while time.monotonic() < deadline:
            cancellation_checkpoint("composer_actionability_wait")
            poll_count += 1
            prompt_input, buttons, last_facts = (
                self._current_generation_submit_buttons()
            )
            token = last_facts.get("composer_token")
            if prior_token is not None and token is not None and token != prior_token:
                remount_observed = True
            if token is not None:
                prior_token = int(token)
            if (
                prompt_input is not None
                and not last_facts.get("dom_changed_during_reacquisition")
            ):
                if composer_reacquired_poll is None:
                    composer_reacquired_poll = poll_count
                self._prestart_mark("composer_reacquired_ms")
            if (
                last_facts.get("structural_generation_control_ready")
                and last_facts.get("fresh_structurally_owned_locator_count") == 1
                and not last_facts.get("dom_changed_during_reacquisition")
            ):
                if start_control_reacquired_poll is None:
                    start_control_reacquired_poll = poll_count
                self._prestart_mark("start_control_reacquired_ms")
            prompt_verified = False
            try:
                if prompt_input is None:
                    raise FlowAutomationError(
                        "GENERATION_PROMPT_UNAVAILABLE: active prompt editor remounted."
                    )
                if (
                    intended_prompt is not None
                    and self._prompt_control_text(prompt_input) != intended_prompt
                ):
                    raise FlowAutomationError(
                        "GENERATION_PROMPT_MISMATCH: the freshly reacquired active "
                        "composer does not contain the intended prompt."
                    )
                prompt_verified = True
                if prompt_verified_poll is None:
                    prompt_verified_poll = poll_count
                    self._prestart_mark("prompt_verified_ms")
            except Exception as exc:
                last_error = exc
            if prompt_verified and len(buttons) == 1:
                button = buttons[0]
                try:
                    snapshot = self._inspect_generation_submit_button(button)
                    stable_box = _stable_generation_submit_box(snapshot)
                    current_box = self._current_generation_submit_hit(
                        button,
                        stable_box,
                    )
                    self._composer_generation_observation = (
                        self._composer_generation_diagnostics(
                            None,
                            last_facts,
                            elapsed_ms=max(
                                0,
                                round((time.perf_counter() - started) * 1_000),
                            ),
                            poll_count=poll_count,
                            remount_observed=remount_observed,
                            actionability_wait=True,
                        )
                    )
                    self._prestart_mark("start_actionable_ms")
                    self._prestart_polling(
                        "post_fill_composer_actionability",
                        poll_count=poll_count,
                        poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
                        timeout_ms=timeout_ms,
                        success_immediate=poll_count == 1,
                        final_reason="stable_owned_start_actionable",
                        remount_observed=remount_observed,
                    )
                    self._prestart_polling(
                        "prompt_verification",
                        poll_count=prompt_verified_poll or poll_count,
                        poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
                        timeout_ms=timeout_ms,
                        success_immediate=prompt_verified_poll == 1,
                        final_reason="exact_prompt_verified",
                        remount_observed=remount_observed,
                    )
                    self._prestart_polling(
                        "composer_reacquisition",
                        poll_count=composer_reacquired_poll or poll_count,
                        poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
                        timeout_ms=timeout_ms,
                        success_immediate=composer_reacquired_poll == 1,
                        final_reason="fresh_composer_reacquired",
                        remount_observed=remount_observed,
                    )
                    self._prestart_polling(
                        "owned_start_reacquisition",
                        poll_count=start_control_reacquired_poll or poll_count,
                        poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
                        timeout_ms=timeout_ms,
                        success_immediate=start_control_reacquired_poll == 1,
                        final_reason="fresh_owned_start_reacquired",
                        remount_observed=remount_observed,
                    )
                    self._prestart_polling(
                        "angular_remount_settle",
                        poll_count=poll_count,
                        poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
                        timeout_ms=timeout_ms,
                        success_immediate=poll_count == 1,
                        final_reason=(
                            "fresh_dom_reacquired_after_remount"
                            if remount_observed
                            else "fresh_dom_stable"
                        ),
                        remount_observed=remount_observed,
                    )
                    self._prestart_polling(
                        "start_geometry_stability",
                        poll_count=_GENERATION_SUBMIT_GEOMETRY_SAMPLE_COUNT,
                        poll_interval_ms=_GENERATION_SUBMIT_GEOMETRY_SAMPLE_INTERVAL_MS,
                        timeout_ms=_GENERATION_SUBMIT_GEOMETRY_TIMEOUT_MS,
                        success_immediate=(
                            _GENERATION_SUBMIT_GEOMETRY_SAMPLE_COUNT == 1
                        ),
                        final_reason="stable_unobscured_geometry",
                    )
                    return prompt_input, button, snapshot, current_box
                except Exception as exc:
                    last_error = exc
            page.wait_for_timeout(_COMPOSER_GENERATION_SETTLE_INTERVAL_MS)
        self._prestart_polling(
            "post_fill_composer_actionability",
            poll_count=poll_count,
            poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=False,
            final_reason="actionability_timeout",
            remount_observed=remount_observed,
        )
        self._prestart_polling(
            "prompt_verification",
            poll_count=prompt_verified_poll or poll_count,
            poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=prompt_verified_poll == 1,
            final_reason=(
                "exact_prompt_verified"
                if prompt_verified_poll is not None
                else "prompt_not_verified_before_timeout"
            ),
            remount_observed=remount_observed,
        )
        self._prestart_polling(
            "composer_reacquisition",
            poll_count=composer_reacquired_poll or poll_count,
            poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=False,
            final_reason=(
                "fresh_composer_reacquired"
                if composer_reacquired_poll is not None
                else "composer_not_reacquired_before_timeout"
            ),
            remount_observed=remount_observed,
        )
        self._prestart_polling(
            "owned_start_reacquisition",
            poll_count=start_control_reacquired_poll or poll_count,
            poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=False,
            final_reason=(
                "fresh_owned_start_reacquired"
                if start_control_reacquired_poll is not None
                else "owned_start_not_reacquired_before_timeout"
            ),
            remount_observed=remount_observed,
        )
        self._prestart_polling(
            "angular_remount_settle",
            poll_count=poll_count,
            poll_interval_ms=_COMPOSER_GENERATION_SETTLE_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=False,
            final_reason="fresh_dom_not_stable_before_timeout",
            remount_observed=remount_observed,
        )
        self._composer_generation_observation = self._composer_generation_diagnostics(
            None,
            last_facts,
            elapsed_ms=max(0, round((time.perf_counter() - started) * 1_000)),
            poll_count=poll_count,
            remount_observed=remount_observed,
            actionability_wait=True,
        )
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        raise FlowGenerationPreflightError(
            "the active prompt composer did not expose one stable, unobscured Start "
            f"control within {timeout_ms}ms{detail}. No generation was submitted. "
            "diagnostics="
            f"{json.dumps(self._composer_generation_observation, sort_keys=True, separators=(',', ':'))}"
        )

    def _activate_generation_submit_button(
        self,
        control,
        stable_box: dict[str, float],
    ) -> dict[str, Any]:
        """Dispatch exactly one trusted mouse down/up activation."""
        page = self._require_page()
        self._set_generation_interrupt_checkpoint("pre_start_activation")
        self._capture_latency_counts("pre_click")
        self._prestart_mark("final_prestart_validation_start_ms")
        try:
            current = self._current_generation_submit_hit(control, stable_box)
            x = float(current["x"] + current["width"] / 2)
            y = float(current["y"] + current["height"] / 2)
            self._prestart_mark("mouse_position_start_ms")
            page.mouse.move(x, y)
            self._prestart_mark("mouse_position_complete_ms")
            down_box = self._current_generation_submit_hit(control, current)
            self._prestart_mark("final_prestart_validation_complete_ms")
        except FlowGenerationPreflightError:
            cancellation_checkpoint("pre_start_activation")
            raise
        except Exception as exc:
            cancellation_checkpoint("pre_start_activation")
            raise FlowGenerationPreflightError(
                "Start could not be proven actionable immediately before mouse "
                "activation. No generation was submitted."
            ) from exc
        # Crossing this boundary is intentionally conservative: an interrupt
        # raised by mouse.down itself cannot prove whether Playwright dispatched
        # the input, so it must retain submitted/ambiguous retry protection.
        self._set_generation_interrupt_checkpoint(
            "start_mouse_down",
            start_mouse_down_sent=True,
        )
        self._prestart_mark("start_mouse_down_ms")
        self._begin_post_start_ack_timing(origin_source="mouse_down_boundary")
        page.mouse.down(button="left")
        try:
            up_box = self._current_generation_submit_hit(control, down_box)
        finally:
            page.mouse.up(button="left")
            self._prestart_mark("start_mouse_up_ms")
            if self._post_start_ack_timing is not None:
                self._post_start_ack_timing.mark_once("start_mouse_up_ms")
        return {
            "input": "mouse_move_down_up",
            "activation_count": 1,
            "center": {"x": x, "y": y},
            "box_before_up": up_box,
        }

    def _begin_current_generation_attempt_observation(self) -> None:
        """Locate local messages; qualify their failure evidence at the Start boundary."""
        page = self._require_page()
        self._current_generation_failure_poll_observation = None
        page.evaluate(
            r"""
            (diagnosticEnabled = false) => {
              const key = '__nanoBatchCurrentGenerationAttempt';
              const prior = window[key];
              if (prior?.observer) prior.observer.disconnect();
              delete window[key];

              // Active only inside an opt-in observer read, never mutation callbacks.
              let readTiming = null;
              const readStep = (name, work) => readTiming ? readTiming.run(name, work) : work();
              const visible = (element) => {
                if (readTiming) {
                  const style = readStep('style_read', () => getComputedStyle(element));
                  const rect = readStep('geometry_read', () => element.getBoundingClientRect());
                  return readStep('visibility_test', () =>
                    style.visibility !== 'hidden' && style.display !== 'none' &&
                    rect.width > 0 && rect.height > 0);
                }
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const normalizedTexts = (element) => {
                const values = readTiming ? [
                  readStep('aria_label_read', () => element.getAttribute('aria-label')),
                  readStep('inner_text_read', () => element.innerText),
                  readStep('text_content_read', () => element.textContent),
                ] : [
                  element.getAttribute('aria-label'),
                  element.innerText,
                  element.textContent,
                ];
                const unique = [];
                for (const [index, value] of values.entries()) {
                  const text = readTiming ? readStep('normalization', () =>
                    String(value || '').replace(/\s+/g, ' ').trim()) :
                    String(value || '').replace(/\s+/g, ' ').trim();
                  // A label is its own message channel, never descendant text.
                  if (index === 0) unique.ariaLabel = text.length <= 1200 ? text : '';
                  if (text && text.length <= 1200 && !unique.includes(text)) {
                    unique.push(text);
                  }
                }
                return unique;
              };
              const failurePattern = /(generation failed|could not generate|couldn't generate|failed to generate|error generating|you\s*(?:['’]\s*ve|have)\s+reached\s+your\s+usage\s+limit)/i;
              const failureContainerHintPattern = /\bfailed\b|\bwe noticed some unusual activity\b|\bthis generation might violate our policies\b|\byou have not been charged for this generation\b|\byou\s*(?:['’]\s*ve|have)\s+reached\s+your\s+usage\s+limit\b/i;
              // The four exact clauses remain required in order. Flow may place its
              // browser-extension advisory between the unusual-activity and Help
              // Center clauses; normalized text and nested inline content are safe.
              const unusualActivityPattern = /\bfailed\b[\s\S]*\bwe noticed some unusual activity\b[\s\S]*\bplease visit the help center for more information\b[\s\S]*\byou have not been charged for this generation\b/i;
              const unusualActivityFailure =
                'Failed. We noticed some unusual activity. ' +
                'Please visit the Help Center for more information. ' +
                'You have not been charged for this generation.';
              const policyViolationPattern = /\bfailed\b[\s\S]*\bthis generation might violate our policies\b[\s\S]*\bplease try a different prompt or send feedback\b[\s\S]*\byou have not been charged for this generation\b/i;
              const policyViolationFailure =
                'Failed. This generation might violate our policies. ' +
                'Please try a different prompt or send feedback. ' +
                'You have not been charged for this generation.';
              const genericGenerationFailureClause =
                /\bsorry,\s+this image failed to generate\b/i;
              const highDemandPattern = /\bfailed\b[\s\S]*\bflow is experiencing high demand\.\s+please try again later\.[\s\S]*\byou have not been charged for this generation\./i;
              const highDemandFailure =
                'Failed. Flow is experiencing high demand. Please try again later. ' +
                'You have not been charged for this generation.';
              const genericGenerationFailurePattern =
                /\bfailed\b[\s\S]*\bsorry,\s+this image failed to generate\b[\s\S]*\byou have not been charged for this generation\b/i;
              const genericGenerationFailure =
                'Failed. Sorry, this image failed to generate. ' +
                'You have not been charged for this generation.';
              const state = {
                active: false, failures: [], observer: null,
                activatedAtMs: null, mutationCount: 0, lastMutationAtMs: null,
                addedRoots: new WeakSet(), addedRootCount: 0, addedRootTruncated: false,
                progressAncestors: new WeakSet(), progressNodes: new WeakSet(),
                progressRegionCount: 0, progressTruncated: false,
                failureContainers: [], failureContainerNodes: new WeakSet(),
                failureContainerLimit: 200, failureContainerTruncated: false,
                failureBaseline: new WeakMap(), historicalFailureEvidence: new Set(),
                failureBaselineTruncated: false, failureAttributionAmbiguous: false,
                failureMutationSources: new WeakMap(), progressOwners: new WeakSet(),
                clearedFailureSurfaces: new WeakSet(),
                postStartFailureMutationCount: 0,
                visibleFailureContainerCount: 0,
                currentAttemptFailureContainerCount: 0,
                unusualSignatureMatchCount: 0,
                genericSignatureMatchCount: 0,
                lastFailureDetectionReason: 'observer_not_armed',
                diagnosticEnabled, firstMutationAtMs: null,
                callbackCount: 0, callbackTotalMs: 0, callbackMaxMs: 0,
              };
              state.setObserverReadTiming = (timing) => { readTiming = timing; };
              if (diagnosticEnabled && typeof document.addEventListener === 'function') {
                // First trusted primary document gesture after arm; no DOM identity/text.
                const stamps = {installation_at_ms: performance.now()};
                let stopped = false, expired = false, scheduled = false, timeout = null, raf = null, expiry = null;
                let longObserver = null, supported = false, count = 0, total = 0, longest = 0;
                let first = null, longestAt = null, overlaps = false, truncated = false;
                const listeners = [];
                const anchor = () => stamps.pointerdown_at_ms ?? stamps.mousedown_at_ms ?? null;
                const consume = (entries) => {
                  if (stopped || anchor() === null) return;
                  for (const entry of entries) {
                    const start = entry.startTime, duration = entry.duration;
                    if (!Number.isFinite(start) || !Number.isFinite(duration) || duration < 0 || start + duration <= anchor()) continue;
                    if (count >= 128) { truncated = true; break; }
                    count++;
                    first = first === null ? start : Math.min(first, start);
                    if (longestAt === null || duration > longest) { longest = duration; longestAt = start; }
                    total = Math.min(1000000000, total + duration);
                    overlaps = true;
                  }
                };
                const record = (name) => { if (!stopped && stamps[name] === undefined) stamps[name] = performance.now(); };
                const schedule = () => {
                  if (scheduled || stopped) return;
                  scheduled = true;
                  if (typeof queueMicrotask === 'function') queueMicrotask(() => record('microtask_at_ms'));
                  Promise.resolve().then(() => record('promise_microtask_at_ms'));
                  timeout = setTimeout(() => record('timeout_0_at_ms'), 0);
                  if (typeof requestAnimationFrame === 'function') {
                    raf = requestAnimationFrame(() => {
                      record('raf_1_at_ms');
                      if (!stopped) raf = requestAnimationFrame(() => record('raf_2_at_ms'));
                    });
                  }
                };
                for (const type of ['pointerdown', 'mousedown', 'click']) {
                  const handler = (event) => {
                    if (!state.active || stopped || !event.isTrusted || event.button !== 0) return;
                    record(type + '_at_ms');
                    if (type !== 'click') schedule();
                    document.removeEventListener?.(type, handler, true);
                  };
                  document.addEventListener?.(type, handler, {capture: true, passive: true});
                  listeners.push([type, handler]);
                }
                try {
                  if (typeof PerformanceObserver === 'function' &&
                      PerformanceObserver.supportedEntryTypes?.includes('longtask')) {
                    longObserver = new PerformanceObserver((list) => consume(list.getEntries()));
                    longObserver.observe({entryTypes: ['longtask']});
                    supported = true;
                  }
                } catch (_) {
                  try { longObserver?.disconnect(); } catch (_) {}
                  longObserver = null;
                }
                const stop = () => {
                  if (stopped) return;
                  stopped = true;
                  for (const [type, handler] of listeners) document.removeEventListener?.(type, handler, true);
                  listeners.length = 0;
                  if (timeout !== null) clearTimeout(timeout);
                  if (expiry !== null) clearTimeout(expiry);
                  if (raf !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf);
                  try { longObserver?.disconnect(); } catch (_) {}
                };
                state.eventLoopSentinel = {
                  stop,
                  read(mutationAt) {
                    if (stopped && !expired) return null;
                    try { if (supported) consume(longObserver.takeRecords()); } catch (_) {}
                    const result = {
                      ...stamps, read_at_ms: performance.now(), mutation_callback_at_ms: mutationAt ?? null,
                      longtask_supported: supported,
                      longtask_count: supported ? count : null,
                      first_longtask_at_ms: supported ? first : null,
                      longest_longtask_at_ms: supported ? longestAt : null,
                      longest_longtask_ms: supported ? longest : null,
                      total_longtask_ms: supported ? total : null,
                      longtask_overlaps_ack_window: supported && anchor() !== null ? overlaps : null,
                      longtask_truncated: truncated,
                      capture_expired: expired,
                    };
                    stop();
                    return result;
                  },
                };
                // Best-effort one-shot expiry also bounds abandoned/Ctrl+C diagnostics
                // without requiring any fresh browser RPC during intentional unwind.
                expiry = setTimeout(() => { expired = true; stop(); }, 30000);
              }
              // Compare failure clauses, not aggregate DOM text: progress, formatting,
              // or a new sibling must never make inherited failure content look new.
              const evidencePattern = /sorry,\s+this image failed to generate|we noticed some unusual activity|please visit the help center for more information|this generation might violate our policies|please try a different prompt or send feedback|flow is experiencing high demand\.\s+please try again later\.|you have not been charged for this generation|generation failed|could not generate|couldn't generate|failed to generate|error generating|you\s*(?:['’]\s*ve|have)\s+reached\s+your\s+usage\s+limit|\bfailed\b/gi;
              const evidence = (texts) => [...new Set(texts.flatMap((text) =>
                (text.match(evidencePattern) || []).map((clause) =>
                  clause.toLowerCase().replace(/\s+/g, ' ').trim())
              ))].sort().join('|');
              const complete = (texts) => texts.some((text) =>
                unusualActivityPattern.test(text) || policyViolationPattern.test(text) ||
                genericGenerationFailurePattern.test(text) || highDemandPattern.test(text) ||
                (failurePattern.test(text) && !genericGenerationFailureClause.test(text)));
              const boundarySelector =
                'flow-image-tile,article,[data-media-id],[role="alert"],[role="status"],[aria-live]';
              const localOwner = (element) => {
                for (let current = element, depth = 0;
                     current && current !== document.body && depth < 12;
                     current = current.parentElement, depth++) {
                  if (current.matches(boundarySelector)) return current;
                }
                return null;
              };
              const localSurface = (element, texts) => {
                if (complete([texts.ariaLabel || ''])) return true;
                // The smallest complete message subtree is an independent surface.
                // Never assemble clauses across separate card/message boundaries or
                // retain a shared history ancestor of an already complete message.
                if ([...(element.children || [])].some((child) =>
                    complete(normalizedTexts(child)))) return false;
                return element.querySelectorAll(boundarySelector).length === 0;
              };
              state.hasFailureProvenance = () => Boolean(state.observer &&
                Number.isFinite(state.activatedAtMs) &&
                state.failureBaseline instanceof WeakMap &&
                state.historicalFailureEvidence instanceof Set &&
                state.failureMutationSources instanceof WeakMap &&
                state.progressOwners instanceof WeakSet &&
                state.clearedFailureSurfaces instanceof WeakSet);
              state.captureFailureBaseline = () => {
                state.failureBaseline = new WeakMap();
                state.historicalFailureEvidence = new Set();
                state.failureBaselineTruncated = false;
                const walker = document.createTreeWalker(document.body, 1);
                let element, count = 0;
                while ((element = walker.nextNode())) {
                  if (++count > 10000) { state.failureBaselineTruncated = true; break; }
                  const texts = normalizedTexts(element);
                  const signature = evidence(complete([texts.ariaLabel || ''])
                    ? [texts.ariaLabel] : texts);
                  state.failureBaseline.set(element, signature);
                  if (signature) state.historicalFailureEvidence.add(signature);
                }
              };
              state.noteProgress = (element) => {
                if (!state.active || !element?.isConnected || !visible(element) ||
                    state.progressNodes.has(element)) return;
                if (state.progressRegionCount >= 200) {
                  state.progressTruncated = true;
                  return;
                }
                state.progressNodes.add(element);
                state.progressRegionCount++;
                // Only a separate, failure-free semantic owner proves a current
                // progress -> failure transition. History-level progress cannot.
                for (let owner = element, depth = 0;
                     owner && owner !== document.body && depth < 12;
                     owner = owner.parentElement, depth++) {
                  if (owner.matches(boundarySelector) &&
                      !state.failureBaseline.get(owner) &&
                      !evidence(normalizedTexts(owner))) {
                    // A status indicator and its enclosing card may both be
                    // replaced. Do not promote their common history container.
                    const boundaries = [...owner.querySelectorAll(boundarySelector)];
                    if (boundaries.every((child) => child === element || child.contains(element))) {
                      state.progressOwners.add(owner);
                    }
                  }
                }
                for (let ancestor = element, depth = 0;
                     ancestor && ancestor !== document.body && depth < 8;
                     ancestor = ancestor.parentElement, depth++) {
                  state.progressAncestors.add(ancestor);
                }
              };
              state.trackFailureContainers = (element, contentChanged = false) => {
                if (!state.active || !(element instanceof Element)) return;
                const sourceEvidence = contentChanged && evidence(normalizedTexts(element));
                for (let current = element, depth = 0;
                     current && current !== document.body && depth < 12;
                     current = current.parentElement, depth++) {
                  if (!current.isConnected) continue;
                  if (current.matches('[role="progressbar"],[role="status"],[aria-busy="true"]')) {
                    state.noteProgress(current);
                  }
                  const semanticContainer = current.matches(
                    '[role="alert"],[role="status"],[aria-live]'
                  );
                  const texts = normalizedTexts(current);
                  if (contentChanged && current === element && !evidence(texts) &&
                      state.failureBaseline.get(current) && localSurface(current, texts)) {
                    // Reuse of this exact message node after a local content reset
                    // can produce the same failure wording in a later attempt.
                    state.clearedFailureSurfaces.add(current);
                  }
                  const hintedContainer = texts.some(
                    (text) => failureContainerHintPattern.test(text)
                  );
                  if (!semanticContainer && !hintedContainer) continue;
                  if (!localSurface(current, texts)) continue;
                  if (sourceEvidence) state.failureMutationSources.set(current, element);
                  if (state.failureContainerNodes.has(current)) continue;
                  if (state.failureContainers.length >= state.failureContainerLimit) {
                    state.failureContainerTruncated = true;
                    if (complete(texts) && sourceEvidence) state.failureAttributionAmbiguous = true;
                    break;
                  }
                  state.failureContainerNodes.add(current);
                  state.failureContainers.push(current);
                }
              };
              state.detectFailureIn = (element) => {
                if (!(element instanceof Element) || !element.isConnected || !visible(element)) {
                  return false;
                }
                const channels = normalizedTexts(element);
                const texts = complete([channels.ariaLabel || ''])
                  ? [channels.ariaLabel] : channels;
                if (!complete(texts) || !localSurface(element, channels) ||
                    !state.failureMutationSources.has(element)) return false;
                const signature = evidence(texts);
                const baseline = state.failureBaseline.get(element);
                const owner = localOwner(element);
                const transitioned = state.clearedFailureSurfaces.has(element) ||
                  (owner && state.progressOwners.has(owner));
                if (baseline === signature && !transitioned) return false;
                const source = state.failureMutationSources.get(element);
                const inheritedPartial = baseline && source !== element;
                if (!transitioned && (inheritedPartial ||
                    (baseline === undefined && (state.failureBaselineTruncated ||
                     state.historicalFailureEvidence.has(signature))))) {
                  // A remounted historical message and a new identical failure
                  // cannot be distinguished by fresh node identity alone. Nor can
                  // an ancestor combine old partial clauses with a new sibling's
                  // text and establish ownership by its tag/role alone.
                  state.failureAttributionAmbiguous = true;
                  state.lastFailureDetectionReason = 'failure_attribution_ambiguous';
                  return false;
                }
                if (readStep('signature_match', () => texts.some((text) => unusualActivityPattern.test(text)))) {
                  return readStep('failure_record', () => {
                    const isNew = !state.failures.includes(unusualActivityFailure);
                    if (isNew && state.failures.length < 20) {
                      state.failures.push(unusualActivityFailure);
                      state.unusualSignatureMatchCount = Math.min(
                        1000, state.unusualSignatureMatchCount + 1
                      );
                    }
                    state.lastFailureDetectionReason = 'exact_unusual_activity_signature';
                    return true;
                  });
                }
                if (readStep('signature_match', () => texts.some((text) => policyViolationPattern.test(text)))) {
                  return readStep('failure_record', () => {
                    if (!state.failures.includes(policyViolationFailure) &&
                        state.failures.length < 20) {
                      state.failures.push(policyViolationFailure);
                    }
                    state.lastFailureDetectionReason = 'exact_policy_rejection_signature';
                    return true;
                  });
                }
                if (readStep('signature_match', () => texts.some((text) => genericGenerationFailurePattern.test(text)))) {
                  return readStep('failure_record', () => {
                    const isNew = !state.failures.includes(genericGenerationFailure);
                    if (isNew && state.failures.length < 20) {
                      state.failures.push(genericGenerationFailure);
                      state.genericSignatureMatchCount = Math.min(
                        1000, state.genericSignatureMatchCount + 1
                      );
                    }
                    state.lastFailureDetectionReason =
                      'exact_generic_generation_failure_signature';
                    return true;
                  });
                }
                if (readStep('signature_match', () => texts.some((text) => highDemandPattern.test(text)))) {
                  return readStep('failure_record', () => {
                    if (!state.failures.includes(highDemandFailure) && state.failures.length < 20) {
                      state.failures.push(highDemandFailure);
                    }
                    state.lastFailureDetectionReason = 'exact_high_demand_signature';
                    return true;
                  });
                }
                const broadFailure = readStep('signature_match', () => texts.find(
                  (text) => failurePattern.test(text) &&
                    !genericGenerationFailureClause.test(text)
                ));
                if (!broadFailure) return false;
                return readStep('failure_record', () => {
                  const bounded = broadFailure.slice(0, 600);
                  if (!state.failures.includes(bounded) && state.failures.length < 20) {
                    state.failures.push(bounded);
                  }
                  state.lastFailureDetectionReason = 'generic_failure_signature';
                  return true;
                });
              };
              const remember = (element, contentChanged = false) => {
                if (!(element instanceof Element) || !element.isConnected) return;
                state.trackFailureContainers(element, contentChanged);
              };
              state.scanFailureContainers = () => {
                if (!state.active) {
                  state.lastFailureDetectionReason = 'observer_not_armed';
                  return;
                }
                let currentCount = 0;
                let visibleCount = 0;
                const priorFailureCount = state.failures.length;
                for (const element of state.failureContainers) {
                  const container = readTiming?.beginContainer();
                  try {
                    if (!(readTiming ? readStep('container_connectivity', () => element?.isConnected) : element?.isConnected)) continue;
                    currentCount++;
                    if (!visible(element)) continue;
                    visibleCount++;
                    if (readTiming) readStep('failure_detect', () => state.detectFailureIn(element));
                    else state.detectFailureIn(element);
                  } finally {
                    if (readTiming) readTiming.endContainer(container);
                  }
                }
                state.currentAttemptFailureContainerCount = currentCount;
                state.visibleFailureContainerCount = visibleCount;
                if (state.failureAttributionAmbiguous) {
                  state.lastFailureDetectionReason = 'failure_attribution_ambiguous';
                  return;
                }
                if (state.failures.length !== priorFailureCount) return;
                if (currentCount === 0) {
                  state.lastFailureDetectionReason =
                    'no_current_attempt_failure_container';
                } else if (visibleCount === 0) {
                  state.lastFailureDetectionReason =
                    'no_visible_current_attempt_failure_container';
                } else if (state.failures.length === 0) {
                  state.lastFailureDetectionReason = 'no_exact_failure_signature';
                }
              };
              const inspectAddedSubtree = (node) => {
                const element = node instanceof Element ? node : node.parentElement;
                if (!element) return;
                if (node instanceof Element) {
                  if (state.addedRootCount < 10000) {
                    state.addedRoots.add(node);
                    state.addedRootCount++;
                  } else {
                    state.addedRootTruncated = true;
                  }
                }
                if (element.matches('[role="progressbar"],[role="status"],[aria-busy="true"]')) {
                  state.noteProgress(element);
                }
                for (const progress of element.querySelectorAll(
                  '[role="progressbar"],[role="status"],[aria-busy="true"]'
                )) {
                  state.noteProgress(progress);
                }
                remember(element, true);
                for (const candidate of element.querySelectorAll(
                  '[role="alert"],[role="status"],[aria-live],p,span,div'
                )) {
                  remember(candidate, true);
                }
              };
              state.observer = new MutationObserver((records) => {
                if (!state.active) return;
                const callbackStarted = diagnosticEnabled ? performance.now() : null;
                if (callbackStarted !== null && state.firstMutationAtMs === null) {
                  state.firstMutationAtMs = callbackStarted;
                }
                state.mutationCount = Math.min(100000, state.mutationCount + records.length);
                state.postStartFailureMutationCount = Math.min(
                  100000, state.postStartFailureMutationCount + records.length
                );
                state.lastMutationAtMs = performance.now();
                for (const record of records) {
                  if (record.type === 'childList') {
                    remember(record.target);
                    for (const node of record.addedNodes) inspectAddedSubtree(node);
                  } else if (record.type === 'characterData') {
                    const changed = evidence([record.target.textContent || '']) !==
                      evidence([record.oldValue || '']);
                    remember(record.target.parentElement, changed);
                  } else if (record.type === 'attributes') {
                    const changed = record.attributeName === 'aria-label' &&
                      evidence([record.target.getAttribute('aria-label') || '']) !==
                      evidence([record.oldValue || '']);
                    remember(record.target, changed);
                  }
                }
                if (callbackStarted !== null) {
                  const elapsed = Math.max(0, performance.now() - callbackStarted);
                  state.callbackCount = Math.min(1000000000, state.callbackCount + 1);
                  state.callbackTotalMs = Math.min(1000000000, state.callbackTotalMs + elapsed);
                  state.callbackMaxMs = Math.max(state.callbackMaxMs, elapsed);
                }
              });
              state.observer.observe(document.body, {
                childList: true,
                subtree: true,
                characterData: true,
                characterDataOldValue: true,
                attributes: true,
                attributeOldValue: true,
                attributeFilter: [
                  'aria-label', 'aria-live', 'aria-hidden', 'role', 'hidden', 'class', 'style'
                ],
              });
              if (diagnosticEnabled) {
                // Counters only: no retained observer/DOM registry or extra observer.
                const metricsKey = '__nanoBatchGenerationObserverMetricsV1';
                const metrics = window[metricsKey] || {
                  active: 0, installed: 0, disconnected: 0, peak: 0,
                };
                window[metricsKey] = metrics;
                metrics.active = Math.min(1000000000, metrics.active + 1);
                metrics.installed = Math.min(1000000000, metrics.installed + 1);
                metrics.peak = Math.max(metrics.peak, metrics.active);
                const disconnect = state.observer.disconnect.bind(state.observer);
                let counted = true;
                state.observer.disconnect = () => {
                  disconnect();
                  state.eventLoopSentinel?.stop();
                  if (counted) {
                    metrics.active = Math.max(0, metrics.active - 1);
                    metrics.disconnected = Math.min(1000000000, metrics.disconnected + 1);
                    counted = false;
                  }
                };
              }
              Object.defineProperty(window, key, {
                value: state,
                configurable: true,
              });
            }
            """,
            self.prestart_timing_debug,
        )

    def _arm_current_generation_attempt_observation(
        self, *, preserve_attempt: bool = False,
    ) -> None:
        """Accept new failure mutations immediately before Start activation."""
        page = self._require_page()
        armed = page.evaluate(
            r"""
            (preserveAttempt = false) => {
              const state = window.__nanoBatchCurrentGenerationAttempt;
              // Recovery cannot create a Start boundary after submission or use
              // a replacement observer that lacks the original attempt evidence.
              if (preserveAttempt && (typeof state?.hasFailureProvenance !== 'function' ||
                  !state.hasFailureProvenance())) return false;
              if (!state?.observer) {
                throw new Error('Current generation observer is not installed.');
              }
              // Window recovery re-arms the same acknowledged attempt. Preserve
              // its original baseline and provenance rather than snapshotting the
              // post-Start DOM as history or granting it a fresh Start boundary.
              if (state.activatedAtMs !== null) {
                state.active = true;
                return true;
              }
              state.failures = [];
              state.activatedAtMs = performance.now();
              state.mutationCount = 0;
              state.lastMutationAtMs = null;
              state.firstMutationAtMs = null;
              state.callbackCount = 0;
              state.callbackTotalMs = 0;
              state.callbackMaxMs = 0;
              state.addedRoots = new WeakSet();
              state.addedRootCount = 0;
              state.addedRootTruncated = false;
              state.progressAncestors = new WeakSet();
              state.progressNodes = new WeakSet();
              state.progressRegionCount = 0;
              state.progressTruncated = false;
              state.failureContainers = [];
              state.failureContainerNodes = new WeakSet();
              state.failureContainerTruncated = false;
              state.postStartFailureMutationCount = 0;
              state.visibleFailureContainerCount = 0;
              state.currentAttemptFailureContainerCount = 0;
              state.unusualSignatureMatchCount = 0;
              state.genericSignatureMatchCount = 0;
              state.failureAttributionAmbiguous = false;
              state.failureMutationSources = new WeakMap();
              state.progressOwners = new WeakSet();
              state.clearedFailureSurfaces = new WeakSet();
              state.observer.takeRecords();
              state.captureFailureBaseline();
              state.lastFailureDetectionReason = 'awaiting_post_start_mutation';
              state.active = true;
              return true;
            }
            """,
            preserve_attempt,
        )
        if preserve_attempt and armed is not True:
            raise FlowFailureAttributionAmbiguous(
                "the submitted attempt's original failure baseline or provenance was lost."
            )

    @_ack_profiled("observer_read")
    def _current_generation_attempt_failures(self) -> tuple[str, ...]:
        """Return only failures recorded by the active post-Start observer."""
        page = self._require_page()
        diagnostic_started = time.perf_counter() if self.prestart_timing_debug else None
        raw = _ack_call("observer_page_evaluate", page.evaluate,
            r"""
            (diagnosticEnabled = false) => {
              const entered = diagnosticEnabled ? performance.now() : null;
              const stages = diagnosticEnabled ? {} : null;
              const stack = diagnosticEnabled ? [] : null;
              const samples = diagnosticEnabled ? [] : null;
              let scanned = 0, activeContainer = null, slowest = null;
              const timing = diagnosticEnabled ? {
                run(name, work) {
                  const started = performance.now();
                  const frame = {child: 0};
                  stack.push(frame);
                  try { return work(); }
                  finally {
                    const elapsed = Math.max(0, performance.now() - started);
                    stack.pop();
                    if (stack.length) stack[stack.length - 1].child += elapsed;
                    const self = Math.max(0, elapsed - frame.child);
                    const stats = stages[name] || (stages[name] = {calls: 0, total_ms: 0, max_ms: 0, self_ms: 0});
                    stats.calls++; stats.total_ms += elapsed;
                    stats.max_ms = Math.max(stats.max_ms, elapsed); stats.self_ms += self;
                    if (activeContainer) activeContainer.stages[name] = (activeContainer.stages[name] || 0) + self;
                  }
                },
                beginContainer() {
                  activeContainer = {index: scanned++, started: performance.now(), stages: {}};
                  return activeContainer;
                },
                endContainer(container) {
                  const elapsed = Math.max(0, performance.now() - container.started);
                  samples.push(elapsed);
                  if (samples.length > 128) samples.shift();
                  if (!slowest || elapsed > slowest.elapsed) {
                    const names = Object.keys(container.stages);
                    slowest = {index: container.index, elapsed,
                      stage: names.sort((a, b) => container.stages[b] - container.stages[a])[0] || null};
                  }
                  activeContainer = null;
                },
              } : null;
              const state = timing ? timing.run('state_lookup', () => window.__nanoBatchCurrentGenerationAttempt)
                : window.__nanoBatchCurrentGenerationAttempt;
              if (typeof state?.hasFailureProvenance !== 'function' ||
                  !state.hasFailureProvenance()) {
                return {failures: [], failure_attribution_ambiguous: true, diagnostics: {
                  failure_observer_active: Boolean(state?.active),
                  last_failure_detection_reason: 'attempt_provenance_lost',
                }};
              }
              if (timing) state?.setObserverReadTiming?.(timing);
              let payload;
              try {
                if (timing) timing.run('retained_container_scan', () => state?.scanFailureContainers?.());
                else state?.scanFailureContainers?.();
                const buildPayload = () => state ? {
                  failures: [...state.failures],
                  failure_attribution_ambiguous: Boolean(state.failureAttributionAmbiguous),
                  diagnostics: {
                    failure_observer_active: Boolean(state.active),
                    generation_dom_mutation_count: state.mutationCount ?? 0,
                    post_start_failure_mutation_count:
                      state.postStartFailureMutationCount ?? 0,
                    visible_failure_container_count:
                      state.visibleFailureContainerCount ?? 0,
                    current_attempt_failure_container_count:
                      state.currentAttemptFailureContainerCount ?? 0,
                    unusual_signature_match_count:
                      state.unusualSignatureMatchCount ?? 0,
                    generic_generation_signature_match_count:
                      state.genericSignatureMatchCount ?? 0,
                    last_failure_detection_reason:
                      state.lastFailureDetectionReason || 'unknown',
                    first_mutation_at_ms: state.firstMutationAtMs ?? null,
                    active_observer_count: window.__nanoBatchGenerationObserverMetricsV1?.active ?? null,
                    observer_install_count: window.__nanoBatchGenerationObserverMetricsV1?.installed ?? null,
                    observer_disconnect_count: window.__nanoBatchGenerationObserverMetricsV1?.disconnected ?? null,
                    observer_peak_count: window.__nanoBatchGenerationObserverMetricsV1?.peak ?? null,
                    observer_callback_count: state.diagnosticEnabled ? state.callbackCount : null,
                    observer_callback_total_ms: state.diagnosticEnabled ? state.callbackTotalMs : null,
                    observer_callback_max_ms: state.diagnosticEnabled ? state.callbackMaxMs : null,
                    retained_failure_container_count: state.diagnosticEnabled ? state.failureContainers.length : null,
                    detached_failure_container_count: state.diagnosticEnabled
                      ? state.failureContainers.filter((e) => !e.isConnected).length : null,
                  },
                } : {failures: [], diagnostics: {
                  failure_observer_active: false,
                  post_start_failure_mutation_count: 0,
                  visible_failure_container_count: 0,
                  current_attempt_failure_container_count: 0,
                  unusual_signature_match_count: 0,
                  generic_generation_signature_match_count: 0,
                  last_failure_detection_reason: 'observer_missing',
                }};
                payload = timing ? timing.run('return_payload', buildPayload) : buildPayload();
              } finally {
                if (timing) state?.setObserverReadTiming?.(null);
              }
              if (timing) {
                samples.sort((a, b) => a - b);
                const middle = Math.floor(samples.length / 2);
                const median = samples.length ? (samples.length % 2 ? samples[middle]
                  : (samples[middle - 1] + samples[middle]) / 2) : null;
                const internal = {
                  stages, containers_scanned: scanned,
                  retained_strong_container_count: state?.failureContainers?.length ?? null,
                  mutation_record_count: state?.mutationCount ?? null,
                  container_sample_count: samples.length,
                  max_container_ms: slowest?.elapsed ?? null,
                  p50_container_ms: median,
                  p95_container_ms: samples.length >= 20 ? samples[Math.ceil(samples.length * .95) - 1] : null,
                  slowest_container_index: slowest?.index ?? null,
                  slowest_container_stage: slowest?.stage ?? null,
                  evaluate_enter_browser_ms: entered,
                };
                payload.diagnostics.observer_read_internal_timing = internal;
                const exited = performance.now();
                internal.evaluate_exit_browser_ms = exited;
                internal.total_browser_js_ms = Math.max(0, exited - entered);
              }
              return payload;
            }
            """,
            self.prestart_timing_debug,
        )
        diagnostic_returned = time.perf_counter() if diagnostic_started is not None else None
        items = raw.get("failures", []) if isinstance(raw, dict) else raw
        diagnostics = raw.get("diagnostics", {}) if isinstance(raw, dict) else {}
        if diagnostic_started is not None and self._post_start_ack_timing is not None:
            observer_duration_ms = (time.perf_counter() - diagnostic_started) * 1_000
            self._post_start_ack_timing.record_latency(
                "observer_read", observer_duration_ms
            )
            self._post_start_ack_timing.note_observer_read(diagnostics, observer_duration_ms)
            self._post_start_ack_timing.observe_browser_metrics(diagnostics)
            self._post_start_ack_timing.record_observer_internal(
                diagnostics.get("observer_read_internal_timing") if isinstance(diagnostics, dict) else None,
                diagnostic_started, diagnostic_returned,
            )
        mutation_count = (
            diagnostics.get("generation_dom_mutation_count")
            if isinstance(diagnostics, dict)
            else None
        )
        attribution_ambiguous = isinstance(raw, dict) and raw.get("failure_attribution_ambiguous") is True
        self._current_generation_failure_poll_observation = {
            "generation_dom_mutation_count": (
                max(0, min(1_000_000, int(mutation_count)))
                if isinstance(mutation_count, (int, float))
                and not isinstance(mutation_count, bool)
                else None
            ),
            "exact_explicit_failure_detected": bool(items) and not attribution_ambiguous,
        }
        if attribution_ambiguous:
            raise FlowFailureAttributionAmbiguous()
        return tuple(
            dict.fromkeys(
                _safe_current_generation_failure(str(item))
                for item in (items or [])
                if _is_current_generation_failure_text(str(item))
            )
        )

    def _raise_for_current_generation_attempt_failure(self) -> None:
        failures = self._current_generation_attempt_failures()
        if any(_FLOW_USAGE_LIMIT_PATTERN.search(failure) for failure in failures):
            raise FlowUsageLimitReached()
        if any(
            _POLICY_VIOLATION_GENERATION_FAILURE_PATTERN.search(failure)
            for failure in failures
        ):
            raise FlowPolicyRejected()
        if any(
            _UNUSUAL_ACTIVITY_GENERATION_FAILURE_PATTERN.search(failure)
            for failure in failures
        ):
            raise FlowUnusualActivity()
        if any(
            _GENERIC_GENERATION_FAILURE_PATTERN.search(failure)
            for failure in failures
        ):
            raise FlowGenericGenerationFailed()
        if failures:
            if any(_HIGH_DEMAND_GENERATION_FAILURE_PATTERN.search(failure) for failure in failures):
                raise FlowHighDemand()
            raise FlowAutomationError(
                "Flow reported a terminal failure for the current generation: "
                "unrecognized failure message; raw UI text was omitted. "
                "Explicit manual Retry is required."
            )

    def _stop_current_generation_attempt_observation(self) -> None:
        self._stop_generation_network_timing()
        if cancellation_requested():
            return
        page = self._require_page()
        try:
            page.evaluate(
                r"""
                () => {
                  const key = '__nanoBatchCurrentGenerationAttempt';
                  const state = window[key];
                  if (state?.observer) state.observer.disconnect();
                  delete window[key];
                }
                """
            )
        except Exception:
            pass

    @_ack_wait_scope
    def _wait_for_generation_start(
        self,
        *,
        prompt_input,
        intended_prompt: str,
        baseline_fingerprints: set[str],
        baseline_progress: set[str],
        baseline_candidate_ids: set[str] | None = None,
        forbidden_candidate_ids: set[str] | frozenset[str] | None = None,
        timeout_ms: int = _GENERATION_START_ACK_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Require a positive post-activation transition without ever clicking again."""
        if not 500 <= timeout_ms <= 15_000:
            raise ValueError(
                "Generation start acknowledgement wait must be between 500 and 15000 ms."
            )
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        if self._post_start_ack_timing is None:
            self._begin_post_start_ack_timing(origin_source="wait_entry_fallback")
        assert self._post_start_ack_timing is not None
        timing = self._post_start_ack_timing
        timing.configure_wait(
            timeout_ms=timeout_ms,
            poll_interval_ms=_GENERATION_START_ACK_POLL_INTERVAL_MS,
        )
        poll_count = 0

        def acknowledged(signal: str) -> dict[str, Any]:
            timing.acknowledge(signal)
            self._prestart_polling(
                "start_acknowledgement",
                poll_count=poll_count,
                poll_interval_ms=_GENERATION_START_ACK_POLL_INTERVAL_MS,
                timeout_ms=timeout_ms,
                success_immediate=poll_count == 1,
                final_reason=signal,
            )
            return {"signal": signal}

        while time.monotonic() < deadline:
            _ack_call("cancellation_checkpoint", cancellation_checkpoint, "generation_acknowledgement_wait")
            poll_count += 1
            timing.begin_poll()
            self._current_generation_failure_poll_observation = None
            try:
                _ack_call("failure_observation", self._raise_for_current_generation_attempt_failure)
            except FlowAutomationError as exc:
                attribution_ambiguous = isinstance(exc, FlowFailureAttributionAmbiguous)
                failure_poll = self._current_generation_failure_poll_observation or {}
                timing.stage_complete(
                    "explicit_failure_check",
                    generation_dom_mutation_count=failure_poll.get(
                        "generation_dom_mutation_count"
                    ),
                    exact_explicit_failure_detected=not attribution_ambiguous,
                )
                timing.acknowledge(
                    "failure_attribution_ambiguous" if attribution_ambiguous else "explicit_failure"
                )
                raise
            failure_poll = self._current_generation_failure_poll_observation or {}
            timing.stage_complete(
                "explicit_failure_check",
                generation_dom_mutation_count=failure_poll.get(
                    "generation_dom_mutation_count"
                ),
                exact_explicit_failure_detected=False,
            )
            new_progress = set(_ack_call("progress_observation", self._progress_signals)) - baseline_progress
            timing.stage_complete(
                "progress_check",
                progress_signal_count=len(new_progress),
            )
            if new_progress:
                return acknowledged("new_generation_progress")
            candidates = _ack_call(
                "candidate_observation", self.discover_candidates,
                baseline_fingerprints,
                baseline_candidate_ids=baseline_candidate_ids,
                forbidden_candidate_ids=forbidden_candidate_ids,
            )
            timing.stage_complete(
                "candidate_check",
                candidate_count=len(candidates),
                candidate_uuid_appeared=any(
                    candidate.candidate_id is not None for candidate in candidates
                ),
            )
            if candidates:
                return acknowledged("new_candidate_visible")

            _current_prompt, buttons, facts = (
                _ack_call("composer_resolution", self._current_generation_submit_buttons)
            )
            composer_token = facts.get("composer_token")
            composer_remounted = bool(
                timing.initial_composer_token is not None
                and isinstance(composer_token, (int, float))
                and not isinstance(composer_token, bool)
                and int(composer_token) != timing.initial_composer_token
            )
            timing.stage_complete(
                "composer_check",
                **self._post_start_button_state(buttons, facts),
                composer_remounted=composer_remounted,
            )
            if not buttons:
                if facts.get("active_composer_generation_control_count") == 0:
                    return acknowledged("submit_button_removed")
                if (
                    facts.get("active_composer_generation_control_count", 0) > 0
                    and facts.get("actionable_owned_generation_control_count") == 0
                ):
                    return acknowledged("submit_button_hidden")
            if len(buttons) == 1:
                try:
                    button_facts = _ack_call("start_state_read", self._live_control_facts, buttons[0])
                except Exception:
                    button_facts = {}
                timing.stage_complete(
                    "live_start_check",
                    **self._post_start_button_state(
                        buttons,
                        facts,
                        live=button_facts if button_facts else None,
                    ),
                    composer_remounted=composer_remounted,
                )
                if button_facts and not button_facts.get("enabled"):
                    return acknowledged("submit_button_disabled")
            try:
                prompt_state = _ack_call(
                    "prompt_state_read", self._prompt_control_post_start_state,
                    prompt_input,
                    intended_prompt,
                )
                timing.stage_complete(
                    "prompt_check",
                    prompt_editor_changed=prompt_state["changed"],
                    prompt_editor_cleared=prompt_state["cleared"],
                    prompt_editor_disabled=prompt_state["disabled"],
                )
                if prompt_state["changed"]:
                    return acknowledged("prompt_editor_transition")
            except Exception:
                timing.stage_complete(
                    "prompt_check",
                    prompt_editor_changed=None,
                    prompt_editor_cleared=None,
                    prompt_editor_disabled=None,
                )
            timing.finish_poll()
            _ack_call("poll_wait", page.wait_for_timeout, _GENERATION_START_ACK_POLL_INTERVAL_MS)
            profiler = _ACTIVE_ACK_PROFILER.get()
            if profiler is not None:
                profiler.finish_poll()
        timing.record_timeout()
        self._prestart_polling(
            "start_acknowledgement",
            poll_count=poll_count,
            poll_interval_ms=_GENERATION_START_ACK_POLL_INTERVAL_MS,
            timeout_ms=timeout_ms,
            success_immediate=False,
            final_reason="acknowledgement_timeout",
        )
        raise FlowAutomationError(
            "GENERATION_START_UNCONFIRMED: one Start generation mouse activation "
            "was dispatched, but no positive generation-start signal appeared; "
            "the button was not activated again."
        )

    def submit_prompt(
        self,
        prompt: str,
        *,
        baseline_fingerprints: set[str] | None = None,
        baseline_candidate_ids: set[str] | None = None,
        forbidden_candidate_ids: set[str] | frozenset[str] | None = None,
        baseline_progress: set[str] | None = None,
        keep_attempt_observer: bool = False,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")
        if (
            self._prestart_timing is None
            or (self._prestart_timing_observation or {}).get("origin_established")
        ):
            self._begin_prestart_timing()
        if baseline_fingerprints is None:
            self._prestart_mark("candidate_baseline_start_ms")
            baseline_fingerprints = {
                asset["fingerprint"] for asset in self._asset_snapshot()
            }
            self._prestart_mark("candidate_baseline_complete_ms")
        else:
            baseline_fingerprints = set(baseline_fingerprints)
        if baseline_progress is None:
            self._prestart_mark("progress_baseline_start_ms")
            baseline_progress = set(self._progress_signals())
            self._prestart_mark("progress_baseline_complete_ms")
        else:
            baseline_progress = set(baseline_progress)
        if baseline_progress:
            raise FlowAutomationError(
                "GENERATION_ALREADY_IN_PROGRESS: visible generation activity exists "
                "before prompt submission."
            )
        self._set_generation_interrupt_checkpoint("composer_resolution")
        prompt_input, _structural_facts = (
            self._wait_for_owned_generation_control_structure()
        )
        self._set_generation_interrupt_checkpoint("prompt_entry")
        self._prestart_mark("prompt_fill_start_ms")
        try:
            prompt_input.fill(prompt)
            self._prestart_mark("prompt_fill_end_ms")
        except FlowGenerationPreflightError:
            cancellation_checkpoint("prompt_entry")
            raise
        except Exception as exc:
            cancellation_checkpoint("prompt_entry")
            raise FlowGenerationPreflightError(
                "the prompt could not be entered before Start. No generation was "
                "submitted."
            ) from exc
        self._set_generation_interrupt_checkpoint(
            "post_input_start_actionability",
            prompt_written=True,
        )
        self._prestart_mark("prompt_verification_start_ms")
        self._prestart_mark("composer_reacquire_start_ms")
        self._prestart_mark("start_control_reacquire_start_ms")
        self._prestart_mark("start_actionability_wait_start_ms")
        prompt_input, submit, _snapshot, stable_box = (
            self._wait_for_generation_submit_button(intended_prompt=prompt)
        )
        composer_token = _snapshot.get("composer_token")
        self._post_start_initial_composer_token = (
            int(composer_token)
            if isinstance(composer_token, (int, float))
            and not isinstance(composer_token, bool)
            else None
        )
        self._set_generation_interrupt_checkpoint("pre_start_observer")
        self._prestart_mark("failure_observer_arm_start_ms")
        try:
            self._arm_generation_network_timing()
            self._begin_current_generation_attempt_observation()
        except KeyboardInterrupt:
            self._stop_generation_network_timing(skip_rpc=True)
            raise
        except Exception as exc:
            cancellation_checkpoint("pre_start_observer")
            self._stop_generation_network_timing()
            raise FlowGenerationPreflightError(
                "the current-attempt observer could not be established before "
                "Start. No generation was submitted."
            ) from exc
        try:
            self._arm_current_generation_attempt_observation()
            self._prestart_mark("failure_observer_armed_ms")
            activation = self._activate_generation_submit_button(submit, stable_box)
            activated_at = time.perf_counter()
            self._set_generation_interrupt_checkpoint("start_acknowledgement_wait")
            self._prestart_mark("start_ack_wait_start_ms")
            try:
                acknowledgement = self._wait_for_generation_start(
                    prompt_input=prompt_input,
                    intended_prompt=prompt,
                    baseline_fingerprints=baseline_fingerprints,
                    baseline_progress=baseline_progress,
                    baseline_candidate_ids=baseline_candidate_ids,
                    forbidden_candidate_ids=forbidden_candidate_ids,
                )
            except KeyboardInterrupt:
                raise
            except Exception:
                self._capture_event_loop_sentinel()
                raise
            ack_elapsed_ms = round((time.perf_counter() - activated_at) * 1_000)
            self._prestart_mark("start_ack_ms")
            self._set_generation_interrupt_checkpoint(
                "start_acknowledged",
                start_acknowledged=True,
            )
            self._capture_event_loop_sentinel()
            self._capture_latency_counts("post_ack")
            if self.prestart_timing_debug and self._post_start_ack_timing is not None:
                # Scalars only; never retain UUIDs or candidate snapshot contents.
                for counts in self._post_start_ack_timing._diagnostic_counts.values():
                    counts["baseline_candidate_count"] = len(baseline_candidate_ids or ())
                    counts["run_owned_candidate_count"] = len(forbidden_candidate_ids or ())
            prestart_timing = self._prestart_timing_snapshot()
            post_start_ack_timing = self._post_start_ack_timing_snapshot()
            if self.prestart_timing_debug:
                print(
                    "\n"
                    + self._format_prestart_timing(prestart_timing)
                    + "\n\n"
                    + self._format_post_start_ack_timing(post_start_ack_timing)
                    + "\n"
                )
            return {
                "activation": activation,
                "acknowledgement": acknowledgement,
                "ack_elapsed_ms": ack_elapsed_ms,
                "prestart_timing": prestart_timing,
                "post_start_ack_timing": post_start_ack_timing,
            }
        finally:
            self._stop_generation_network_timing()
            if not keep_attempt_observer:
                self._stop_current_generation_attempt_observation()

    @_ack_profiled("_progress_signals")
    def _progress_signals(self) -> tuple[str, ...]:
        page = self._require_page()
        raw = _ack_call("progress_page_evaluate", page.evaluate,
            """
            () => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              return [...document.querySelectorAll('[role="progressbar"],[role="status"],[aria-busy="true"]')]
                .filter(visible)
                .map((element) => {
                  window.__nanoBatchCurrentGenerationAttempt?.noteProgress?.(element);
                  return (element.getAttribute('aria-label') || element.innerText || '')
                    .replace(/\\s+/g, ' ').trim().slice(0, 180);
                });
            }
            """
        )
        return tuple(item for item in raw if item)

    def _generation_window_watchdog(
        self,
        *,
        recovery_count: int,
    ) -> dict[str, Any] | None:
        """Recover each low-frequency unusable-window event for the same attempt."""
        if recovery_count < 0:
            raise ValueError("Window recovery count cannot be negative.")
        snapshot = self._production_window_snapshot()
        if snapshot["window_state"] == "maximized" and self._production_viewport_usable(
            snapshot["viewport"]
        ):
            return None
        return self.ensure_production_window_usable(
            reason="generation_wait_minimized"
        )

    def wait_for_result(
        self,
        baseline_fingerprints: set[str],
        timeout_seconds: int,
        *,
        baseline_progress: set[str] | None = None,
        expected_count: int | None = None,
        baseline_candidate_ids: set[str] | None = None,
        forbidden_candidate_ids: set[str] | frozenset[str] | None = None,
    ) -> tuple[DiscoveredCandidate, ...]:
        page = self._require_page()
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        prior_signature: tuple[tuple[str, str | None, str | None, str | None], ...] | None = None
        prior_progress: set[str] | None = None
        stable_polls = 0
        baseline_progress = baseline_progress or set()
        observation: dict[str, Any] = {
            "polls": 0,
            "first_progress_poll": None,
            "last_progress_poll": None,
            "first_candidate_poll": None,
            "last_candidate_poll": None,
            "max_candidate_count": 0,
            "last_candidate_count": 0,
            "max_progress_count": 0,
            "last_progress_count": 0,
            "progress_change_count": 0,
            "last_progress_change_poll": None,
            "elapsed_ms": 0,
            "window_watchdog_checks": 0,
            "window_recovery_attempted": False,
            "window_recovery_count": 0,
            "window_recovery_events": [],
        }
        self._generation_result_observation = observation
        self._candidate_correlation_failure_observation = None
        # Check native state before the first post-acknowledgement DOM read so an
        # immediate minimize cannot turn a renderer transition into generation failure.
        next_window_watchdog_at = started_at
        while True:
            cancellation_checkpoint("generation_result_wait")
            now = time.monotonic()
            observation["elapsed_ms"] = max(0, round((now - started_at) * 1_000))
            if now >= deadline:
                break
            observation["polls"] += 1
            if self._page_cdp_session is not None and now >= next_window_watchdog_at:
                observation["window_watchdog_checks"] += 1
                recovery = self._generation_window_watchdog(
                    recovery_count=int(observation["window_recovery_count"])
                )
                if recovery is not None:
                    observation["window_recovery_attempted"] = True
                    observation["window_recovery_count"] += 1
                    observation["window_recovery_events"].append(recovery)
                    if (
                        len(observation["window_recovery_events"])
                        > _WINDOW_RECOVERY_EVENT_LIMIT
                    ):
                        del observation["window_recovery_events"][
                            :-_WINDOW_RECOVERY_EVENT_LIMIT
                        ]
                next_window_watchdog_at = (
                    now + _GENERATION_WINDOW_WATCHDOG_INTERVAL_SECONDS
                )
            self._raise_for_current_generation_attempt_failure()
            candidates = interruptible_call(
                "candidate_discovery",
                "candidate_dom_discovery",
                self.discover_candidates,
                baseline_fingerprints,
                baseline_candidate_ids=baseline_candidate_ids,
                forbidden_candidate_ids=forbidden_candidate_ids,
            )
            progress = set(self._progress_signals()) - baseline_progress
            poll = observation["polls"]
            observation["last_candidate_count"] = len(candidates)
            observation["last_progress_count"] = len(progress)
            observation["max_progress_count"] = max(
                observation["max_progress_count"], len(progress)
            )
            if prior_progress is None or progress != prior_progress:
                observation["progress_change_count"] += 1
                observation["last_progress_change_poll"] = poll
            prior_progress = progress
            observation["max_candidate_count"] = max(
                observation["max_candidate_count"], len(candidates)
            )
            if candidates:
                if observation["first_candidate_poll"] is None:
                    observation["first_candidate_poll"] = poll
                observation["last_candidate_poll"] = poll
            if progress:
                if observation["first_progress_poll"] is None:
                    observation["first_progress_poll"] = poll
                observation["last_progress_poll"] = poll
            signature = tuple(
                (
                    candidate.source_kind,
                    candidate.candidate_id,
                    candidate.asset_url,
                    candidate.accessible_name,
                )
                for candidate in candidates
            )
            if expected_count is not None and len(candidates) > expected_count:
                try:
                    self._candidate_correlation_failure_observation = (
                        self._candidate_correlation_observation(
                            baseline_fingerprints=baseline_fingerprints,
                            baseline_candidate_ids=set(baseline_candidate_ids or ()),
                            forbidden_candidate_ids=set(forbidden_candidate_ids or ()),
                            observed_count=len(candidates),
                            expected_count=expected_count,
                        )
                    )
                except Exception:
                    self._candidate_correlation_failure_observation = {
                        "capture": "unavailable"
                    }
                raise FlowAutomationError(
                    f"{CANDIDATE_CORRELATION_UNPROVEN}: expected exactly "
                    f"{expected_count} new candidate UUIDs, observed {len(candidates)}."
                )
            enough_candidates = expected_count is None or len(candidates) == expected_count
            if candidates and enough_candidates and not progress:
                stable_polls = stable_polls + 1 if signature == prior_signature else 1
                if stable_polls >= 3:
                    return candidates
            else:
                stable_polls = 0
            prior_signature = signature
            page.wait_for_timeout(2_000)
        raise FlowAutomationError(
            f"No stable terminal image result was detected within {timeout_seconds} seconds."
        )

    def _generation_failure_observation(
        self,
        *,
        error: Exception,
        submission: dict[str, Any] | None,
        baseline_fingerprints: set[str],
        baseline_progress: set[str],
        baseline_candidate_ids: set[str],
        forbidden_candidate_ids: frozenset[str],
    ) -> dict[str, Any]:
        """Read only bounded, non-identifying evidence before the attempt observer stops."""
        result: dict[str, Any] = {
            "phase": "result_wait" if submission is not None else "submission",
            "start_ack_signal": (
                submission["acknowledgement"]["signal"] if submission else None
            ),
            "start_ack_ms": submission.get("ack_elapsed_ms") if submission else None,
            "prestart_timing": (
                submission.get("prestart_timing")
                if submission is not None
                else self._prestart_timing_snapshot()
            ),
            "post_start_ack_timing": (
                submission.get("post_start_ack_timing")
                if submission is not None
                else self._post_start_ack_timing_snapshot()
            ),
            "result_wait": self._generation_result_observation,
        }
        if self.prestart_timing_debug and self._post_start_ack_timing is not None:
            saved_timing = result.get("post_start_ack_timing")
            if isinstance(saved_timing, dict):
                result["post_start_ack_timing"] = {
                    **saved_timing,
                    "observer_read_breakdown": self._post_start_ack_timing.observer_read_snapshot(),
                }
        if self._candidate_correlation_failure_observation is not None:
            result["candidate_correlation"] = (
                self._candidate_correlation_failure_observation
            )
        page_observation: dict[str, Any] = {
            "target_attached": None,
            "dom_query_responsive": False,
            "cdp_responsive": None,
            "flow_controls_queryable": False,
            "prompt_control_count": None,
            "generation_submit_control_count": None,
            "window_state": "unknown",
            "lifecycle_state": None,
            "page_lifecycle_keepalive_applied": (
                self._page_lifecycle_keepalive_applied
            ),
        }
        try:
            page = self._require_page()
        except Exception:
            page = None
            page_observation["target_attached"] = False
        if page is not None:
            try:
                page_observation["target_attached"] = not page.is_closed()
            except Exception:
                pass
        try:
            if page is None:
                raise RuntimeError("page unavailable")
            snapshot = page.evaluate(
                """
                () => {
                  const state = window.__nanoBatchCurrentGenerationAttempt;
                  state?.scanFailureContainers?.();
                  const now = performance.now();
                  return {
                    renderer: {
                      visibility: document.visibilityState,
                      has_focus: document.hasFocus(),
                      outer_width: Number.isFinite(window.outerWidth) ? window.outerWidth : null,
                      outer_height: Number.isFinite(window.outerHeight) ? window.outerHeight : null,
                      inner_width: Number.isFinite(window.innerWidth) ? window.innerWidth : null,
                      inner_height: Number.isFinite(window.innerHeight) ? window.innerHeight : null,
                      viewport_width: document.documentElement?.clientWidth ?? null,
                      viewport_height: document.documentElement?.clientHeight ?? null,
                      observer_active: Boolean(state?.active),
                      ms_since_activation: state?.activatedAtMs == null ? null :
                        Math.round(now - state.activatedAtMs),
                      mutation_count: state?.mutationCount ?? null,
                      ms_since_last_mutation: state?.lastMutationAtMs == null ? null :
                        Math.round(now - state.lastMutationAtMs),
                      observed_failure_count: state?.failures?.length ?? null,
                      failure_observer_active: Boolean(state?.active),
                      post_start_failure_mutation_count:
                        state?.postStartFailureMutationCount ?? null,
                      visible_failure_container_count:
                        state?.visibleFailureContainerCount ?? null,
                      current_attempt_failure_container_count:
                        state?.currentAttemptFailureContainerCount ?? null,
                      unusual_signature_match_count:
                        state?.unusualSignatureMatchCount ?? null,
                      generic_generation_signature_match_count:
                        state?.genericSignatureMatchCount ?? null,
                      last_failure_detection_reason:
                        state?.lastFailureDetectionReason || 'observer_missing',
                    },
                    page: {
                      prompt_control_count: document.querySelectorAll(
                        'textarea,[contenteditable="true"]'
                      ).length,
                      generation_submit_control_count: [...document.querySelectorAll(
                        'button,[role="button"]'
                      )].filter((element) => /^(start|create|generate)$/i.test(String(
                        element.getAttribute('aria-label') || element.innerText || ''
                      ).replace(/\\s+/g, ' ').trim())).length,
                    },
                  };
                }
                """
            )
            if isinstance(snapshot, dict) and isinstance(snapshot.get("renderer"), dict):
                result["renderer"] = snapshot["renderer"]
                controls = snapshot.get("page") if isinstance(snapshot.get("page"), dict) else {}
                page_observation["dom_query_responsive"] = True
                page_observation["flow_controls_queryable"] = True
                page_observation["prompt_control_count"] = controls.get("prompt_control_count")
                page_observation["generation_submit_control_count"] = controls.get(
                    "generation_submit_control_count"
                )
            else:
                result["renderer"] = {"capture": "unavailable"}
        except Exception:
            result["renderer"] = {"capture": "unavailable"}
        cdp = self._page_cdp_session
        browser_observation: dict[str, Any] = {
            "version": None,
            "protocol_version": None,
            "command_line_available": False,
            "required_background_flags_present": (
                self._required_background_flags_present
                if self._required_background_flags_present is not None
                else "unknown"
            ),
            "startup_flag_presence": {
                "disable_background_timer_throttling": None,
                "disable_backgrounding_occluded_windows": None,
                "disable_renderer_backgrounding": None,
                "disable_calculate_native_win_occlusion": None,
            },
        }
        if cdp is not None:
            try:
                cdp.send(
                    "Runtime.evaluate",
                    {"expression": "1", "returnByValue": True, "silent": True},
                )
                page_observation["cdp_responsive"] = True
            except Exception:
                page_observation["cdp_responsive"] = False
            try:
                window = cdp.send("Browser.getWindowForTarget")
                bounds = window.get("bounds") if isinstance(window, dict) else None
                window_state = bounds.get("windowState") if isinstance(bounds, dict) else None
                if window_state in {"normal", "minimized", "maximized", "fullscreen"}:
                    page_observation["window_state"] = window_state
            except Exception:
                pass
            try:
                version = cdp.send("Browser.getVersion")
                if isinstance(version, dict):
                    product = version.get("product")
                    protocol = version.get("protocolVersion")
                    browser_observation["version"] = (
                        str(product)[:80] if isinstance(product, str) else None
                    )
                    browser_observation["protocol_version"] = (
                        str(protocol)[:40] if isinstance(protocol, str) else None
                    )
            except Exception:
                pass
            try:
                command_line = cdp.send("Browser.getBrowserCommandLine")
                arguments = command_line.get("arguments") if isinstance(command_line, dict) else None
                presence = self._background_flag_presence(arguments)
                if presence is not None:
                    browser_observation["command_line_available"] = True
                    browser_observation["startup_flag_presence"] = presence
                    browser_observation["required_background_flags_present"] = all(
                        presence.values()
                    )
            except Exception:
                pass
        result["page"] = page_observation
        result["browser"] = browser_observation
        try:
            result["current_progress_count"] = len(
                set(self._progress_signals()) - baseline_progress
            )
        except Exception:
            result["current_progress_count"] = None
        try:
            result["current_candidate_count"] = len(
                self.discover_candidates(
                    baseline_fingerprints,
                    baseline_candidate_ids=baseline_candidate_ids,
                    forbidden_candidate_ids=forbidden_candidate_ids,
                )
            )
        except Exception:
            result["current_candidate_count"] = None
        result["elapsed_ms"] = (
            (result.get("result_wait") or {}).get("elapsed_ms")
            if result["phase"] == "result_wait"
            else (result.get("renderer") or {}).get("ms_since_activation")
        )
        message = str(error)
        result_wait = result.get("result_wait") or {}
        renderer = result.get("renderer") or {}
        page_health = result["page"]
        if "Flow reported a terminal failure for the current generation" in message:
            category = "explicit_flow_failure"
        elif "GENERATION_START_UNCONFIRMED" in message:
            category = "start_not_acknowledged"
        elif CANDIDATE_CORRELATION_UNPROVEN in message:
            category = "candidate_correlation_unproven"
        elif (
            page_health.get("target_attached") is False
            or page_health.get("dom_query_responsive") is False
            or page_health.get("cdp_responsive") is False
        ):
            category = "connectivity_or_page_failure"
        elif "No stable terminal image result" in message:
            max_progress = int(result_wait.get("max_progress_count") or 0)
            last_progress = int(result_wait.get("last_progress_count") or 0)
            last_mutation_ms = renderer.get("ms_since_last_mutation")
            mutation_count = renderer.get("mutation_count")
            since_activation_ms = renderer.get("ms_since_activation")
            mutation_stale = (
                isinstance(last_mutation_ms, (int, float)) and last_mutation_ms >= 30_000
            ) or (
                last_mutation_ms is None
                and mutation_count == 0
                and isinstance(since_activation_ms, (int, float))
                and since_activation_ms >= 30_000
            )
            if max_progress and not last_progress:
                category = "result_timeout_progress_disappeared"
            elif max_progress and mutation_stale:
                category = "result_timeout_progress_stalled_evidence"
            elif mutation_stale:
                category = "result_timeout_renderer_stalled_evidence"
            else:
                category = "result_timeout_active_renderer_no_result"
        elif result["phase"] == "submission":
            category = "submission_automation_failure"
        else:
            category = "unknown_generation_failure"
        result["failure_category"] = category
        result["causality"] = (
            "explicit_flow_signal"
            if category == "explicit_flow_failure"
            else "diagnostic_evidence_only"
        )
        return result

    @staticmethod
    def _candidate_media_id(candidate: CandidateRecord) -> str:
        explicit_id = candidate.candidate_id or ""
        if explicit_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", explicit_id):
            raise FlowAutomationError(
                f"Candidate v{candidate.variant:02d} has an invalid persisted data-media-id."
            )

        legacy_id = ""
        if candidate.asset_url:
            parts = urlsplit(candidate.asset_url)
            path_parts = parts.path.strip("/").split("/")
            if (
                parts.scheme == "https"
                and parts.netloc.lower() == "flow-content.google"
                and len(path_parts) == 2
                and path_parts[0] == "image"
                and re.fullmatch(r"[A-Za-z0-9_-]{8,100}", path_parts[1])
            ):
                legacy_id = path_parts[1]

        if explicit_id and legacy_id and explicit_id != legacy_id:
            raise FlowAutomationError(
                f"Candidate v{candidate.variant:02d} has conflicting persisted identities."
            )
        candidate_id = explicit_id or legacy_id
        if not candidate_id:
            raise FlowAutomationError(
                f"Candidate v{candidate.variant:02d} has no supported persisted data-media-id identity."
            )
        return candidate_id

    def _assert_download_context(self) -> None:
        inspection = self._assert_editor_available()
        current = inspection.current_url.rstrip("/")
        expected = (_safe_visible_url(self.flow_url) or "").rstrip("/")
        if not expected or current != expected:
            raise FlowAutomationError(
                "The attached Flow page does not match the configured project for this run."
            )

    def _exact_candidate_tile(
        self,
        candidate: CandidateRecord,
        *,
        reveal_virtualized_history: bool = False,
    ):
        candidate_id = self._candidate_media_id(candidate)
        image, tile = self._exact_candidate_image_tile(
            candidate_id,
            reveal_virtualized_history=reveal_virtualized_history,
        )
        return candidate_id, image, tile

    def _candidate_image_tile_probe(self, candidate_id: str) -> dict[str, Any]:
        """Atomically inspect UUID image/tile ownership without a chained locator."""
        page = self._require_page()
        raw = page.evaluate(
            r"""
            (candidateId) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                       style.visibility !== "hidden" && style.display !== "none";
              };
              const images = [...document.querySelectorAll("img[data-media-id]")]
                .filter((image) => image.getAttribute("data-media-id") === candidateId);
              const image = images.length === 1 ? images[0] : null;
              const tile = image ? image.closest("flow-image-tile") : null;
              return {
                image_match_count: images.length,
                image_connected: Boolean(image && image.isConnected),
                image_visible: Boolean(image && visible(image)),
                tile_found: Boolean(tile),
                tile_connected: Boolean(tile && tile.isConnected),
                tile_visible: Boolean(tile && visible(tile)),
                tile_owns_exact_image: Boolean(tile && image && tile.contains(image)),
              };
            }
            """,
            candidate_id,
        )
        if not isinstance(raw, dict):
            return {
                "image_match_count": 0,
                "image_connected": False,
                "image_visible": False,
                "tile_found": False,
                "tile_connected": False,
                "tile_visible": False,
                "tile_owns_exact_image": False,
            }
        return {
            "image_match_count": int(raw.get("image_match_count") or 0),
            "image_connected": bool(raw.get("image_connected")),
            "image_visible": bool(raw.get("image_visible")),
            "tile_found": bool(raw.get("tile_found")),
            "tile_connected": bool(raw.get("tile_connected")),
            "tile_visible": bool(raw.get("tile_visible")),
            "tile_owns_exact_image": bool(raw.get("tile_owns_exact_image")),
        }

    def _exact_candidate_image_tile(
        self,
        candidate_id: str,
        *,
        timeout_ms: int = 3_000,
        poll_interval_ms: int = 100,
        reveal_virtualized_history: bool = False,
    ):
        """Boundedly reacquire one visible UUID image and its current live tile."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", candidate_id):
            raise FlowAutomationError("Candidate data-media-id is invalid.")
        if not 100 <= timeout_ms <= 10_000:
            raise ValueError("Candidate reacquisition timeout must be 100 to 10000 ms.")
        if not 50 <= poll_interval_ms <= 500:
            raise ValueError("Candidate reacquisition poll interval must be 50 to 500 ms.")
        page = self._require_page()
        image_selector = f'img[data-media-id="{candidate_id}"]'
        tile_selector = (
            f'flow-image-tile:has(img[data-media-id="{candidate_id}"])'
        )
        deadline = time.monotonic() + timeout_ms / 1_000
        last_probe: dict[str, Any] | None = None
        last_error: Exception | None = None
        while True:
            cancellation_checkpoint("candidate_reacquisition_wait")
            try:
                probe = interruptible_call(
                    "candidate_reacquisition",
                    "candidate_dom_probe",
                    self._candidate_image_tile_probe,
                    candidate_id,
                )
                last_probe = probe
                ready = (
                    probe["image_match_count"] == 1
                    and probe["image_connected"]
                    and probe["image_visible"]
                    and probe["tile_found"]
                    and probe["tile_connected"]
                    and probe["tile_visible"]
                    and probe["tile_owns_exact_image"]
                )
                if ready:
                    images = interruptible_call(
                        "candidate_tile_reacquisition",
                        "candidate_image_locator_acquisition",
                        page.locator(image_selector).all,
                    )
                    tiles = interruptible_call(
                        "candidate_tile_reacquisition",
                        "candidate_tile_locator_acquisition",
                        page.locator(tile_selector).all,
                    )
                    if len(images) == 1 and len(tiles) == 1:
                        return images[0], tiles[0]
                    last_error = FlowAutomationError(
                        "Candidate DOM changed between the ownership probe and "
                        "fresh locator acquisition."
                    )
            except Exception as exc:
                cancellation_checkpoint("candidate_reacquisition")
                last_error = exc
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(poll_interval_ms)

        history_search: dict[str, Any] | None = None
        if (
            reveal_virtualized_history
            and last_probe is not None
            and last_probe.get("image_match_count") == 0
        ):
            history_search = interruptible_call(
                "candidate_history_search",
                "virtualized_candidate_history_search",
                self._reveal_virtualized_candidate_history,
                candidate_id,
            )
            if history_search.get("outcome") == "exact_candidate_revealed":
                try:
                    probe = interruptible_call(
                        "candidate_reacquisition",
                        "candidate_dom_probe_after_history_search",
                        self._candidate_image_tile_probe,
                        candidate_id,
                    )
                    last_probe = probe
                    ready = (
                        probe["image_match_count"] == 1
                        and probe["image_connected"]
                        and probe["image_visible"]
                        and probe["tile_found"]
                        and probe["tile_connected"]
                        and probe["tile_visible"]
                        and probe["tile_owns_exact_image"]
                    )
                    if ready:
                        images = interruptible_call(
                            "candidate_tile_reacquisition",
                            "candidate_image_locator_after_history_search",
                            page.locator(image_selector).all,
                        )
                        tiles = interruptible_call(
                            "candidate_tile_reacquisition",
                            "candidate_tile_locator_after_history_search",
                            page.locator(tile_selector).all,
                        )
                        if len(images) == 1 and len(tiles) == 1:
                            return images[0], tiles[0]
                        last_error = FlowAutomationError(
                            "Candidate DOM changed after virtualized-history reveal "
                            "and before fresh locator acquisition."
                        )
                except Exception as exc:
                    last_error = exc
                if self._candidate_history_recovery is not None:
                    history_search["original_view_restored"] = (
                        self._restore_candidate_history_view(candidate_id)
                    )

        facts = last_probe or {}
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        raise FlowAutomationError(
            "CANDIDATE_REACQUISITION_TIMEOUT: "
            f"candidate={candidate_id}; "
            f"image_match_count={facts.get('image_match_count', 0)}; "
            f"image_connected={bool(facts.get('image_connected'))}; "
            f"image_visible={bool(facts.get('image_visible'))}; "
            f"tile_found={bool(facts.get('tile_found'))}; "
            f"tile_connected={bool(facts.get('tile_connected'))}; "
            f"tile_visible={bool(facts.get('tile_visible'))}; "
            f"tile_owns_exact_image={bool(facts.get('tile_owns_exact_image'))}"
            + (
                "; history_search="
                + json.dumps(history_search, sort_keys=True, separators=(",", ":"))
                if history_search is not None
                else ""
            )
            + f"{detail}."
        )

    def _candidate_history_scroll_snapshot(
        self,
        candidate_id: str,
        *,
        target_scroll_top: int | float | None = None,
    ) -> dict[str, Any]:
        """Freshly identify and optionally move the scroll surface owning result tiles."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", candidate_id):
            raise FlowAutomationError("Candidate data-media-id is invalid.")
        if target_scroll_top is not None and (
            isinstance(target_scroll_top, bool)
            or not isinstance(target_scroll_top, (int, float))
            or not math.isfinite(float(target_scroll_top))
        ):
            raise ValueError("Candidate-history scroll target must be finite.")
        page = self._require_page()
        raw = page.evaluate(
            r"""
            ({candidateId, targetScrollTop}) => {
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  Number.parseFloat(style.opacity || '1') > 0.01;
              };
              const exactImages = [...document.querySelectorAll('img[data-media-id]')]
                .filter((image) => image.getAttribute('data-media-id') === candidateId);
              const exactImage = exactImages.length === 1 ? exactImages[0] : null;
              const exactTile = exactImage ? exactImage.closest('flow-image-tile') : null;
              const exactReady = Boolean(
                exactImage && exactTile && exactTile.contains(exactImage) &&
                exactImage.isConnected && exactTile.isConnected &&
                visible(exactImage) && visible(exactTile)
              );
              const tiles = [...document.querySelectorAll('flow-image-tile')]
                .filter((tile) => tile.isConnected &&
                  tile.querySelector('img[data-media-id]'));
              const scrollable = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const style = getComputedStyle(element);
                return /^(auto|scroll|overlay)$/.test(style.overflowY || '') &&
                  element.clientHeight > 0 && element.scrollHeight > element.clientHeight + 1;
              };
              const scores = new Map();
              for (const tile of tiles) {
                let depth = 0;
                for (let parent = tile.parentElement; parent; parent = parent.parentElement) {
                  depth += 1;
                  if (scrollable(parent)) {
                    const score = scores.get(parent) || {count: 0, depth: 0};
                    score.count += 1;
                    score.depth += depth;
                    scores.set(parent, score);
                  }
                }
              }
              let candidates = [...scores.entries()].map(([element, score]) => ({
                element,
                count: score.count,
                averageDepth: score.depth / Math.max(1, score.count),
              }));
              if (!candidates.length && tiles.length > 0) {
                const documentSurface = document.scrollingElement;
                if (documentSurface && documentSurface.clientHeight > 0 &&
                    documentSurface.scrollHeight > documentSurface.clientHeight + 1) {
                  candidates = [{
                    element: documentSurface,
                    count: tiles.length,
                    averageDepth: Number.MAX_SAFE_INTEGER,
                  }];
                }
              }
              candidates.sort((left, right) =>
                right.count - left.count || left.averageDepth - right.averageDepth
              );
              let surfaceStatus = 'unavailable';
              let selected = null;
              if (candidates.length) {
                const best = candidates[0];
                const second = candidates[1];
                if (second && second.count === best.count &&
                    Math.abs(second.averageDepth - best.averageDepth) < 0.001) {
                  surfaceStatus = 'ambiguous';
                } else {
                  surfaceStatus = 'ready';
                  selected = best;
                }
              }
              let surface = null;
              let moved = false;
              if (selected) {
                const element = selected.element;
                const registryKey = '__nanoBatchCandidateHistorySurfaceRegistryV1';
                let registry = window[registryKey];
                if (!registry || !(registry.nodes instanceof WeakMap)) {
                  registry = {nodes: new WeakMap(), next: 1};
                  window[registryKey] = registry;
                }
                if (!registry.nodes.has(element)) {
                  registry.nodes.set(element, `candidate-history-surface-${registry.next++}`);
                }
                if (typeof targetScrollTop === 'number' && Number.isFinite(targetScrollTop)) {
                  const maximum = Math.max(0, element.scrollHeight - element.clientHeight);
                  const bounded = Math.min(maximum, Math.max(0, targetScrollTop));
                  const before = element.scrollTop;
                  element.scrollTop = bounded;
                  moved = Math.abs(element.scrollTop - before) > 0.5;
                }
                const testId = element.getAttribute('data-testid') ||
                  element.getAttribute('data-test-id') || '';
                surface = {
                  kind: element === document.scrollingElement
                    ? 'document_scrolling_element' : 'tile_scroll_ancestor',
                  tag: element.tagName.toLowerCase(),
                  role: element.getAttribute('role') || '',
                  id: element.id || '',
                  data_testid: testId,
                  token: registry.nodes.get(element),
                  connected: element.isConnected,
                  scroll_top: element.scrollTop,
                  scroll_height: element.scrollHeight,
                  client_height: element.clientHeight,
                  max_scroll_top: Math.max(0, element.scrollHeight - element.clientHeight),
                };
              }
              return {
                exact_match_count: exactImages.length,
                exact_ready: exactReady,
                mounted_candidate_tile_count: tiles.length,
                surface_status: surfaceStatus,
                surface_candidate_count: selected ? selected.count : 0,
                surface,
                requested_scroll_top: typeof targetScrollTop === 'number'
                  ? targetScrollTop : null,
                moved,
              };
            }
            """,
            {
                "candidateId": candidate_id,
                "targetScrollTop": target_scroll_top,
            },
        )
        return _normalize_candidate_history_snapshot(raw)

    @staticmethod
    def _candidate_history_search_positions(
        snapshot: dict[str, Any],
        *,
        limit: int,
        reference_scroll_top: int | float,
    ) -> list[float]:
        surface = snapshot.get("surface")
        if not isinstance(surface, dict):
            return []
        maximum = surface.get("max_scroll_top")
        client_height = surface.get("client_height")
        if not isinstance(maximum, (int, float)) or maximum <= 0:
            return []
        if not isinstance(client_height, (int, float)) or client_height <= 0:
            return []
        stride = max(1.0, float(client_height) * 0.8)
        required = max(2, int(math.ceil(float(maximum) / stride)) + 1)
        count = min(max(2, int(limit) + 1), required)
        positions = [
            float(maximum) * index / max(1, count - 1)
            for index in range(count)
        ]
        reference = min(float(maximum), max(0.0, float(reference_scroll_top)))
        return sorted(
            positions,
            key=lambda position: (abs(position - reference), position),
        )

    @staticmethod
    def _candidate_history_surface_metrics(
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        surface = snapshot.get("surface")
        if not isinstance(surface, dict):
            return None
        return {
            key: surface.get(key)
            for key in (
                "kind",
                "tag",
                "role",
                "id",
                "data_testid",
                "token",
                "scroll_top",
                "scroll_height",
                "client_height",
                "max_scroll_top",
            )
        }

    def _restore_candidate_history_view(
        self,
        candidate_id: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Best-effort scroll restoration; inability to restore never masks download state."""
        recovery = context if context is not None else self._candidate_history_recovery
        if context is None:
            self._candidate_history_recovery = None
        if not isinstance(recovery, dict) or recovery.get("candidate_id") != candidate_id:
            return False
        original = recovery.get("original_scroll_top")
        if not isinstance(original, (int, float)):
            return False
        try:
            snapshot = self._candidate_history_scroll_snapshot(
                candidate_id,
                target_scroll_top=original,
            )
            surface = snapshot.get("surface")
            restored = bool(
                isinstance(surface, dict)
                and isinstance(surface.get("scroll_top"), (int, float))
                and abs(float(surface["scroll_top"]) - float(original)) <= 1.0
            )
        except Exception:
            cancellation_checkpoint("download_cleanup")
            restored = False
        return restored

    def _reveal_virtualized_candidate_history(
        self,
        candidate_id: str,
        *,
        max_steps: int = _CANDIDATE_HISTORY_SEARCH_MAX_STEPS,
        timeout_seconds: float = _CANDIDATE_HISTORY_SEARCH_TIMEOUT_SECONDS,
        settle_ms: int = _CANDIDATE_HISTORY_RENDER_SETTLE_MS,
    ) -> dict[str, Any]:
        """Boundedly scroll the semantic result surface until the exact UUID mounts."""
        if not 1 <= int(max_steps) <= _CANDIDATE_HISTORY_SEARCH_MAX_STEPS:
            raise ValueError("Candidate-history reveal step limit is out of range.")
        if not 0.1 <= float(timeout_seconds) <= _CANDIDATE_HISTORY_SEARCH_TIMEOUT_SECONDS:
            raise ValueError("Candidate-history reveal timeout is out of range.")
        if not 25 <= int(settle_ms) <= 500:
            raise ValueError("Candidate-history render settle must be 25 to 500 ms.")

        page = self._require_page()
        window_recovery = self.ensure_production_window_usable(
            reason="startup_nonmaximized",
            candidate_id=candidate_id,
        )
        identity = _flow_project_identity(
            page.url if isinstance(getattr(page, "url", None), str) else None
        )
        initial = interruptible_call(
            "candidate_history_surface_wait",
            "candidate_history_initial_snapshot",
            self._candidate_history_scroll_snapshot,
            candidate_id,
        )
        surface = initial.get("surface")
        original_scroll_top = (
            surface.get("scroll_top") if isinstance(surface, dict) else None
        )
        context = {
            "candidate_id": candidate_id,
            "original_scroll_top": original_scroll_top,
            "surface": self._candidate_history_surface_metrics(initial),
        }
        stages: list[dict[str, Any]] = []
        tokens: list[str] = []
        directions: list[str] = []

        def record(snapshot: dict[str, Any], *, step: int, direction: str) -> None:
            current_surface = snapshot.get("surface")
            token = (
                current_surface.get("token")
                if isinstance(current_surface, dict)
                else None
            )
            if isinstance(token, str) and token not in tokens:
                tokens.append(token)
            if direction not in {"initial", "settle"} and direction not in directions:
                directions.append(direction)
            stages.append(
                {
                    "stage": len(stages),
                    "reveal_step": step,
                    "direction": direction,
                    "exact_match_count": snapshot.get("exact_match_count", 0),
                    "exact_ready": bool(snapshot.get("exact_ready")),
                    "surface_status": snapshot.get("surface_status"),
                    "scroll_top": (
                        current_surface.get("scroll_top")
                        if isinstance(current_surface, dict)
                        else None
                    ),
                    "surface_token": token,
                }
            )

        record(initial, step=0, direction="initial")
        deadline = time.monotonic() + float(timeout_seconds)
        current = initial
        discovery_polls = 0
        while (
            current.get("surface_status") != "ready"
            and discovery_polls < 4
            and time.monotonic() < deadline
        ):
            cancellation_checkpoint("candidate_history_surface_wait")
            interruptible_call(
                "candidate_history_surface_wait",
                "candidate_history_surface_settle",
                page.wait_for_timeout,
                int(settle_ms),
            )
            current = interruptible_call(
                "candidate_history_surface_wait",
                "candidate_history_surface_snapshot",
                self._candidate_history_scroll_snapshot,
                candidate_id,
            )
            discovery_polls += 1
            record(current, step=0, direction="settle")
            if current.get("exact_ready"):
                break

        visited: set[int] = set()
        if isinstance(original_scroll_top, (int, float)):
            visited.add(round(float(original_scroll_top)))
        steps = 0
        outcome = "search_exhausted"
        if current.get("exact_ready") and current.get("exact_match_count") == 1:
            outcome = "exact_candidate_revealed"
        elif current.get("exact_match_count", 0) > 1:
            outcome = "ambiguous_exact_uuid"

        while (
            outcome == "search_exhausted"
            and steps < int(max_steps)
            and time.monotonic() < deadline
        ):
            cancellation_checkpoint("candidate_history_search")
            current_surface = current.get("surface")
            if not isinstance(current_surface, dict):
                break
            reference = (
                original_scroll_top
                if isinstance(original_scroll_top, (int, float))
                else current_surface.get("scroll_top", 0)
            )
            positions = self._candidate_history_search_positions(
                current,
                limit=int(max_steps),
                reference_scroll_top=float(reference or 0),
            )
            target = next(
                (position for position in positions if round(position) not in visited),
                None,
            )
            if target is None:
                break
            visited.add(round(target))
            before_top = current_surface.get("scroll_top")
            moved_snapshot = interruptible_call(
                "candidate_history_search",
                "candidate_history_scroll_move",
                self._candidate_history_scroll_snapshot,
                candidate_id,
                target_scroll_top=target,
            )
            steps += 1
            after_surface = moved_snapshot.get("surface")
            after_top = (
                after_surface.get("scroll_top")
                if isinstance(after_surface, dict)
                else None
            )
            direction = "stationary"
            if isinstance(before_top, (int, float)) and isinstance(after_top, (int, float)):
                if float(after_top) < float(before_top) - 0.5:
                    direction = "toward_start"
                elif float(after_top) > float(before_top) + 0.5:
                    direction = "toward_end"
            interruptible_call(
                "candidate_history_search",
                "candidate_history_render_settle",
                page.wait_for_timeout,
                int(settle_ms),
            )
            current = interruptible_call(
                "candidate_history_search",
                "candidate_history_post_scroll_snapshot",
                self._candidate_history_scroll_snapshot,
                candidate_id,
            )
            record(current, step=steps, direction=direction)
            remount_polls = 0
            while (
                (
                    current.get("surface_status") != "ready"
                    or (
                        current.get("exact_match_count") == 1
                        and not current.get("exact_ready")
                    )
                )
                and remount_polls < 3
                and time.monotonic() < deadline
            ):
                page.wait_for_timeout(int(settle_ms))
                current = self._candidate_history_scroll_snapshot(candidate_id)
                remount_polls += 1
                record(current, step=steps, direction="settle")
            if current.get("exact_match_count", 0) > 1:
                outcome = "ambiguous_exact_uuid"
                break
            if current.get("exact_match_count") == 1 and current.get("exact_ready"):
                outcome = "exact_candidate_revealed"
                break

        if outcome == "search_exhausted" and time.monotonic() >= deadline:
            outcome = "time_budget_exhausted"
        final_metrics = self._candidate_history_surface_metrics(current)
        diagnostics = {
            "schema": "nano-banana-candidate-history-recovery/v1",
            "candidate_id": candidate_id,
            "project_uuid": identity.get("project_id"),
            "window": (
                window_recovery.get("after")
                if isinstance(window_recovery, dict)
                else {"window_state": "unknown", "viewport": {"width": None, "height": None}}
            ),
            "scroll_surface": (
                final_metrics or self._candidate_history_surface_metrics(initial)
            ),
            "initial_scroll": self._candidate_history_surface_metrics(initial),
            "final_scroll": final_metrics,
            "reveal_steps": steps,
            "max_reveal_steps": int(max_steps),
            "timeout_seconds": float(timeout_seconds),
            "settle_ms": int(settle_ms),
            "directions_searched": directions,
            "stages": stages,
            "result_container_remounted": len(tokens) > 1,
            "result_container_tokens_seen": len(tokens),
            "outcome": outcome,
            "original_view_restored": False,
        }
        if outcome == "exact_candidate_revealed":
            if self._candidate_history_recovery is None:
                self._candidate_history_recovery = context
        else:
            diagnostics["original_view_restored"] = self._restore_candidate_history_view(
                candidate_id,
                context,
            )
        return diagnostics

    def _current_candidate_tile_center(self, candidate_id: str) -> dict[str, float]:
        """Scroll and measure the current UUID tile through a fresh page-level query."""
        page = self._require_page()
        raw = page.evaluate(
            r"""
            (candidateId) => {
              const images = [...document.querySelectorAll("img[data-media-id]")]
                .filter((image) => image.getAttribute("data-media-id") === candidateId);
              if (images.length !== 1 || !images[0].isConnected) return null;
              const tile = images[0].closest("flow-image-tile");
              if (!tile || !tile.isConnected || !tile.contains(images[0])) return null;
              tile.scrollIntoView({block: "center", inline: "center"});
              const rect = tile.getBoundingClientRect();
              const style = getComputedStyle(tile);
              if (rect.width <= 0 || rect.height <= 0 ||
                  style.visibility === "hidden" || style.display === "none") return null;
              return {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
              };
            }
            """,
            candidate_id,
        )
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("x"), (int, float))
            or not isinstance(raw.get("y"), (int, float))
        ):
            raise FlowAutomationError(
                "CANDIDATE_REACQUISITION_NOT_READY: exact candidate tile could "
                "not be measured after reacquisition."
            )
        return {"x": float(raw["x"]), "y": float(raw["y"])}

    def _reacquire_candidate_tile_for_reveal(
        self,
        candidate_id: str,
        *,
        timeout_ms: int = 4_000,
    ):
        """Wait for one current UUID tile that remains valid through measurement."""
        if not 500 <= timeout_ms <= 10_000:
            raise ValueError("Candidate reveal timeout must be 500 to 10000 ms.")
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error: Exception | None = None
        while True:
            cancellation_checkpoint("candidate_reveal_wait")
            try:
                image, tile = self._exact_candidate_image_tile(
                    candidate_id,
                    timeout_ms=500,
                    poll_interval_ms=50,
                )
                center = self._current_candidate_tile_center(candidate_id)
                return image, tile, center
            except Exception as exc:
                cancellation_checkpoint("candidate_tile_reacquisition")
                last_error = exc
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(100)
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        raise FlowAutomationError(
            "CANDIDATE_REACQUISITION_TIMEOUT: exact candidate image/tile did "
            f"not remain valid through toolbar reveal; candidate={candidate_id}"
            f"{detail}."
        )

    @staticmethod
    def _inspect_download_submenu_dom(page) -> dict[str, Any]:
        """Read visible ARIA menu structure without moving, focusing, or activating input."""
        raw = page.evaluate(
            r"""
            () => {
              const safeText = (raw, limit = 180) => String(raw || "")
                .replace(/\bGoogle Account\b/gi, "[redacted account control]")
                .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
                .replace(/\s+/g, " ").trim().slice(0, limit) || null;
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" &&
                       style.display !== "none";
              };
              const box = (element) => {
                const rect = element.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const iconFacts = (item) => [...item.querySelectorAll(
                "mat-icon,[role='img'],svg,.material-icons,.material-symbols-outlined,.material-symbols-rounded"
              )].filter(visible).slice(0, 8).map((icon) => ({
                tag: icon.tagName.toLowerCase(),
                role: icon.getAttribute("role"),
                text: safeText(icon.textContent, 80),
                aria_label: safeText(icon.getAttribute("aria-label"), 80),
                title: safeText(icon.getAttribute("title"), 80),
              }));
              const menuNodes = [...document.querySelectorAll("[role='menu']")].filter(visible);
              const menus = menuNodes.map((menu, menuIndex) => {
                const items = [...menu.querySelectorAll("[role='menuitem']")]
                  .filter((item) => item.closest("[role='menu']") === menu && visible(item))
                  .map((item, itemIndex) => ({
                    index: itemIndex,
                    tag: item.tagName.toLowerCase(),
                    role: item.getAttribute("role"),
                    visible_text: safeText(item.innerText || item.textContent),
                    aria_label: safeText(item.getAttribute("aria-label"), 120),
                    title: safeText(item.getAttribute("title"), 120),
                    disabled: Boolean(item.disabled),
                    aria_disabled: safeText(item.getAttribute("aria-disabled"), 20),
                    tabindex: safeText(item.getAttribute("tabindex"), 20),
                    aria_haspopup: safeText(item.getAttribute("aria-haspopup"), 40),
                    aria_expanded: safeText(item.getAttribute("aria-expanded"), 20),
                    connected: item.isConnected,
                    visible: visible(item),
                    bounding_box: box(item),
                    icons: iconFacts(item),
                  }));
                return {
                  index: menuIndex,
                  tag: menu.tagName.toLowerCase(),
                  role: menu.getAttribute("role"),
                  visible_text: safeText(menu.innerText || menu.textContent),
                  aria_label: safeText(menu.getAttribute("aria-label"), 120),
                  title: safeText(menu.getAttribute("title"), 120),
                  connected: menu.isConnected,
                  visible: visible(menu),
                  bounding_box: box(menu),
                  items,
                };
              });
              const compact = (element) => String(
                element.getAttribute("aria-label") || element.innerText || element.textContent || ""
              ).toLowerCase().replace(/[^a-z]+/g, "");
              const isDownloadParent = (element) => {
                const label = compact(element);
                return label === "download" || label === "downloaddownload" ||
                  /^downloaddownload(?:chevronright|arrowright|arrowdropright|keyboardarrowright|navigatenext)$/.test(label);
              };
              const parentMatches = [];
              menuNodes.forEach((menu, menuIndex) => {
                [...menu.querySelectorAll("[role='menuitem']")]
                  .filter((item) => item.closest("[role='menu']") === menu && visible(item))
                  .forEach((item, itemIndex) => {
                    if (isDownloadParent(item)) {
                      parentMatches.push({menu, menuIndex, item, itemIndex});
                    }
                  });
              });
              const downloadParent = {
                match_count: parentMatches.length,
                parent_menu_index: null,
                parent_item_index: null,
                aria_haspopup: null,
                aria_expanded: null,
                has_aria_controls: false,
                has_aria_owns: false,
                submenu_indicator: [],
                relationship: {status: "absent", basis: [], submenu_menu_indices: []},
              };
              let submenuDetected = false;
              if (parentMatches.length === 1) {
                const parent = parentMatches[0];
                const item = parent.item;
                const icons = iconFacts(item);
                downloadParent.parent_menu_index = parent.menuIndex;
                downloadParent.parent_item_index = parent.itemIndex;
                downloadParent.aria_haspopup = safeText(item.getAttribute("aria-haspopup"), 40);
                downloadParent.aria_expanded = safeText(item.getAttribute("aria-expanded"), 20);
                downloadParent.has_aria_controls = item.hasAttribute("aria-controls");
                downloadParent.has_aria_owns = item.hasAttribute("aria-owns");
                downloadParent.submenu_indicator = icons;
                const linked = new Set();
                const basis = new Set();
                const controls = new Set(String(item.getAttribute("aria-controls") || "").split(/\s+/).filter(Boolean));
                const owns = new Set(String(item.getAttribute("aria-owns") || "").split(/\s+/).filter(Boolean));
                menuNodes.forEach((menu, index) => {
                  if (index === parent.menuIndex) return;
                  if (menu.id && controls.has(menu.id)) { linked.add(index); basis.add("aria_controls"); }
                  if (menu.id && owns.has(menu.id)) { linked.add(index); basis.add("aria_owns"); }
                  if (item.id && menu.getAttribute("aria-labelledby") === item.id) {
                    linked.add(index); basis.add("aria_labelledby");
                  }
                  if (item.contains(menu)) { linked.add(index); basis.add("dom_descendant"); }
                });
                if (linked.size) {
                  downloadParent.relationship = {
                    status: "explicit", basis: [...basis], submenu_menu_indices: [...linked],
                  };
                  submenuDetected = true;
                } else {
                  const siblings = menuNodes.map((_, index) => index)
                    .filter((index) => index !== parent.menuIndex);
                  const indicatorText = icons.map((icon) =>
                    `${icon.text || ""} ${icon.aria_label || ""} ${icon.title || ""}`
                  ).join(" ");
                  const hasIndicator = Boolean(downloadParent.aria_haspopup) ||
                    /chevron|arrow|navigate.?next|right/i.test(indicatorText);
                  if (siblings.length && hasIndicator) {
                    downloadParent.relationship = {
                      status: "inferred_visible_sibling",
                      basis: ["visible_sibling_with_submenu_indicator"],
                      submenu_menu_indices: siblings,
                    };
                    submenuDetected = true;
                  } else {
                    downloadParent.relationship = {
                      status: "unresolved", basis: [], submenu_menu_indices: [],
                    };
                  }
                }
              }
              return {
                visible_menu_count: menus.length,
                menus,
                download_parent: downloadParent,
                submenu_detected: submenuDetected,
              };
            }
            """
        )
        return _normalize_download_submenu_snapshot(raw)

    def _wait_for_candidate_more_options_snackbar_clearance(
        self,
        candidate_id: str,
        *,
        timeout_ms: int = 4_000,
        poll_interval_ms: int = 100,
    ):
        """Wait only for a proven Material snackbar to stop intercepting More options."""
        if not 500 <= timeout_ms <= 8_000:
            raise ValueError("Snackbar occlusion wait must be 500 to 8000 ms.")
        if not 50 <= poll_interval_ms <= 200:
            raise ValueError("Snackbar occlusion polling must be 50 to 200 ms.")

        page = self._require_page()
        started = time.monotonic()
        deadline = started + timeout_ms / 1_000
        poll_count = 0
        candidate_reacquisition_count = 0
        control_reacquisition_count = 0
        last_error: Exception | None = None
        last_hit_diagnostic: str | None = None

        while True:
            cancellation_checkpoint("candidate_snackbar_wait")
            poll_count += 1
            current_snapshot: dict[str, Any] | None = None
            try:
                candidate_reacquisition_count += 1
                _image, _tile, position = self._reacquire_candidate_tile_for_reveal(
                    candidate_id,
                    timeout_ms=500,
                )
                page.mouse.move(float(position["x"]), float(position["y"]))
                page.wait_for_timeout(50)
                _image, current_tile = self._exact_candidate_image_tile(
                    candidate_id,
                    timeout_ms=500,
                    poll_interval_ms=50,
                )
                matches = current_tile.get_by_role(
                    "button",
                    name="More options",
                ).all()
                visible = []
                for control in matches:
                    try:
                        facts = self._live_control_facts(control)
                    except Exception:
                        cancellation_checkpoint("candidate_tile_interaction")
                        continue
                    if facts["connected"] and facts["visible"]:
                        visible.append(control)
                if len(visible) > 1:
                    raise FlowAutomationError(
                        f"Candidate {candidate_id} More options control is ambiguous; "
                        f"found {len(visible)} visible matches during snackbar wait."
                    )
                if len(visible) != 1:
                    raise FlowAutomationError(
                        "CANDIDATE_MORE_OPTIONS_NOT_PRESENT: exact visible control is "
                        "temporarily unavailable during snackbar wait."
                    )

                control = visible[0]
                control_reacquisition_count += 1
                current_snapshot = self._inspect_candidate_more_options(
                    control,
                    candidate_id,
                    sample_count=2,
                    sample_interval_ms=50,
                )
                last_hit_diagnostic = _candidate_more_options_hit_diagnostic(
                    current_snapshot
                )
                stable_box = _stable_candidate_more_options_box(current_snapshot)
                safe_point = _candidate_more_options_safe_point(
                    current_snapshot,
                    stable_box,
                )
                current_box = self._current_candidate_more_options_hit(
                    control,
                    candidate_id,
                    stable_box,
                    preferred_probe=safe_point["name"],
                    diagnostic_snapshot=current_snapshot,
                )
                elapsed_ms = round((time.monotonic() - started) * 1_000, 3)
                current_snapshot.update(
                    {
                        "transient_overlay_type": "snackbar",
                        "snackbar_wait_entered": True,
                        "snackbar_wait_ms": elapsed_ms,
                        "snackbar_poll_count": poll_count,
                        "snackbar_disappeared": int(
                            current_snapshot.get("visible_material_snackbar_count") or 0
                        )
                        == 0,
                        "snackbar_stopped_intersecting": True,
                        "snackbar_candidate_reacquisition_count": (
                            candidate_reacquisition_count
                        ),
                        "snackbar_control_reacquisition_count": (
                            control_reacquisition_count
                        ),
                    }
                )
                return control, current_snapshot, current_box
            except FlowAutomationError as exc:
                cancellation_checkpoint("candidate_snackbar_wait")
                last_error = exc
                message = str(exc)
                if current_snapshot is not None:
                    last_hit_diagnostic = _candidate_more_options_hit_diagnostic(
                        current_snapshot
                    )
                if "ambiguous" in message:
                    raise
                if "CANDIDATE_MORE_OPTIONS_OVERLAID" in message:
                    if current_snapshot is None or not (
                        _candidate_more_options_has_material_snackbar_occlusion(
                            current_snapshot
                        )
                    ):
                        # A snackbar wait never grants arbitrary overlays extra privilege.
                        raise
                elif not any(
                    code in message
                    for code in (
                        "CANDIDATE_REACQUISITION",
                        "CANDIDATE_MORE_OPTIONS_NOT_PRESENT",
                        "CANDIDATE_MORE_OPTIONS_HIDDEN",
                        "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE",
                        "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE",
                        "CANDIDATE_MORE_OPTIONS_WRONG_TILE",
                    )
                ):
                    raise
            except Exception as exc:
                cancellation_checkpoint("candidate_snackbar_wait")
                last_error = exc

            now = time.monotonic()
            if now >= deadline:
                elapsed_ms = round((now - started) * 1_000, 3)
                hit_detail = (
                    f"; hit_diagnostic={last_hit_diagnostic}"
                    if last_hit_diagnostic
                    else ""
                )
                error_detail = (
                    f"; last_check={_safe_trace_header(str(last_error))}"
                    if last_error is not None
                    else ""
                )
                raise FlowAutomationError(
                    "CANDIDATE_MORE_OPTIONS_OVERLAID: proven visible Material snackbar "
                    "continued to intercept the exact More options hit area; "
                    "transient_overlay_type=snackbar; snackbar_wait_entered=true; "
                    f"snackbar_wait_ms={elapsed_ms}; snackbar_poll_count={poll_count}; "
                    "snackbar_disappeared=false; snackbar_stopped_intersecting=false; "
                    "snackbar_candidate_reacquisition_count="
                    f"{candidate_reacquisition_count}; "
                    "snackbar_control_reacquisition_count="
                    f"{control_reacquisition_count}{hit_detail}{error_detail}."
                )
            page.wait_for_timeout(poll_interval_ms)

    def _candidate_more_options_failure_layout_detail(self, candidate_id: str) -> str:
        """Best-effort safe layout context for the terminal missing-control error."""
        window_state = self._exact_window_state()
        snapshot: dict[str, Any] = {}
        try:
            snapshot = self._download_layout_dom_snapshot(
                candidate_limit=1,
                target_candidate_id=candidate_id,
            )
        except Exception:
            cancellation_checkpoint("candidate_toolbar_diagnostics")
            # Diagnostic collection must never replace the established failure.
            pass
        window = snapshot.get("browser_window") or {}
        target = snapshot.get("target_candidate")

        def dimensions(width_key: str, height_key: str) -> str:
            width = window.get(width_key)
            height = window.get(height_key)
            return f"{width}x{height}" if width is not None and height is not None else "unknown"

        bounding_box = target.get("bounding_box") if isinstance(target, dict) else None
        if isinstance(bounding_box, dict):
            box_value = ",".join(
                str(bounding_box.get(key)) for key in ("x", "y", "width", "height")
            )
        else:
            box_value = "unknown"
        more_options_count = (
            target.get("more_options_count") if isinstance(target, dict) else "unknown"
        )
        return (
            f"; window_state={window_state}"
            f"; outer={dimensions('outer_width', 'outer_height')}"
            f"; inner={dimensions('inner_width', 'inner_height')}"
            f"; viewport={dimensions('viewport_width', 'viewport_height')}"
            f"; candidate_bbox={box_value}"
            f"; more_options_count={more_options_count}"
        )

    def _recover_candidate_more_options_after_window_normalization(
        self,
        candidate_id: str,
        *,
        reason: str,
        timeout_ms: int = 2_000,
    ) -> tuple[Any | None, dict[str, Any], dict[str, Any], str]:
        """Normalize once, then reacquire only this UUID's freshly rendered control."""
        if reason not in {
            "download_control_zero_geometry",
            "download_control_absent",
        }:
            raise ValueError("Unsupported candidate-toolbar recovery reason.")
        if not 500 <= timeout_ms <= 3_000:
            raise ValueError("Candidate-toolbar recovery timeout must be 500-3000 ms.")

        page = self._require_page()
        recovery = self.ensure_production_window_usable(
            reason=reason,
            candidate_id=candidate_id,
        )
        deadline = time.monotonic() + timeout_ms / 1_000
        last_facts: dict[str, Any] = {}
        last_outcome = "control_not_present"
        while True:
            cancellation_checkpoint("candidate_toolbar_recovery")
            try:
                _image, tile, position = self._reacquire_candidate_tile_for_reveal(
                    candidate_id,
                    timeout_ms=500,
                )
                page.mouse.move(float(position["x"]), float(position["y"]))
                _image, tile = self._exact_candidate_image_tile(
                    candidate_id,
                    timeout_ms=500,
                    poll_interval_ms=50,
                )
                matches = tile.get_by_role("button", name="More options").all()
                if len(matches) > 1:
                    raise FlowAutomationError(
                        f"Candidate {candidate_id} More options control is ambiguous; "
                        f"found {len(matches)} semantic matches in the exact tile after "
                        "window normalization."
                    )
                if len(matches) == 1:
                    facts = self._live_control_facts(matches[0])
                    last_facts = facts
                    box = facts.get("bounding_box")
                    has_geometry = bool(
                        isinstance(box, dict)
                        and float(box.get("width", 0)) > 0
                        and float(box.get("height", 0)) > 0
                    )
                    if (
                        facts.get("connected")
                        and facts.get("visible")
                        and facts.get("enabled")
                        and has_geometry
                    ):
                        return matches[0], facts, recovery, "control_actionable"
                    last_outcome = (
                        "control_zero_geometry"
                        if facts.get("connected")
                        and facts.get("enabled")
                        and not has_geometry
                        else "control_present_but_hidden"
                    )
                else:
                    last_facts = {}
                    last_outcome = "control_not_present"
            except FlowAutomationError as exc:
                cancellation_checkpoint("candidate_toolbar_recovery")
                if "ambiguous" in str(exc):
                    raise
                last_facts = {"reacquisition_error": _safe_trace_header(str(exc))}
                last_outcome = "control_replaced_or_unstable"
            except Exception as exc:
                cancellation_checkpoint("candidate_toolbar_recovery")
                last_facts = {"reacquisition_error": _safe_trace_header(str(exc))}
                last_outcome = "control_replaced_or_unstable"
            if time.monotonic() >= deadline:
                return None, last_facts, recovery, last_outcome
            page.wait_for_timeout(50)

    def _reveal_candidate_options(self, candidate_id: str, image, tile):
        """Boundedly reveal and reacquire stable More options in the exact UUID tile."""
        page = self._require_page()
        del image, tile
        last_error: Exception | None = None
        last_hit_diagnostic: str | None = None
        last_outcome = "control_not_present"
        prior_reveal_outcomes: list[str] = []
        layout_recovery_reason: str | None = None
        reveal_attempts = 3
        for reveal_attempt in range(1, reveal_attempts + 1):
            layout_recovery_reason = None
            current_snapshot: dict[str, Any] | None = None
            try:
                _current_image, current_tile, position = (
                    self._reacquire_candidate_tile_for_reveal(
                        candidate_id,
                        timeout_ms=1_200,
                    )
                )
                page.mouse.move(float(position["x"]), float(position["y"]))
                # Check the freshly reacquired exact tile immediately. If Flow still
                # needs its former 100 ms paint opportunity, retain it as two bounded
                # 50 ms polls instead of imposing it on an already-ready toolbar.
                for paint_poll in range(3):
                    _current_image, current_tile = self._exact_candidate_image_tile(
                        candidate_id,
                        timeout_ms=500,
                        poll_interval_ms=50,
                    )
                    matches = current_tile.get_by_role(
                        "button",
                        name="More options",
                    ).all()
                    visible = []
                    present_facts = []
                    for control in matches:
                        try:
                            facts = self._live_control_facts(control)
                        except Exception:
                            cancellation_checkpoint("candidate_tile_interaction")
                            continue
                        present_facts.append(facts)
                        if facts["connected"] and facts["visible"]:
                            visible.append(control)
                    if len(visible) > 1:
                        raise FlowAutomationError(
                            f"Candidate {candidate_id} More options control is ambiguous; "
                            f"found {len(visible)} visible matches in the exact tile."
                        )
                    if len(visible) == 1:
                        break
                    if paint_poll < 2:
                        page.wait_for_timeout(50)
                if matches and not visible:
                    last_outcome = "control_present_but_hidden"
                    facts = present_facts[0] if len(present_facts) == 1 else {}
                    box = facts.get("bounding_box")
                    if (
                        len(matches) == 1
                        and facts.get("connected")
                        and facts.get("enabled")
                        and isinstance(box, dict)
                        and (
                            float(box.get("width", 0)) <= 0
                            or float(box.get("height", 0)) <= 0
                        )
                    ):
                        layout_recovery_reason = "download_control_zero_geometry"
                    last_error = FlowAutomationError(
                        "CANDIDATE_MORE_OPTIONS_HIDDEN: semantic control exists but is "
                        "not visibly painted; "
                        f"connected={bool(facts.get('connected'))}; "
                        f"opacity={facts.get('opacity') or '(unreadable)'}; "
                        f"visibility={facts.get('visibility') or '(unreadable)'}; "
                        f"pointer_events={facts.get('pointer_events') or '(unreadable)'}."
                    )
                    continue
                if not matches:
                    last_outcome = "control_not_present"
                    layout_recovery_reason = "download_control_absent"
                    last_error = FlowAutomationError(
                        "CANDIDATE_MORE_OPTIONS_NOT_PRESENT: semantic control is absent "
                        "from the exact candidate tile after reveal."
                    )
                    continue
                if len(visible) == 1:
                    control = visible[0]
                    snapshot = self._inspect_candidate_more_options(
                        control,
                        candidate_id,
                    )
                    current_snapshot = snapshot
                    last_hit_diagnostic = _candidate_more_options_hit_diagnostic(snapshot)
                    stable_box = _stable_candidate_more_options_box(snapshot)
                    safe_point = _candidate_more_options_safe_point(
                        snapshot,
                        stable_box,
                    )
                    current_box = self._current_candidate_more_options_hit(
                        control,
                        candidate_id,
                        stable_box,
                        preferred_probe=safe_point["name"],
                    )
                    snapshot["reveal_attempt"] = reveal_attempt
                    snapshot["prior_reveal_outcomes"] = list(prior_reveal_outcomes)
                    return control, snapshot, current_box
            except FlowAutomationError as exc:
                cancellation_checkpoint("candidate_tile_interaction")
                if "ambiguous" in str(exc):
                    raise
                last_error = exc
                message = str(exc)
                if "CANDIDATE_MORE_OPTIONS_WRONG_TILE" in message:
                    last_outcome = "tile_replaced_during_reveal"
                elif "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE" in message:
                    last_outcome = "control_replaced_or_unstable"
                elif "CANDIDATE_MORE_OPTIONS_HIDDEN" in message:
                    last_outcome = "control_present_but_hidden"
                elif "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE" in message:
                    last_outcome = "control_present_not_actionable"
                elif (
                    "CANDIDATE_MORE_OPTIONS_OVERLAID" in message
                    and current_snapshot is not None
                    and _candidate_more_options_all_topmost(current_snapshot, "img")
                ):
                    last_outcome = "image_topmost"
                elif (
                    "CANDIDATE_MORE_OPTIONS_OVERLAID" in message
                    and current_snapshot is not None
                    and _candidate_more_options_has_material_snackbar_occlusion(
                        current_snapshot
                    )
                ):
                    control, snapshot, current_box = (
                        self._wait_for_candidate_more_options_snackbar_clearance(
                            candidate_id,
                            timeout_ms=4_000,
                            poll_interval_ms=100,
                        )
                    )
                    snapshot["reveal_attempt"] = reveal_attempt
                    snapshot["prior_reveal_outcomes"] = [
                        *prior_reveal_outcomes,
                        "snackbar_occlusion_wait",
                    ]
                    return control, snapshot, current_box
                elif "CANDIDATE_MORE_OPTIONS_OVERLAID" in message:
                    last_outcome = "hit_test_ownership_not_established"
            except Exception as exc:
                cancellation_checkpoint("candidate_tile_interaction")
                last_error = exc
                last_outcome = "control_replaced_or_unstable"
            prior_reveal_outcomes.append(last_outcome)
            if reveal_attempt < reveal_attempts:
                page.wait_for_timeout(100)
        if layout_recovery_reason is not None:
            control, recovery_facts, recovery, recovery_outcome = (
                self._recover_candidate_more_options_after_window_normalization(
                    candidate_id,
                    reason=layout_recovery_reason,
                )
            )
            prior_reveal_outcomes.append(
                f"window_normalization:{layout_recovery_reason}"
            )
            if control is not None:
                snapshot = self._inspect_candidate_more_options(control, candidate_id)
                last_hit_diagnostic = _candidate_more_options_hit_diagnostic(snapshot)
                stable_box = _stable_candidate_more_options_box(snapshot)
                safe_point = _candidate_more_options_safe_point(snapshot, stable_box)
                current_box = self._current_candidate_more_options_hit(
                    control,
                    candidate_id,
                    stable_box,
                    preferred_probe=safe_point["name"],
                )
                snapshot["reveal_attempt"] = reveal_attempts
                snapshot["prior_reveal_outcomes"] = list(prior_reveal_outcomes)
                snapshot["window_layout_recovery"] = recovery
                return control, snapshot, current_box
            last_outcome = recovery_outcome
            if recovery_outcome == "control_zero_geometry":
                last_outcome = "control_present_not_actionable"
            last_error = FlowAutomationError(
                "Candidate More options remained unusable after one bounded "
                "same-UUID window-layout recovery; "
                f"outcome={recovery_outcome}; facts="
                f"{json.dumps(recovery_facts, sort_keys=True, separators=(',', ':'))}; "
                "recovery="
                f"{json.dumps(recovery, sort_keys=True, separators=(',', ':'))}."
            )
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        hit_detail = (
            f"; hit_diagnostic={last_hit_diagnostic}"
            if last_hit_diagnostic
            else ""
        )
        failure_codes = {
            "control_not_present": "CANDIDATE_MORE_OPTIONS_NOT_PRESENT",
            "control_present_but_hidden": "CANDIDATE_MORE_OPTIONS_HIDDEN",
            "control_present_not_actionable": "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE",
            "image_topmost": "CANDIDATE_MORE_OPTIONS_IMAGE_TOPMOST",
            "tile_replaced_during_reveal": "CANDIDATE_TILE_REPLACED_DURING_REVEAL",
            "control_replaced_or_unstable": "CANDIDATE_MORE_OPTIONS_REPLACED_OR_UNSTABLE",
            "hit_test_ownership_not_established": "CANDIDATE_MORE_OPTIONS_OVERLAID",
        }
        code = failure_codes.get(last_outcome, "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE")
        layout_detail = (
            self._candidate_more_options_failure_layout_detail(candidate_id)
            if code == "CANDIDATE_MORE_OPTIONS_NOT_PRESENT"
            else ""
        )
        raise FlowAutomationError(
            f"{code}: candidate={candidate_id}; bounded reveal/reacquisition "
            f"attempts={reveal_attempts}; outcome={last_outcome}"
            f"{layout_detail}{hit_detail}{detail}."
        )

    def _inspect_candidate_more_options(
        self,
        control,
        candidate_id: str,
        *,
        sample_count: int = 4,
        sample_interval_ms: int = 100,
    ) -> dict[str, Any]:
        """Inspect stable geometry and a bounded set of ownership-aware hit points."""
        snapshot = self._inspect_download_menu_item(
            control,
            sample_count=sample_count,
            sample_interval_ms=sample_interval_ms,
        )
        raw = control.evaluate(
            r"""
            (element, candidateId) => {
              const rect = element.getBoundingClientRect();
              const tile = element.closest("flow-image-tile");
              const globalExactImages = [...document.querySelectorAll("img[data-media-id]")]
                .filter((image) => image.getAttribute("data-media-id") === candidateId);
              const exactImages = tile ? [...tile.querySelectorAll("img[data-media-id]")]
                .filter((image) => image.getAttribute("data-media-id") === candidateId) : [];
              const composedReaches = (node, ancestor) => {
                if (!(node instanceof Element) || !(ancestor instanceof Element)) return false;
                const seen = new Set();
                let current = node;
                while (current && !seen.has(current)) {
                  seen.add(current);
                  if (current === ancestor) return true;
                  if (current.assignedSlot) {
                    current = current.assignedSlot;
                    continue;
                  }
                  if (current.parentElement) {
                    current = current.parentElement;
                    continue;
                  }
                  const root = current.getRootNode?.();
                  current = root instanceof ShadowRoot ? root.host : null;
                }
                return false;
              };
              const composedOwnership = (top) => {
                if (!(top instanceof Element)) return null;
                if (top === element) return "self";
                if (element.contains(top)) return "descendant";
                if (composedReaches(top, element)) return "composed_descendant";
                return null;
              };
              const materialSnackbarRoot = (node) => {
                if (!(node instanceof Element)) return null;
                const root = node.closest(".mat-mdc-snack-bar-container,.mdc-snackbar");
                if (!root) return null;
                const hasMaterialStructure = root.matches(".mdc-snackbar") || Boolean(
                  root.querySelector(
                    ".mdc-snackbar__surface,.mdc-snackbar__label,.mat-mdc-snack-bar-label"
                  )
                );
                return hasMaterialStructure ? root : null;
              };
              const visiblyPainted = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const nodeRect = node.getBoundingClientRect();
                const nodeStyle = getComputedStyle(node);
                return nodeRect.width > 0 && nodeRect.height > 0 &&
                  nodeStyle.visibility !== "hidden" && nodeStyle.display !== "none" &&
                  Number.parseFloat(nodeStyle.opacity || "1") > 0.01;
              };
              const describe = (top) => {
                if (!(top instanceof Element)) return null;
                const snackbarRoot = materialSnackbarRoot(top);
                const topStyle = getComputedStyle(top);
                const topRect = top.getBoundingClientRect();
                const controlToolbar = element.closest('[role="toolbar"]');
                const topToolbar = top.closest('[role="toolbar"]');
                const ownership = composedOwnership(top);
                return {
                  tag: top.tagName.toLowerCase(),
                  role: top.getAttribute("role"),
                  aria_label: snackbarRoot ? null : top.getAttribute("aria-label"),
                  title: snackbarRoot ? null : top.getAttribute("title"),
                  class_summary: [...top.classList].filter((value) =>
                    /^[A-Za-z0-9_-]{1,60}$/.test(value)
                  ).slice(0, 6),
                  pointer_events: topStyle.pointerEvents,
                  visibility: topStyle.visibility,
                  opacity: topStyle.opacity,
                  bounding_box: {
                    x: topRect.x, y: topRect.y,
                    width: topRect.width, height: topRect.height,
                  },
                  connected: top.isConnected,
                  top_contains_control: top.contains(element),
                  control_contains_top: element.contains(top),
                  same_parent: Boolean(top.parentElement && top.parentElement === element.parentElement),
                  same_toolbar: Boolean(controlToolbar && topToolbar === controlToolbar),
                  same_candidate_tile: Boolean(tile && composedReaches(top, tile)),
                  top_closest_button_is_control: top.closest("button") === element,
                  event_path_reaches_control: Boolean(ownership),
                  composed_ownership: ownership,
                  material_snackbar: Boolean(snackbarRoot),
                };
              };
              const definitions = [
                ["center", 0.50, 0.50],
                ["left", 0.32, 0.50],
                ["right", 0.68, 0.50],
                ["up", 0.50, 0.32],
                ["down", 0.50, 0.68],
              ];
              const probes = definitions.map(([name, fx, fy]) => {
                const x = rect.left + rect.width * fx;
                const y = rect.top + rect.height * fy;
                const top = document.elementFromPoint(x, y);
                return {
                  name,
                  x,
                  y,
                  ownership: composedOwnership(top),
                  top: describe(top),
                };
              });
              return {
                belongs_to_candidate_tile: Boolean(
                  tile && globalExactImages.length === 1 && exactImages.length === 1 &&
                  tile.contains(globalExactImages[0])
                ),
                visible_material_snackbar_count: [...new Set(
                  [...document.querySelectorAll(
                    ".mat-mdc-snack-bar-container,.mdc-snackbar"
                  )].map(materialSnackbarRoot).filter(Boolean)
                )].filter(visiblyPainted).length,
                probes,
              };
            }
            """,
            candidate_id,
            timeout=1_000,
        )
        if not isinstance(raw, dict) or not raw.get("belongs_to_candidate_tile"):
            raise FlowAutomationError(
                "CANDIDATE_MORE_OPTIONS_WRONG_TILE: control no longer belongs to "
                f"candidate {candidate_id}."
            )
        allowed_names = {"center", "left", "right", "up", "down"}
        allowed_ownership = {"self", "descendant", "composed_descendant"}
        probes = []
        for raw_probe in (raw.get("probes") or [])[:5]:
            if not isinstance(raw_probe, dict):
                continue
            name = str(raw_probe.get("name") or "")
            x = raw_probe.get("x")
            y = raw_probe.get("y")
            ownership = raw_probe.get("ownership")
            if name not in allowed_names or not isinstance(x, (int, float)) or not isinstance(
                y,
                (int, float),
            ):
                continue
            probes.append(
                {
                    "name": name,
                    "x": round(float(x), 3),
                    "y": round(float(y), 3),
                    "ownership": ownership if ownership in allowed_ownership else None,
                    "top": _normalize_trace_node(raw_probe.get("top")),
                }
            )
        snapshot["hit_test_probes"] = probes
        raw_snackbar_count = raw.get("visible_material_snackbar_count")
        snapshot["visible_material_snackbar_count"] = (
            min(16, max(0, int(raw_snackbar_count)))
            if isinstance(raw_snackbar_count, (int, float))
            else 0
        )
        snapshot["safe_owned_point"] = next(
            (probe for probe in probes if probe["ownership"] in allowed_ownership),
            None,
        )
        if probes and probes[0]["name"] == "center":
            snapshot["center_top_belongs_to_control"] = bool(
                probes[0]["ownership"] in allowed_ownership
            )
            snapshot["overlay_above"] = not snapshot[
                "center_top_belongs_to_control"
            ]
            snapshot["center_top"] = probes[0]["top"]
        return snapshot

    def _current_candidate_more_options_hit(
        self,
        control,
        candidate_id: str,
        prior_box: dict[str, float],
        *,
        tolerance: float = 1.0,
        preferred_probe: str | None = None,
        diagnostic_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self._inspect_candidate_more_options(
            control,
            candidate_id,
            sample_count=2,
            sample_interval_ms=50,
        )
        if diagnostic_snapshot is not None:
            diagnostic_snapshot.clear()
            diagnostic_snapshot.update(snapshot)
        current = _stable_candidate_more_options_box(snapshot, tolerance=tolerance)
        if any(
            abs(current[key] - prior_box[key]) > tolerance
            for key in ("x", "y", "width", "height")
        ):
            raise FlowAutomationError(
                "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE: box changed between "
                "stability check and input."
            )
        activation_point = _candidate_more_options_safe_point(
            snapshot,
            current,
            preferred_probe=preferred_probe,
        )
        return {**current, "activation_point": activation_point}

    def _activate_candidate_more_options(
        self,
        control,
        candidate_id: str,
        stable_box: dict[str, Any],
    ) -> dict[str, Any]:
        """Activate exact candidate More options once with explicit mouse input."""
        page = self._require_page()
        current = interruptible_call(
            "download_button_interaction", "more_options_hit_test",
            self._current_candidate_more_options_hit,
            control,
            candidate_id,
            stable_box,
            preferred_probe=(stable_box.get("activation_point") or {}).get("name"),
        )
        activation_point = current.get("activation_point") or {
            "name": "center",
            "x": float(current["x"] + current["width"] / 2),
            "y": float(current["y"] + current["height"] / 2),
            "ownership": "descendant",
        }
        x = float(activation_point["x"])
        y = float(activation_point["y"])
        interruptible_call(
            "download_button_interaction", "more_options_mouse_move",
            page.mouse.move, x, y,
        )
        down_box = interruptible_call(
            "download_button_interaction", "more_options_before_down_hit_test",
            self._current_candidate_more_options_hit,
            control,
            candidate_id,
            current,
            preferred_probe=activation_point["name"],
        )
        interruptible_call(
            "download_button_interaction", "more_options_mouse_down",
            page.mouse.down, button="left",
        )
        try:
            up_box = interruptible_call(
                "download_button_interaction", "more_options_before_up_hit_test",
                self._current_candidate_more_options_hit,
                control,
                candidate_id,
                down_box,
                preferred_probe=activation_point["name"],
            )
        finally:
            if not cancellation_requested():
                interruptible_call(
                    "download_button_interaction",
                    "candidate_more_options_mouse_up",
                    page.mouse.up,
                    button="left",
                )
        return {
            "input": "mouse_move_down_up",
            "activation_count": 1,
            "safe_point": {
                "name": activation_point["name"],
                "x": x,
                "y": y,
                "ownership": activation_point.get("ownership"),
            },
            "box_before_up": up_box,
        }

    @staticmethod
    def _arm_candidate_menu_baseline(page, candidate_id: str) -> dict[str, Any]:
        raw = page.evaluate(
            r"""
            (candidateId) => {
              const visible = (element) => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return element.isConnected && rect.width > 0 && rect.height > 0 &&
                       style.visibility !== "hidden" && style.display !== "none";
              };
              const visibleMenus = [...document.querySelectorAll("[role='menu']")]
                .filter(visible);
              window.__nanoBatchCandidateMenuBaselineV1 = {
                candidateId,
                visibleMenus,
              };
              return {candidate_id: candidateId, visible_menu_count: visibleMenus.length};
            }
            """,
            candidate_id,
        )
        return {
            "candidate_id": str((raw or {}).get("candidate_id") or ""),
            "visible_menu_count": int((raw or {}).get("visible_menu_count") or 0),
        }

    @staticmethod
    def _clear_candidate_menu_baseline(page) -> None:
        page.evaluate(
            "() => { delete window.__nanoBatchCandidateMenuBaselineV1; "
            "delete window.__nanoBatchCandidateMenuNodeV1; }"
        )

    def _unique_download_menu_item(self):
        matches = [
            control
            for control in self._visible_role_matches(
                ("menuitem",),
                re.compile(r"^(?:download\s+)?download$", re.I),
            )
            if control.is_enabled()
        ]
        if len(matches) != 1:
            qualifier = "missing" if not matches else "ambiguous"
            raise FlowAutomationError(
                f"Candidate Download menu item is {qualifier}; "
                f"found {len(matches)} visible enabled matches."
            )
        return matches[0]

    @staticmethod
    def _empty_candidate_menu_diagnostics() -> dict[str, Any]:
        return {
            "more_options_before_activation": None,
            "more_options_activation": None,
            "more_options_activation_count": 0,
            "menu_baseline": None,
            "menu_visible_before_options_activation": False,
            "menu_visible_after_options_activation": False,
            "live_menu_count": 0,
            "fresh_live_menu_count": 0,
            "stale_visible_menu_count": 0,
            "fresh_download_match_count": 0,
            "fresh_download_visible_count": 0,
            "fresh_download_connected_count": 0,
            "fresh_download_enabled_count": 0,
            "download_node_identity_changed_after_open": False,
            "download_node_detached_during_wait": False,
            "last_download_control": None,
            "poll_count": 0,
        }

    @staticmethod
    @_ack_profiled("_live_control_facts")
    def _live_control_facts(control) -> dict[str, Any]:
        raw = _ack_call("live_control_evaluate", control.evaluate,
            r"""
            (element) => {
              const rect = element.getBoundingClientRect();
              const style = getComputedStyle(element);
              const disabled = Boolean(element.disabled);
              const ariaDisabled = element.getAttribute("aria-disabled");
              return {
                tag: element.tagName.toLowerCase(),
                role: element.getAttribute("role"),
                connected: element.isConnected,
                visible: element.isConnected && rect.width > 0 && rect.height > 0 &&
                         style.visibility !== "hidden" && style.display !== "none" &&
                         Number.parseFloat(style.opacity || "1") > 0.01,
                enabled: !disabled && String(ariaDisabled || "").toLowerCase() !== "true",
                disabled,
                aria_disabled: ariaDisabled,
                pointer_events: style.pointerEvents,
                opacity: style.opacity,
                visibility: style.visibility,
                display: style.display,
                bounding_box: {
                  x: rect.x,
                  y: rect.y,
                  width: rect.width,
                  height: rect.height,
                },
              };
            }
            """,
            timeout=500,
        )
        if not isinstance(raw, dict):
            return {"connected": False, "visible": False, "enabled": False}
        return {
            "tag": _clean_text(str(raw.get("tag") or ""), 40).lower() or None,
            "role": _clean_text(str(raw.get("role") or ""), 40).lower() or None,
            "connected": bool(raw.get("connected")),
            "visible": bool(raw.get("visible")),
            "enabled": bool(raw.get("enabled")),
            "disabled": bool(raw.get("disabled")),
            "aria_disabled": _clean_text(str(raw.get("aria_disabled") or ""), 20) or None,
            "pointer_events": _clean_text(str(raw.get("pointer_events") or ""), 20) or None,
            "opacity": _clean_text(str(raw.get("opacity") or ""), 20) or None,
            "visibility": _clean_text(str(raw.get("visibility") or ""), 20).lower() or None,
            "display": _clean_text(str(raw.get("display") or ""), 20).lower() or None,
            "bounding_box": _safe_layout_box(raw.get("bounding_box")),
        }

    def _current_candidate_menu_controls(self) -> tuple[list[Any], dict[str, Any]]:
        """Re-query the current DOM; returned controls are never cached across polls."""
        page = self._require_page()
        live_menus = 0
        stale_visible_menus = 0
        for menu in page.get_by_role("menu").all():
            try:
                facts = self._live_control_facts(menu)
                was_visible_at_baseline = bool(
                    menu.evaluate(
                        r"""
                        (element) => Boolean(
                          window.__nanoBatchCandidateMenuBaselineV1?.visibleMenus?.includes(element)
                        )
                        """,
                        timeout=500,
                    )
                )
            except Exception:
                cancellation_checkpoint("candidate_menu_wait")
                continue
            if facts["connected"] and facts["visible"]:
                live_menus += 1
                stale_visible_menus += int(was_visible_at_baseline)

        matches = page.get_by_role(
            "menuitem",
            name=re.compile(r"^(?:download\s+)?download$", re.I),
        ).all()
        eligible = []
        eligible_facts: list[dict[str, Any]] = []
        visible_count = 0
        connected_count = 0
        enabled_count = 0
        fresh_match_count = 0
        for control in matches:
            try:
                facts = self._live_control_facts(control)
                menu_relationship = control.evaluate(
                        r"""
                        (element) => {
                          const menu = element.closest("[role='menu']");
                          if (!menu || !menu.isConnected) {
                            return {inside_visible_menu: false, stale_at_baseline: false};
                          }
                          const rect = menu.getBoundingClientRect();
                          const style = getComputedStyle(menu);
                          return {
                            inside_visible_menu: rect.width > 0 && rect.height > 0 &&
                              style.visibility !== "hidden" && style.display !== "none",
                            stale_at_baseline: Boolean(
                              window.__nanoBatchCandidateMenuBaselineV1?.visibleMenus?.includes(menu)
                            ),
                          };
                        }
                        """,
                        timeout=500,
                    ) or {}
            except Exception:
                cancellation_checkpoint("candidate_menu_wait")
                continue
            inside_visible_menu = bool(menu_relationship.get("inside_visible_menu"))
            stale_at_baseline = bool(menu_relationship.get("stale_at_baseline"))
            if not stale_at_baseline:
                fresh_match_count += 1
                connected_count += int(facts["connected"])
                visible_count += int(facts["visible"])
                enabled_count += int(facts["enabled"])
            if (
                facts["connected"]
                and facts["visible"]
                and facts["enabled"]
                and inside_visible_menu
                and not stale_at_baseline
            ):
                eligible.append(control)
                eligible_facts.append(facts)
        fresh_live_menus = live_menus - stale_visible_menus
        current_parent = eligible_facts[0] if len(eligible_facts) == 1 else {}
        return eligible, {
            "live_menu_count": live_menus,
            "fresh_live_menu_count": fresh_live_menus,
            "stale_visible_menu_count": stale_visible_menus,
            "fresh_download_match_count": fresh_match_count,
            "fresh_download_visible_count": visible_count,
            "fresh_download_connected_count": connected_count,
            "fresh_download_enabled_count": enabled_count,
            "menu_visible": bool(fresh_live_menus and eligible),
            "current_download_parent": {
                "connected": current_parent.get("connected"),
                "visible": current_parent.get("visible"),
                "enabled": current_parent.get("enabled"),
                "pointer_events": current_parent.get("pointer_events"),
            }
            if current_parent
            else None,
        }

    @staticmethod
    def _remember_download_node_identity(control) -> tuple[bool, bool]:
        raw = control.evaluate(
            r"""
            (element) => {
              const KEY = "__nanoBatchCandidateMenuNodeV1";
              const previous = window[KEY] || null;
              window[KEY] = element;
              return {seen_before: Boolean(previous), changed: Boolean(previous && previous !== element)};
            }
            """,
            timeout=500,
        )
        return bool((raw or {}).get("seen_before")), bool((raw or {}).get("changed"))

    def _wait_for_candidate_menu(
        self,
        candidate_id: str,
        diagnostics: dict[str, Any],
        *,
        timeout_ms: int = 2_000,
    ) -> tuple[Any, dict[str, Any]]:
        """Wait briefly for one fresh, stable Download item in a visible context menu."""
        if not 250 <= timeout_ms <= 5_000:
            raise ValueError("Candidate menu wait must be between 250 and 5000 ms.")
        page = self._require_page()
        page.evaluate("() => { delete window.__nanoBatchCandidateMenuNodeV1; }")
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error = None
        try:
            while time.monotonic() < deadline:
                cancellation_checkpoint("candidate_menu_wait")
                diagnostics["poll_count"] += 1
                eligible, current = self._current_candidate_menu_controls()
                diagnostics.update(
                    {
                        "menu_visible_after_options_activation": current["menu_visible"],
                        "live_menu_count": current["live_menu_count"],
                        "fresh_live_menu_count": current.get(
                            "fresh_live_menu_count",
                            current["live_menu_count"],
                        ),
                        "stale_visible_menu_count": current.get(
                            "stale_visible_menu_count",
                            0,
                        ),
                        "fresh_download_match_count": current["fresh_download_match_count"],
                        "fresh_download_visible_count": current["fresh_download_visible_count"],
                        "fresh_download_connected_count": current["fresh_download_connected_count"],
                        "fresh_download_enabled_count": current["fresh_download_enabled_count"],
                    }
                )
                if len(eligible) == 1:
                    control = eligible[0]
                    try:
                        _, changed = self._remember_download_node_identity(control)
                        diagnostics["download_node_identity_changed_after_open"] |= changed
                        snapshot = self._inspect_download_menu_item(control)
                        diagnostics["last_download_control"] = {
                            "connected": snapshot.get("connected"),
                            "visible": snapshot.get("visible"),
                            "disabled": snapshot.get("disabled"),
                            "aria_disabled": snapshot.get("aria_disabled"),
                            "pointer_events": snapshot.get("pointer_events"),
                            "bounding_box_samples": snapshot.get("bounding_box_samples"),
                            "overlay_above": snapshot.get("overlay_above"),
                        }
                        if not snapshot.get("connected"):
                            diagnostics["download_node_detached_during_wait"] = True
                        _stable_download_control_box(snapshot)
                        return control, snapshot
                    except Exception as exc:
                        cancellation_checkpoint("candidate_menu_wait")
                        last_error = exc
                        if "not visible and connected" in str(exc):
                            diagnostics["download_node_detached_during_wait"] = True
                page.wait_for_timeout(100)
        finally:
            if not cancellation_requested():
                try:
                    interruptible_call(
                        "download_cleanup",
                        "candidate_menu_identity_cleanup",
                        page.evaluate,
                        "() => { delete window.__nanoBatchCandidateMenuNodeV1; }",
                    )
                except Exception:
                    pass
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        raise FlowAutomationError(
            f"CANDIDATE_MENU_OPEN_TIMEOUT: candidate={candidate_id}; "
            f"live_menu_count={diagnostics['live_menu_count']}; "
            f"fresh_download_match_count={diagnostics['fresh_download_match_count']}; "
            f"fresh_download_visible_count={diagnostics['fresh_download_visible_count']}; "
            f"fresh_download_connected_count={diagnostics['fresh_download_connected_count']}"
            f"{detail}"
        )

    def _open_candidate_menu(
        self,
        candidate_id: str,
        image,
        tile,
        diagnostics: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """Open the exact tile menu, then reacquire Download from the current live DOM."""
        page = self._require_page()
        _, before = self._current_candidate_menu_controls()
        diagnostics["menu_visible_before_options_activation"] = before["menu_visible"]
        if before["live_menu_count"]:
            page.keyboard.press("Escape")
            close_deadline = time.monotonic() + 1
            while time.monotonic() < close_deadline:
                cancellation_checkpoint("stale_candidate_menu_close")
                _, closed = self._current_candidate_menu_controls()
                if closed["live_menu_count"] == 0:
                    break
                page.wait_for_timeout(100)
            else:
                raise FlowAutomationError(
                    "CANDIDATE_MENU_OPEN_TIMEOUT: a stale visible menu did not close "
                    "before candidate More options activation."
                )

        stage_started = time.monotonic()
        options, options_snapshot, stable_box = self._reveal_candidate_options(
            candidate_id,
            image,
            tile,
        )
        diagnostics.setdefault("timing_ms", {})["toolbar_reveal"] = round(
            (time.monotonic() - stage_started) * 1_000,
            3,
        )
        diagnostics["more_options_before_activation"] = {
            "candidate_id": candidate_id,
            "connected": options_snapshot.get("connected"),
            "visible": options_snapshot.get("visible"),
            "disabled": options_snapshot.get("disabled"),
            "aria_disabled": options_snapshot.get("aria_disabled"),
            "pointer_events": options_snapshot.get("pointer_events"),
            "opacity": options_snapshot.get("opacity"),
            "visibility": options_snapshot.get("visibility"),
            "display": options_snapshot.get("display"),
            "bounding_box_samples": options_snapshot.get("bounding_box_samples"),
            "overlay_above": options_snapshot.get("overlay_above"),
            "stable_box": stable_box,
            "reveal_attempt": options_snapshot.get("reveal_attempt"),
            "prior_reveal_outcomes": options_snapshot.get(
                "prior_reveal_outcomes",
                [],
            ),
            "transient_overlay_type": options_snapshot.get(
                "transient_overlay_type"
            ),
            "snackbar_wait_entered": bool(
                options_snapshot.get("snackbar_wait_entered")
            ),
            "snackbar_wait_ms": options_snapshot.get("snackbar_wait_ms"),
            "snackbar_poll_count": int(
                options_snapshot.get("snackbar_poll_count") or 0
            ),
            "snackbar_disappeared": options_snapshot.get(
                "snackbar_disappeared"
            ),
            "snackbar_stopped_intersecting": options_snapshot.get(
                "snackbar_stopped_intersecting"
            ),
            "snackbar_candidate_reacquisition_count": int(
                options_snapshot.get("snackbar_candidate_reacquisition_count")
                or 0
            ),
            "snackbar_control_reacquisition_count": int(
                options_snapshot.get("snackbar_control_reacquisition_count") or 0
            ),
        }
        baseline_armed = False
        try:
            baseline_armed = True
            baseline = self._arm_candidate_menu_baseline(page, candidate_id)
            diagnostics["menu_baseline"] = baseline
            if baseline["visible_menu_count"] != 0:
                raise FlowAutomationError(
                    "CANDIDATE_MENU_OPEN_TIMEOUT: a visible menu appeared before "
                    "More options activation; refusing stale-menu correlation."
                )
            stage_started = time.monotonic()
            activation = self._activate_candidate_more_options(
                options,
                candidate_id,
                stable_box,
            )
            diagnostics["timing_ms"]["more_options_activation"] = round(
                (time.monotonic() - stage_started) * 1_000,
                3,
            )
            diagnostics["more_options_activation"] = activation
            diagnostics["more_options_activation_count"] = 1
            stage_started = time.monotonic()
            result = self._wait_for_candidate_menu(candidate_id, diagnostics)
            diagnostics["timing_ms"]["download_parent_acquisition"] = round(
                (time.monotonic() - stage_started) * 1_000,
                3,
            )
            return result
        finally:
            if baseline_armed and not cancellation_requested():
                try:
                    interruptible_call(
                        "download_cleanup",
                        "candidate_menu_baseline_cleanup",
                        self._clear_candidate_menu_baseline,
                        page,
                    )
                except Exception:
                    pass

    def _current_associated_download_submenu(
        self,
        parent,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Resolve only the current parent's explicit ARIA-controlled submenu."""
        page = self._require_page()
        haspopup = _clean_text(str(parent.get_attribute("aria-haspopup") or ""), 40).lower()
        expanded = _clean_text(str(parent.get_attribute("aria-expanded") or ""), 20).lower()
        controls_id = str(parent.get_attribute("aria-controls") or "").strip()
        if haspopup != "menu":
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: first-level Download lacks aria-haspopup=menu."
            )

        safe_controls_id = (
            controls_id
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", controls_id)
            else None
        )
        controlled_target = {}
        if controls_id:
            try:
                raw_target = parent.evaluate(
                    r"""
                    (element) => {
                      const id = element.getAttribute("aria-controls") || "";
                      const visible = (node) => {
                        if (!(node instanceof Element) || !node.isConnected) return false;
                        const rect = node.getBoundingClientRect();
                        const style = getComputedStyle(node);
                        if (rect.width > 0 && rect.height > 0 &&
                            style.visibility !== "hidden" && style.display !== "none" &&
                            Number.parseFloat(style.opacity || "1") > 0.01) return true;
                        return [...node.querySelectorAll("[role='menu']")].some((menu) => {
                          const menuRect = menu.getBoundingClientRect();
                          const menuStyle = getComputedStyle(menu);
                          return menu.isConnected && menuRect.width > 0 && menuRect.height > 0 &&
                            menuStyle.visibility !== "hidden" && menuStyle.display !== "none" &&
                            Number.parseFloat(menuStyle.opacity || "1") > 0.01;
                        });
                      };
                      const targets = [...document.querySelectorAll("[id]")]
                        .filter((node) => node.id === id);
                      return {
                        match_count: targets.length,
                        connected_count: targets.filter((node) => node.isConnected).length,
                        visible_count: targets.filter(visible).length,
                      };
                    }
                    """,
                    timeout=500,
                )
                if isinstance(raw_target, dict):
                    controlled_target = raw_target
            except Exception:
                cancellation_checkpoint("download_submenu_wait")
                controlled_target = {}

        live_menu_count = 0
        menu_panel_ids: list[str] = []
        explicit_matches = []
        controlled_visible_1k_count = 0
        relationship_probe_errors = 0
        for menu in page.get_by_role("menu").all():
            try:
                facts = self._live_control_facts(menu)
                menu_id = str(menu.get_attribute("id") or "")
            except Exception:
                cancellation_checkpoint("download_submenu_wait")
                relationship_probe_errors += 1
                continue
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", menu_id):
                menu_panel_ids.append(menu_id)
            if facts["connected"] and facts["visible"]:
                live_menu_count += 1
            else:
                continue

            related = bool(controls_id and menu_id == controls_id)
            if controls_id and not related:
                try:
                    raw_relationship = menu.evaluate(
                        r"""
                        (element, controlsId) => {
                          let node = element;
                          while (node instanceof Element) {
                            if (node.id === controlsId) return {related: true};
                            node = node.parentElement;
                          }
                          return {related: false};
                        }
                        """,
                        controls_id,
                        timeout=500,
                    )
                    related = bool(
                        isinstance(raw_relationship, dict)
                        and raw_relationship.get("related")
                    )
                except Exception:
                    cancellation_checkpoint("download_submenu_wait")
                    relationship_probe_errors += 1
            if not related:
                continue

            explicit_matches.append(menu)
            try:
                semantic_1k = menu.get_by_role(
                    "menuitem",
                    name=re.compile(r"^\s*1K\s+Original\s+size\s*$", re.I),
                ).all()
                for option in semantic_1k:
                    option_facts = self._live_control_facts(option)
                    if option_facts["connected"] and option_facts["visible"]:
                        controlled_visible_1k_count += 1
            except Exception:
                cancellation_checkpoint("download_submenu_wait")
                relationship_probe_errors += 1

        controlled_match_count = int(controlled_target.get("match_count") or 0)
        controlled_connected_count = int(
            controlled_target.get("connected_count") or 0
        )
        controlled_visible_count = int(controlled_target.get("visible_count") or 0)
        # A direct role=menu ID match is sufficient even if the optional aggregate
        # target probe raced an Angular remount and returned no result.
        if controls_id and explicit_matches and controlled_match_count == 0:
            controlled_match_count = len(explicit_matches)
            controlled_connected_count = len(explicit_matches)
            controlled_visible_count = len(explicit_matches)

        return explicit_matches, {
            "aria_haspopup": haspopup or None,
            "aria_expanded": expanded or None,
            "aria_controls": safe_controls_id,
            "has_aria_controls": bool(controls_id),
            "relationship_basis": "aria_controls" if explicit_matches else None,
            "live_menu_count": live_menu_count,
            "visible_menu_panel_count": live_menu_count,
            "menu_panel_ids": sorted(set(menu_panel_ids))[:16],
            "controlled_panel_found": controlled_match_count > 0,
            "controlled_panel_match_count": controlled_match_count,
            "controlled_panel_connected": controlled_connected_count > 0,
            "controlled_panel_visible": controlled_visible_count > 0,
            "controlled_role_menu_count": len(explicit_matches),
            "related_menu_count": len(explicit_matches),
            "structural_candidate_count": 0,
            "visible_related_menu_count": len(explicit_matches),
            "visible_1k_in_controlled_panel": controlled_visible_1k_count,
            "relationship_probe_error_count": relationship_probe_errors,
        }

    def _current_download_submenu_parents(self) -> tuple[list[Any], dict[str, Any]]:
        """Reacquire current live semantic Download submenu parents from the DOM."""
        controls, facts = self._current_candidate_menu_controls()
        parents = []
        for control in controls:
            try:
                haspopup = _clean_text(
                    str(control.get_attribute("aria-haspopup") or ""), 40
                ).lower()
            except Exception:
                cancellation_checkpoint("download_submenu_wait")
                continue
            if haspopup == "menu":
                parents.append(control)
        return parents, {
            **facts,
            "semantic_download_parent_count": len(parents),
        }

    @staticmethod
    def _normalize_before_hover_handoff_decision(
        raw: Any,
        *,
        expected_controls_id: str,
    ) -> dict[str, Any]:
        """Whitelist and classify one probe-only pre-handoff DOM decision."""
        if not isinstance(raw, dict):
            raw = {}

        def token(value: Any, limit: int = 128) -> str | None:
            text = str(value or "")[:limit]
            return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else None

        def bounded_count(value: Any) -> int:
            return (
                int(value)
                if isinstance(value, (int, float)) and 0 <= int(value) <= 100
                else 0
            )

        def node(value: Any) -> dict[str, Any] | None:
            if not isinstance(value, dict):
                return None
            classes = [
                item
                for item in (
                    token(item, 60) for item in (value.get("class_summary") or [])[:4]
                )
                if item is not None
            ]
            return {
                "id": token(value.get("id")),
                "tag": token(value.get("tag"), 40),
                "role": token(value.get("role"), 40),
                "aria_haspopup": token(value.get("aria_haspopup"), 40),
                "aria_expanded": token(value.get("aria_expanded"), 20),
                "aria_controls": token(value.get("aria_controls")),
                "connected": bool(value.get("connected")),
                "visible": bool(value.get("visible")),
                "hover": bool(value.get("hover")),
                "bounding_box": _normalize_submenu_box(value.get("bounding_box")),
                "class_summary": classes,
            }

        def point(value: Any) -> dict[str, float] | None:
            if not isinstance(value, dict):
                return None
            x = value.get("x")
            y = value.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return None
            if not -100_000 <= float(x) <= 100_000:
                return None
            if not -100_000 <= float(y) <= 100_000:
                return None
            return {"x": round(float(x), 3), "y": round(float(y), 3)}

        def css_value(value: Any) -> str | None:
            text = _clean_text(str(value or ""), 40)
            return (
                text
                if text and re.fullmatch(r"[A-Za-z0-9_.%()+-]{1,40}", text)
                else None
            )

        def visibility_subject(value: Any) -> dict[str, Any]:
            raw_subject = value if isinstance(value, dict) else {}
            bounding_box = _normalize_submenu_box(raw_subject.get("bounding_box"))
            visibility = css_value(raw_subject.get("visibility"))
            display = css_value(raw_subject.get("display"))
            opacity = css_value(raw_subject.get("opacity"))
            pointer_events = css_value(raw_subject.get("pointer_events"))
            opacity_source = opacity or "1"
            opacity_match = re.match(
                r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
                opacity_source,
            )
            opacity_value = (
                float(opacity_match.group(0)) if opacity_match is not None else None
            )
            clauses = {
                "is_element": bool(raw_subject.get("is_element")),
                "connected": bool(raw_subject.get("connected")),
                "positive_width": bool(
                    bounding_box is not None and bounding_box["width"] > 0
                ),
                "positive_height": bool(
                    bounding_box is not None and bounding_box["height"] > 0
                ),
                "visibility_not_hidden": visibility != "hidden",
                "display_not_none": display != "none",
                "opacity_parseable": opacity_value is not None,
                "opacity_above_threshold": bool(
                    opacity_value is not None and opacity_value > 0.01
                ),
            }
            return {
                "exact_id": token(raw_subject.get("id")),
                "tag": token(raw_subject.get("tag"), 40),
                "role": token(raw_subject.get("role"), 40),
                "connected": bool(raw_subject.get("connected")),
                "bounding_box": bounding_box,
                "computed_css": {
                    "visibility": visibility,
                    "display": display,
                    "opacity": opacity,
                    "pointer_events": pointer_events,
                },
                "clauses": clauses,
                "production_visible": all(clauses.values()),
            }

        expected_id = token(expected_controls_id)
        current_id = token(raw.get("current_aria_controls"))
        parent = node(raw.get("parent"))
        target = node(raw.get("controlled_target"))
        target_count = bounded_count(raw.get("controlled_target_count"))
        target_connected_count = bounded_count(
            raw.get("controlled_target_connected_count")
        )
        target_visible_count = bounded_count(
            raw.get("controlled_target_visible_count")
        )
        menu_count = bounded_count(raw.get("controlled_role_menu_count"))
        menu_connected_count = bounded_count(
            raw.get("controlled_role_menu_connected_count")
        )
        menu_visible_count = bounded_count(
            raw.get("controlled_role_menu_visible_count")
        )
        candidates = [
            normalized
            for value in (raw.get("menu_candidates") or [])[:4]
            if (normalized := node(value)) is not None
        ]
        handoff_point = point(raw.get("handoff_point"))
        relationship_unchanged = bool(
            expected_id
            and current_id == expected_id
            and parent is not None
            and parent.get("aria_expanded") == "true"
        )

        gate_values = (
            ("parent_connected", bool(parent and parent["connected"])),
            ("parent_role_menuitem", bool(parent and parent["role"] == "menuitem")),
            ("parent_haspopup_menu", bool(parent and parent["aria_haspopup"] == "menu")),
            ("parent_expanded_true", bool(parent and parent["aria_expanded"] == "true")),
            ("controls_id_valid", bool(raw.get("controls_id_valid"))),
            ("controlled_target_unique", target_count == 1),
            ("controlled_target_connected_unique", target_connected_count == 1),
            ("controlled_target_visible_unique", target_visible_count == 1),
            ("controlled_role_menu_unique", menu_count == 1),
            ("controlled_role_menu_connected_unique", menu_connected_count == 1),
            ("controlled_role_menu_visible_unique", menu_visible_count == 1),
            ("handoff_point_available", isinstance(raw.get("handoff_point"), dict)),
            ("handoff_hit_owned", raw.get("handoff_hit_owned") is True),
            ("handoff_coordinate_parseable", handoff_point is not None),
        )
        gate_reasons = {
            "parent_connected": ("parent is connected", "parent is disconnected"),
            "parent_role_menuitem": (
                "parent role is menuitem",
                "parent role is not menuitem",
            ),
            "parent_haspopup_menu": (
                "parent aria-haspopup is menu",
                "parent aria-haspopup is not menu",
            ),
            "parent_expanded_true": (
                "parent aria-expanded is true",
                "parent aria-expanded is not true",
            ),
            "controls_id_valid": (
                "aria-controls is a safe concrete id",
                "aria-controls is not a safe concrete id",
            ),
            "controlled_target_unique": (
                "exact controlled target is unique",
                "exact controlled target is missing or duplicated",
            ),
            "controlled_target_connected_unique": (
                "exact controlled target is connected",
                "exact controlled target is not uniquely connected",
            ),
            "controlled_target_visible_unique": (
                "exact controlled target passes the production visibility gate",
                "exact controlled target fails the production visibility gate",
            ),
            "controlled_role_menu_unique": (
                "controlled role=menu is unique",
                "controlled role=menu is missing or duplicated",
            ),
            "controlled_role_menu_connected_unique": (
                "controlled role=menu is connected",
                "controlled role=menu is not uniquely connected",
            ),
            "controlled_role_menu_visible_unique": (
                "controlled role=menu is visible",
                "controlled role=menu is not uniquely visible",
            ),
            "handoff_point_available": (
                "an owned handoff point was selected",
                "no handoff point was selected",
            ),
            "handoff_hit_owned": (
                "handoff hit is owned by target and menu",
                "handoff hit is not owned by target and menu",
            ),
            "handoff_coordinate_parseable": (
                "handoff coordinate is parseable",
                "handoff coordinate is not parseable",
            ),
        }
        gates = [
            {
                "name": name,
                "passed": passed,
                "reason": gate_reasons[name][0 if passed else 1],
            }
            for name, passed in gate_values
        ]

        raw_visibility = (
            raw.get("visibility_observation")
            if isinstance(raw.get("visibility_observation"), dict)
            else {}
        )
        visibility_parent = node(raw_visibility.get("parent"))
        visibility_observation = {
            "relationship_unchanged": bool(
                expected_id
                and visibility_parent is not None
                and visibility_parent.get("connected")
                and visibility_parent.get("aria_expanded") == "true"
                and visibility_parent.get("aria_controls") == expected_id
            ),
            "parent": visibility_parent,
            "controlled_target": visibility_subject(
                raw_visibility.get("controlled_target")
            ),
            "role_menu": visibility_subject(raw_visibility.get("role_menu")),
            "hit_test": {
                "coordinate": point(raw_visibility.get("hit_coordinate")),
                "target": node(raw_visibility.get("hit_target")),
                "inside_controlled_target": bool(
                    raw_visibility.get("hit_inside_controlled_target")
                ),
                "inside_role_menu": bool(raw_visibility.get("hit_inside_role_menu")),
            },
        }
        visibility_target = visibility_observation["controlled_target"]
        visibility_menu = visibility_observation["role_menu"]

        def enterable_without_opacity_threshold(subject: dict[str, Any]) -> bool:
            clauses = subject["clauses"]
            return bool(
                all(
                    passed
                    for name, passed in clauses.items()
                    if name != "opacity_above_threshold"
                )
                and subject["computed_css"]["pointer_events"]
                and subject["computed_css"]["pointer_events"] != "none"
            )

        strict_production_visible_before_handoff = bool(
            visibility_target["production_visible"]
            and visibility_menu["production_visible"]
        )
        diagnostic_handoff_enterable = bool(
            visibility_observation["relationship_unchanged"]
            and parent is not None
            and parent["role"] == "menuitem"
            and parent["aria_haspopup"] == "menu"
            and raw.get("controls_id_valid") is True
            and target_count == 1
            and target_connected_count == 1
            and menu_count == 1
            and menu_connected_count == 1
            and visibility_target["exact_id"] == expected_id
            and visibility_menu["role"] == "menu"
            and enterable_without_opacity_threshold(visibility_target)
            and enterable_without_opacity_threshold(visibility_menu)
            and visibility_observation["hit_test"]["coordinate"] is not None
            and visibility_observation["hit_test"]["inside_controlled_target"]
            and visibility_observation["hit_test"]["inside_role_menu"]
        )
        opacity_only_strict_visibility_difference = bool(
            diagnostic_handoff_enterable
            and not strict_production_visible_before_handoff
            and (
                not visibility_target["clauses"]["opacity_above_threshold"]
                or not visibility_menu["clauses"]["opacity_above_threshold"]
            )
        )

        if not relationship_unchanged:
            decision = "relationship_changed"
        elif target_count == 0:
            decision = "controlled_target_missing"
        elif target_count > 1:
            decision = "duplicate_controlled_id"
        elif target_connected_count != 1:
            decision = "controlled_target_disconnected"
        elif opacity_only_strict_visibility_difference:
            decision = "accepted_for_previsible_handoff"
        elif target_visible_count != 1:
            decision = "controlled_target_hidden"
        elif menu_count == 0:
            decision = "no_valid_menu"
        elif menu_count > 1:
            decision = "multiple_valid_menus"
        elif all(passed for _name, passed in gate_values):
            decision = "accepted_for_handoff"
        else:
            decision = "structural_validation_failed"

        return {
            "schema": "nano-banana-before-hover-handoff/v1",
            "decision": decision,
            "expected_controls_id": expected_id,
            "current_aria_controls": current_id,
            "relationship_unchanged": relationship_unchanged,
            "parent": parent,
            "controlled_target": {
                "exact_id": expected_id,
                "match_count": target_count,
                "exists": target_count > 0,
                "connected_count": target_connected_count,
                "production_visible_count": target_visible_count,
                "identity": target,
            },
            "menu_validation": {
                "structural_candidate_count": menu_count,
                "connected_count": menu_connected_count,
                "visible_count": menu_visible_count,
                "candidates": candidates,
                "correlation_unique": target_count == 1 and menu_count == 1,
                "ambiguity_detected": target_count > 1 or menu_count > 1,
            },
            "handoff_point": handoff_point,
            "handoff_hit_target": node(raw.get("handoff_hit_target")),
            "strict_production_visible_before_handoff": (
                strict_production_visible_before_handoff
            ),
            "handoff_enterable": diagnostic_handoff_enterable,
            "opacity_only_strict_visibility_difference": (
                opacity_only_strict_visibility_difference
            ),
            "previsible_handoff_point": visibility_observation["hit_test"][
                "coordinate"
            ],
            "acceptance_gates": gates,
            "visibility_observation": visibility_observation,
        }

    def _capture_before_hover_handoff_decision(
        self,
        parent,
        *,
        expected_controls_id: str,
    ) -> dict[str, Any]:
        """Atomically inspect the exact controlled target before probe handoff input."""
        raw = parent.evaluate(
            r"""
            (element, expectedControlsId) => {
              const safeToken = (value, limit = 128) => {
                const text = String(value || "").slice(0, limit);
                return /^[A-Za-z0-9_.:-]{1,128}$/.test(text) ? text : null;
              };
              const visible = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== "hidden" && style.display !== "none" &&
                  Number.parseFloat(style.opacity || "1") > 0.01;
              };
              const box = (node) => {
                if (!(node instanceof Element)) return null;
                const rect = node.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const facts = (node) => node instanceof Element ? {
                id: safeToken(node.id), tag: node.tagName.toLowerCase(),
                role: safeToken(node.getAttribute("role"), 40),
                aria_haspopup: safeToken(node.getAttribute("aria-haspopup"), 40),
                aria_expanded: safeToken(node.getAttribute("aria-expanded"), 20),
                aria_controls: safeToken(node.getAttribute("aria-controls")),
                connected: node.isConnected, visible: visible(node), hover: node.matches(":hover"),
                bounding_box: box(node),
                class_summary: [...node.classList]
                  .filter((value) => /^[A-Za-z0-9_-]{1,60}$/.test(value)).slice(0, 4),
              } : null;
              const visibilityFacts = (node) => {
                if (!(node instanceof Element)) return {is_element: false};
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return {
                  is_element: true, id: safeToken(node.id),
                  tag: node.tagName.toLowerCase(),
                  role: safeToken(node.getAttribute("role"), 40),
                  connected: node.isConnected,
                  bounding_box: box(node),
                  visibility: style.visibility,
                  display: style.display,
                  opacity: style.opacity,
                  pointer_events: style.pointerEvents,
                };
              };
              const currentControls = element.getAttribute("aria-controls") || "";
              const controlsIdValid = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(
                currentControls
              );
              const targets = [...document.querySelectorAll("[id]")].filter(
                (node) => node.id === expectedControlsId
              );
              const menus = [];
              for (const target of targets) {
                if (target.matches("[role='menu']")) menus.push(target);
                menus.push(...target.querySelectorAll("[role='menu']"));
              }
              const uniqueMenus = [...new Set(menus)];
              const connectedMenus = uniqueMenus.filter((node) => node.isConnected);
              const visibleMenus = connectedMenus.filter(visible);
              const connectedTargets = targets.filter((node) => node.isConnected);
              const visibleTargets = connectedTargets.filter(
                (target) => visible(target) || visibleMenus.some(
                  (menu) => target === menu || target.contains(menu)
                )
              );

              const exactTarget = targets.length === 1 ? targets[0] : null;
              const exactMenu = uniqueMenus.length === 1 ? uniqueMenus[0] : null;
              let diagnosticPoint = null;
              let diagnosticHit = null;
              if (exactMenu instanceof Element) {
                const rect = exactMenu.getBoundingClientRect();
                const insetX = Math.min(12, Math.max(2, rect.width / 4));
                const insetY = Math.min(12, Math.max(2, rect.height / 4));
                const points = [
                  {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2},
                  {x: rect.left + insetX, y: rect.top + insetY},
                  {x: rect.right - insetX, y: rect.top + insetY},
                  {x: rect.left + insetX, y: rect.bottom - insetY},
                  {x: rect.right - insetX, y: rect.bottom - insetY},
                ];
                diagnosticPoint = points.find((point) =>
                  point.x >= 0 && point.y >= 0 &&
                  point.x < window.innerWidth && point.y < window.innerHeight
                ) || null;
                diagnosticHit = diagnosticPoint
                  ? document.elementFromPoint(diagnosticPoint.x, diagnosticPoint.y)
                  : null;
              }

              let handoffPoint = null;
              let handoffHit = null;
              let handoffHitOwned = false;
              if (
                element.isConnected &&
                element.getAttribute("role") === "menuitem" &&
                (element.getAttribute("aria-haspopup") || "").toLowerCase() === "menu" &&
                (element.getAttribute("aria-expanded") || "").toLowerCase() === "true" &&
                controlsIdValid && currentControls === expectedControlsId &&
                targets.length === 1 && connectedTargets.length === 1 &&
                visibleTargets.length === 1 && uniqueMenus.length === 1 &&
                connectedMenus.length === 1 && visibleMenus.length === 1
              ) {
                const target = targets[0];
                const menu = visibleMenus[0];
                const rect = menu.getBoundingClientRect();
                const insetX = Math.min(12, Math.max(2, rect.width / 4));
                const insetY = Math.min(12, Math.max(2, rect.height / 4));
                const points = [
                  {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2},
                  {x: rect.left + insetX, y: rect.top + insetY},
                  {x: rect.right - insetX, y: rect.top + insetY},
                  {x: rect.left + insetX, y: rect.bottom - insetY},
                  {x: rect.right - insetX, y: rect.bottom - insetY},
                ];
                for (const point of points) {
                  if (
                    point.x < 0 || point.y < 0 ||
                    point.x >= window.innerWidth || point.y >= window.innerHeight
                  ) continue;
                  const hit = document.elementFromPoint(point.x, point.y);
                  if (
                    hit && (hit === menu || menu.contains(hit)) &&
                    (hit === target || target.contains(hit))
                  ) {
                    handoffPoint = point;
                    handoffHit = hit;
                    handoffHitOwned = true;
                    break;
                  }
                }
              }
              return {
                parent: facts(element),
                current_aria_controls: currentControls || null,
                controls_id_valid: controlsIdValid,
                controlled_target_count: targets.length,
                controlled_target_connected_count: connectedTargets.length,
                controlled_target_visible_count: visibleTargets.length,
                controlled_target: facts(targets.length === 1 ? targets[0] : null),
                controlled_role_menu_count: uniqueMenus.length,
                controlled_role_menu_connected_count: connectedMenus.length,
                controlled_role_menu_visible_count: visibleMenus.length,
                menu_candidates: uniqueMenus.map(facts),
                handoff_point: handoffPoint,
                handoff_hit_owned: handoffHitOwned,
                handoff_hit_target: facts(handoffHit),
                visibility_observation: {
                  parent: facts(element),
                  controlled_target: visibilityFacts(exactTarget),
                  role_menu: visibilityFacts(exactMenu),
                  hit_coordinate: diagnosticPoint,
                  hit_target: facts(diagnosticHit),
                  hit_inside_controlled_target: Boolean(
                    exactTarget && diagnosticHit &&
                    (diagnosticHit === exactTarget || exactTarget.contains(diagnosticHit))
                  ),
                  hit_inside_role_menu: Boolean(
                    exactMenu && diagnosticHit &&
                    (diagnosticHit === exactMenu || exactMenu.contains(diagnosticHit))
                  ),
                },
              };
            }
            """,
            expected_controls_id,
            timeout=1_000,
        )
        return self._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id=expected_controls_id,
        )

    @staticmethod
    def _record_before_hover_handoff_decision(
        diagnostics: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        """Keep a small probe-only decision timeline without affecting control flow."""
        timeline = diagnostics.get("before_hover_handoff")
        if not isinstance(timeline, dict):
            timeline = {
                "schema": "nano-banana-before-hover-handoff-timeline/v1",
                "observation_count": 0,
                "decisions": [],
                "truncated": False,
                "visibility_timeline": [],
                "visibility_timeline_max_observations": 8,
                "visibility_timeline_stop_reason": None,
                "visibility_timeline_truncated": False,
            }
            diagnostics["before_hover_handoff"] = timeline
        timeline["observation_count"] = min(
            10_000,
            int(timeline.get("observation_count") or 0) + 1,
        )
        decisions = timeline.get("decisions")
        if not isinstance(decisions, list):
            decisions = []
            timeline["decisions"] = decisions
        if len(decisions) < 4:
            decisions.append(
                {
                    key: value
                    for key, value in decision.items()
                    if key != "visibility_observation"
                }
            )
        else:
            timeline["truncated"] = True

        observation = decision.get("visibility_observation")
        if not isinstance(observation, dict):
            return
        if timeline.get("visibility_timeline_stop_reason") is not None:
            return
        recorded = timeline.get("visibility_timeline")
        if not isinstance(recorded, list):
            recorded = []
            timeline["visibility_timeline"] = recorded
        if len(recorded) >= 8:
            timeline["visibility_timeline_stop_reason"] = "maximum_observations"
            timeline["visibility_timeline_truncated"] = True
            return

        started_at = diagnostics.get("_before_hover_handoff_visibility_started_at")
        if not isinstance(started_at, (int, float)):
            started_at = time.monotonic()
            diagnostics["_before_hover_handoff_visibility_started_at"] = started_at
        safe_observation = dict(observation)
        safe_observation["offset_ms"] = round(
            max(0.0, (time.monotonic() - float(started_at)) * 1_000),
            3,
        )
        source = _clean_text(str(safe_observation.get("source") or ""), 40)
        safe_observation["source"] = source or "handoff_decision"
        recorded.append(safe_observation)

        if not safe_observation.get("relationship_unchanged"):
            timeline["visibility_timeline_stop_reason"] = "relationship_changed"
        else:
            target = safe_observation.get("controlled_target")
            clauses = target.get("clauses") if isinstance(target, dict) else None
            if not isinstance(clauses, dict) or not clauses.get("is_element"):
                timeline["visibility_timeline_stop_reason"] = "target_disappeared"
        if len(recorded) >= 8 and timeline.get("visibility_timeline_stop_reason") is None:
            timeline["visibility_timeline_stop_reason"] = "maximum_observations"
            timeline["visibility_timeline_truncated"] = True

    @staticmethod
    def _stop_before_hover_handoff_visibility_timeline(
        diagnostics: dict[str, Any],
        reason: str,
    ) -> None:
        timeline = diagnostics.get("before_hover_handoff")
        if not isinstance(timeline, dict):
            return
        if timeline.get("visibility_timeline_stop_reason") is None:
            timeline["visibility_timeline_stop_reason"] = reason

    def _observe_before_hover_handoff_visibility(
        self,
        parent,
        diagnostics: dict[str, Any],
        *,
        expanded: str | None,
        controls_id: str | None,
        source: str,
    ) -> None:
        """Add one passive sample at an existing probe visibility-check point."""
        if not diagnostics.get("_passive_parent_trace_enabled"):
            return
        expected = diagnostics.get("_before_hover_handoff_expected_controls_id")
        if expected is None:
            if expanded != "true" or controls_id is None:
                return
            expected = controls_id
            diagnostics["_before_hover_handoff_expected_controls_id"] = expected
        timeline = diagnostics.get("before_hover_handoff")
        if (
            isinstance(timeline, dict)
            and timeline.get("visibility_timeline_stop_reason") is not None
        ):
            return
        if expanded != "true":
            self._stop_before_hover_handoff_visibility_timeline(
                diagnostics,
                "parent_collapsed",
            )
            return
        if controls_id is None:
            self._stop_before_hover_handoff_visibility_timeline(
                diagnostics,
                "relationship_changed",
            )
            return
        try:
            decision = self._capture_before_hover_handoff_decision(
                parent,
                expected_controls_id=str(expected),
            )
        except Exception as exc:
            cancellation_checkpoint("download_submenu_wait")
            decision = {
                "schema": "nano-banana-before-hover-handoff/v1",
                "decision": "diagnostic_snapshot_failed",
                "error": _safe_trace_header(str(exc)),
            }
        observation = decision.get("visibility_observation")
        if isinstance(observation, dict):
            observation["source"] = source
        self._record_before_hover_handoff_decision(diagnostics, decision)

    @staticmethod
    def _normalize_after_hover_handoff_snapshot(
        raw: Any,
        *,
        expected_panel_id: str,
        handoff_coordinate: dict[str, float],
    ) -> dict[str, Any]:
        """Whitelist and classify one probe-only post-handoff DOM observation."""
        if not isinstance(raw, dict):
            raw = {}

        def token(value: Any, limit: int = 128) -> str | None:
            text = str(value or "")[:limit]
            return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else None

        def bounded_count(value: Any) -> int:
            return int(value) if isinstance(value, (int, float)) and 0 <= int(value) <= 100 else 0

        def viewport_dimension(value: Any) -> int:
            return (
                int(value)
                if isinstance(value, (int, float)) and 0 <= int(value) <= 100_000
                else 0
            )

        def node(value: Any) -> dict[str, Any] | None:
            if not isinstance(value, dict):
                return None
            classes = [
                item
                for item in (token(item, 60) for item in (value.get("class_summary") or [])[:4])
                if item is not None
            ]
            return {
                "id": token(value.get("id")),
                "tag": token(value.get("tag"), 40),
                "role": token(value.get("role"), 40),
                "aria_haspopup": token(value.get("aria_haspopup"), 40),
                "aria_expanded": token(value.get("aria_expanded"), 20),
                "aria_controls": token(value.get("aria_controls")),
                "connected": bool(value.get("connected")),
                "visible": bool(value.get("visible")),
                "hover": bool(value.get("hover")),
                "bounding_box": _normalize_submenu_box(value.get("bounding_box")),
                "class_summary": classes,
            }

        expected_id = token(expected_panel_id)
        current_id = token(raw.get("current_aria_controls"))
        target_count = bounded_count(raw.get("controlled_target_count"))
        menu_count = bounded_count(raw.get("controlled_role_menu_count"))
        panel = node(raw.get("panel"))
        exact_count = bounded_count(raw.get("one_k_exact_match_count"))
        visible_count = bounded_count(raw.get("one_k_visible_match_count"))
        enabled_count = bounded_count(raw.get("one_k_enabled_match_count"))
        visible_enabled_count = bounded_count(
            raw.get("one_k_visible_enabled_match_count")
        )
        same_panel = bool(
            expected_id
            and current_id == expected_id
            and target_count == 1
            and menu_count == 1
            and panel is not None
        )
        hit_owned = bool(raw.get("hit_owned_by_panel"))
        pointer_inside = bool(
            hit_owned and panel is not None and panel.get("hover")
        )

        parent_connected = bool(raw.get("parent_connected"))
        parent_expanded = token(raw.get("parent_aria_expanded"), 20)
        if current_id is not None and current_id != expected_id:
            outcome = "panel_identity_changed"
        elif target_count > 1 or menu_count > 1:
            outcome = "panel_identity_changed"
        elif not parent_connected or parent_expanded != "true" or current_id != expected_id:
            outcome = "controlled_relationship_lost"
        elif target_count == 0 or panel is None or not panel["connected"]:
            outcome = "panel_disconnected"
        elif not panel["visible"]:
            outcome = "panel_not_visible"
        elif not pointer_inside:
            outcome = "pointer_not_inside_panel"
        elif exact_count == 0:
            outcome = "exact_1k_not_present"
        elif visible_count != 1 or visible_enabled_count != 1:
            outcome = "exact_1k_present_not_ready"
        else:
            outcome = "handoff_succeeded"

        viewport = raw.get("viewport") if isinstance(raw.get("viewport"), dict) else {}
        return {
            "schema": "nano-banana-after-hover-handoff/v1",
            "outcome": outcome,
            "expected_panel_id": expected_id,
            "current_aria_controls": current_id,
            "same_controlled_panel": same_panel,
            "parent_connected": parent_connected,
            "parent_aria_expanded": parent_expanded,
            "controlled_target_count": target_count,
            "controlled_role_menu_count": menu_count,
            "panel": panel,
            "handoff_coordinate": {
                "x": round(float(handoff_coordinate["x"]), 3),
                "y": round(float(handoff_coordinate["y"]), 3),
            },
            "viewport": {
                "width": viewport_dimension(viewport.get("width")),
                "height": viewport_dimension(viewport.get("height")),
            },
            "hit_target": node(raw.get("hit_target")),
            "hit_owned_by_panel": hit_owned,
            "pointer_inside_panel": pointer_inside,
            "one_k": {
                "exact_match_count": exact_count,
                "visible_match_count": visible_count,
                "enabled_match_count": enabled_count,
                "visible_enabled_match_count": visible_enabled_count,
                "belongs_to_controlled_panel": bool(
                    raw.get("one_k_belongs_to_controlled_panel")
                ),
            },
        }

    def _capture_after_hover_handoff_snapshot(
        self,
        parent,
        *,
        expected_panel_id: str,
        handoff_coordinate: dict[str, float],
    ) -> dict[str, Any]:
        """Atomically inspect the exact panel immediately after probe handoff input."""
        raw = parent.evaluate(
            r"""
            (element, args) => {
              const safeToken = (value, limit = 128) => {
                const text = String(value || "").slice(0, limit);
                return /^[A-Za-z0-9_.:-]{1,128}$/.test(text) ? text : null;
              };
              const visible = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== "hidden" && style.display !== "none" &&
                  Number.parseFloat(style.opacity || "1") > 0.01;
              };
              const box = (node) => {
                if (!(node instanceof Element)) return null;
                const rect = node.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const facts = (node) => node instanceof Element ? {
                id: safeToken(node.id), tag: node.tagName.toLowerCase(),
                role: safeToken(node.getAttribute("role"), 40),
                aria_haspopup: safeToken(node.getAttribute("aria-haspopup"), 40),
                aria_expanded: safeToken(node.getAttribute("aria-expanded"), 20),
                aria_controls: safeToken(node.getAttribute("aria-controls")),
                connected: node.isConnected, visible: visible(node), hover: node.matches(":hover"),
                bounding_box: box(node),
                class_summary: [...node.classList]
                  .filter((value) => /^[A-Za-z0-9_-]{1,60}$/.test(value)).slice(0, 4),
              } : null;
              const expectedId = String(args.expectedPanelId || "");
              const currentControls = element.getAttribute("aria-controls") || "";
              const targets = [...document.querySelectorAll("[id]")].filter(
                (node) => node.id === expectedId
              );
              const menus = [];
              for (const target of targets) {
                if (target.matches("[role='menu']")) menus.push(target);
                menus.push(...target.querySelectorAll("[role='menu']"));
              }
              const uniqueMenus = [...new Set(menus)];
              const panel = uniqueMenus.length === 1 ? uniqueMenus[0] : null;
              const point = {
                x: Number(args.handoffCoordinate?.x),
                y: Number(args.handoffCoordinate?.y),
              };
              const hit = Number.isFinite(point.x) && Number.isFinite(point.y)
                ? document.elementFromPoint(point.x, point.y) : null;
              const items = panel
                ? [...panel.querySelectorAll("[role='menuitem']")]
                : [];
              const exact = items.filter((item) => {
                const label = String(
                  item.getAttribute("aria-label") || item.innerText || item.textContent || ""
                ).replace(/\s+/g, " ").trim();
                return /^1K Original size$/i.test(label);
              });
              const exactVisible = exact.filter(visible);
              const enabled = (item) => !item.disabled &&
                (item.getAttribute("aria-disabled") || "").toLowerCase() !== "true";
              return {
                parent_connected: element.isConnected,
                parent_aria_expanded: element.getAttribute("aria-expanded"),
                current_aria_controls: currentControls || null,
                controlled_target_count: targets.length,
                controlled_role_menu_count: uniqueMenus.length,
                panel: facts(panel),
                viewport: {width: window.innerWidth, height: window.innerHeight},
                hit_target: facts(hit),
                hit_owned_by_panel: Boolean(
                  panel && hit && (hit === panel || panel.contains(hit))
                ),
                one_k_exact_match_count: exact.length,
                one_k_visible_match_count: exactVisible.length,
                one_k_enabled_match_count: exact.filter(enabled).length,
                one_k_visible_enabled_match_count: exactVisible.filter(enabled).length,
                one_k_belongs_to_controlled_panel: Boolean(
                  exact.length && exact.every((item) => panel && panel.contains(item))
                ),
              };
            }
            """,
            {
                "expectedPanelId": expected_panel_id,
                "handoffCoordinate": handoff_coordinate,
            },
            timeout=1_000,
        )
        return self._normalize_after_hover_handoff_snapshot(
            raw,
            expected_panel_id=expected_panel_id,
            handoff_coordinate=handoff_coordinate,
        )

    @staticmethod
    def _normalize_hover_handoff_target(
        raw: Any,
        *,
        expected_controls_id: str | None = None,
    ) -> dict[str, Any]:
        """Classify the one narrow non-opacity physical handoff exception."""
        if not isinstance(raw, dict):
            raw = {}

        def safe_id(value: Any) -> str | None:
            text = str(value or "")
            return (
                text
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", text)
                else None
            )

        def bounded_count(value: Any) -> int:
            return (
                int(value)
                if isinstance(value, (int, float)) and 0 <= int(value) <= 100
                else 0
            )

        def point(value: Any) -> dict[str, float] | None:
            if not isinstance(value, dict):
                return None
            x = value.get("x")
            y = value.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return None
            if not -100_000 <= float(x) <= 100_000:
                return None
            if not -100_000 <= float(y) <= 100_000:
                return None
            return {"x": round(float(x), 3), "y": round(float(y), 3)}

        def node_state(value: Any) -> dict[str, Any]:
            source = value if isinstance(value, dict) else {}
            bounding_box = _normalize_submenu_box(source.get("bounding_box"))
            visibility = _clean_text(str(source.get("visibility") or ""), 20).lower()
            display = _clean_text(str(source.get("display") or ""), 20).lower()
            opacity = _clean_text(str(source.get("opacity") or ""), 40)
            pointer_events = _clean_text(
                str(source.get("pointer_events") or ""), 20
            ).lower()
            opacity_source = opacity or "1"
            opacity_match = re.match(
                r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
                opacity_source,
            )
            opacity_value = (
                float(opacity_match.group(0)) if opacity_match is not None else None
            )
            clauses = {
                "is_element": bool(source.get("is_element")),
                "connected": bool(source.get("connected")),
                "positive_width": bool(
                    bounding_box is not None and bounding_box["width"] > 0
                ),
                "positive_height": bool(
                    bounding_box is not None and bounding_box["height"] > 0
                ),
                "visibility_not_hidden": visibility != "hidden",
                "display_not_none": display != "none",
                "opacity_parseable": opacity_value is not None,
                "opacity_above_threshold": bool(
                    opacity_value is not None and opacity_value > 0.01
                ),
            }
            non_opacity_clauses = {
                key: passed
                for key, passed in clauses.items()
                if key != "opacity_above_threshold"
            }
            return {
                "id": safe_id(source.get("id")),
                "tag": _clean_text(str(source.get("tag") or ""), 40).lower()
                or None,
                "role": _clean_text(str(source.get("role") or ""), 40).lower()
                or None,
                "bounding_box": bounding_box,
                "visibility": visibility or None,
                "display": display or None,
                "opacity": opacity or None,
                "pointer_events": pointer_events or None,
                "clauses": clauses,
                "strict_production_visible": all(clauses.values()),
                "handoff_enterable_without_hit": bool(
                    all(non_opacity_clauses.values())
                    and pointer_events
                    and pointer_events != "none"
                ),
            }

        current_controls_id = safe_id(raw.get("aria_controls"))
        expected_id = (
            safe_id(expected_controls_id)
            if expected_controls_id is not None
            else None
        )
        target_count = bounded_count(raw.get("controlled_target_count"))
        target_connected_count = bounded_count(
            raw.get("controlled_target_connected_count")
        )
        target_visible_count = bounded_count(
            raw.get("controlled_target_visible_count")
        )
        menu_count = bounded_count(raw.get("controlled_role_menu_count"))
        menu_connected_count = bounded_count(
            raw.get("controlled_role_menu_connected_count")
        )
        menu_visible_count = bounded_count(
            raw.get("controlled_role_menu_visible_count")
        )
        target = node_state(raw.get("controlled_target_facts"))
        menu = node_state(raw.get("controlled_role_menu_facts"))
        handoff_point = point(raw.get("handoff_point"))
        relationship_unchanged = bool(
            raw.get("parent_connected")
            and str(raw.get("parent_role") or "").lower() == "menuitem"
            and str(raw.get("aria_haspopup") or "").lower() == "menu"
            and str(raw.get("aria_expanded") or "").lower() == "true"
            and raw.get("controls_id_valid") is True
            and current_controls_id is not None
            and (expected_id is None or current_controls_id == expected_id)
        )
        structural_unique = bool(
            target_count == 1
            and target_connected_count == 1
            and menu_count == 1
            and menu_connected_count == 1
            and target["id"] == current_controls_id
            and menu["role"] == "menu"
        )
        strict_production_visible = bool(
            structural_unique
            and target_visible_count == 1
            and menu_visible_count == 1
            and (
                target["strict_production_visible"]
                or menu["strict_production_visible"]
            )
            and menu["strict_production_visible"]
        )
        hit_owned = raw.get("handoff_hit_owned") is True
        handoff_enterable = bool(
            relationship_unchanged
            and structural_unique
            and target["handoff_enterable_without_hit"]
            and menu["handoff_enterable_without_hit"]
            and handoff_point is not None
            and hit_owned
        )
        opacity_only_difference = bool(
            handoff_enterable
            and not strict_production_visible
            and (
                not target["clauses"]["opacity_above_threshold"]
                or not menu["clauses"]["opacity_above_threshold"]
            )
        )
        return {
            "parent_connected": bool(raw.get("parent_connected")),
            "parent_role": _clean_text(str(raw.get("parent_role") or ""), 40).lower()
            or None,
            "aria_haspopup": _clean_text(
                str(raw.get("aria_haspopup") or ""), 40
            ).lower()
            or None,
            "aria_expanded": _clean_text(
                str(raw.get("aria_expanded") or ""), 20
            ).lower()
            or None,
            "aria_controls": current_controls_id,
            "controls_id_valid": raw.get("controls_id_valid") is True,
            "relationship_unchanged": relationship_unchanged,
            "controlled_target_count": target_count,
            "controlled_target_connected_count": target_connected_count,
            "controlled_target_visible_count": target_visible_count,
            "controlled_role_menu_count": menu_count,
            "controlled_role_menu_connected_count": menu_connected_count,
            "controlled_role_menu_visible_count": menu_visible_count,
            "controlled_target": target,
            "controlled_role_menu": menu,
            "handoff_point": handoff_point,
            "handoff_hit_owned": hit_owned,
            "strict_production_visible": strict_production_visible,
            "handoff_enterable": handoff_enterable,
            "opacity_only_strict_visibility_difference": opacity_only_difference,
        }

    def _current_hover_handoff_target(
        self,
        parent,
        *,
        expected_controls_id: str | None = None,
        handoff_coordinate: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Atomically resolve one exact panel for physical hover entry only."""
        raw = parent.evaluate(
            r"""
            (element, args) => {
              const strictVisible = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== "hidden" && style.display !== "none" &&
                  Number.parseFloat(style.opacity || "1") > 0.01;
              };
              const enterable = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== "hidden" && style.display !== "none" &&
                  !Number.isNaN(Number.parseFloat(style.opacity || "1")) &&
                  style.pointerEvents !== "none";
              };
              const facts = (node) => {
                if (!(node instanceof Element)) return {is_element: false};
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return {
                  is_element: true,
                  id: node.id || null,
                  tag: node.tagName.toLowerCase(),
                  role: node.getAttribute("role"),
                  connected: node.isConnected,
                  bounding_box: {
                    x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                  },
                  visibility: style.visibility,
                  display: style.display,
                  opacity: style.opacity,
                  pointer_events: style.pointerEvents,
                };
              };
              const controlsId = element.getAttribute("aria-controls") || "";
              const controlsIdValid = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(
                controlsId
              );
              const expectedMatches = !args.expectedControlsId ||
                controlsId === args.expectedControlsId;
              const targets = controlsIdValid && expectedMatches
                ? [...document.querySelectorAll("[id]")].filter(
                    (node) => node.id === controlsId
                  )
                : [];
              const menus = [];
              for (const target of targets) {
                if (target.matches("[role='menu']")) menus.push(target);
                menus.push(...target.querySelectorAll("[role='menu']"));
              }
              const uniqueMenus = [...new Set(menus)];
              const connectedMenus = uniqueMenus.filter((node) => node.isConnected);
              const visibleMenus = connectedMenus.filter(strictVisible);
              const connectedTargets = targets.filter((node) => node.isConnected);
              const visibleTargets = connectedTargets.filter(
                (target) => strictVisible(target) || visibleMenus.some(
                  (menu) => target === menu || target.contains(menu)
                )
              );
              const target = targets.length === 1 ? targets[0] : null;
              const menu = uniqueMenus.length === 1 ? uniqueMenus[0] : null;
              const relationshipValid = Boolean(
                element.isConnected &&
                element.getAttribute("role") === "menuitem" &&
                (element.getAttribute("aria-haspopup") || "").toLowerCase() === "menu" &&
                (element.getAttribute("aria-expanded") || "").toLowerCase() === "true" &&
                controlsIdValid && expectedMatches
              );
              const structurallyEnterable = Boolean(
                relationshipValid && targets.length === 1 &&
                connectedTargets.length === 1 && uniqueMenus.length === 1 &&
                connectedMenus.length === 1 && enterable(target) && enterable(menu)
              );
              let points = [];
              if (
                args.handoffCoordinate &&
                Number.isFinite(args.handoffCoordinate.x) &&
                Number.isFinite(args.handoffCoordinate.y)
              ) {
                points = [args.handoffCoordinate];
              } else if (menu instanceof Element) {
                const rect = menu.getBoundingClientRect();
                const insetX = Math.min(12, Math.max(2, rect.width / 4));
                const insetY = Math.min(12, Math.max(2, rect.height / 4));
                points = [
                  {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2},
                  {x: rect.left + insetX, y: rect.top + insetY},
                  {x: rect.right - insetX, y: rect.top + insetY},
                  {x: rect.left + insetX, y: rect.bottom - insetY},
                  {x: rect.right - insetX, y: rect.bottom - insetY},
                ];
              }
              let handoffPoint = null;
              let handoffHitOwned = false;
              if (structurallyEnterable) {
                for (const point of points) {
                  if (
                    point.x < 0 || point.y < 0 ||
                    point.x >= window.innerWidth || point.y >= window.innerHeight
                  ) continue;
                  const hit = document.elementFromPoint(point.x, point.y);
                  if (
                    hit && (hit === target || target.contains(hit)) &&
                    (hit === menu || menu.contains(hit))
                  ) {
                    handoffPoint = point;
                    handoffHitOwned = true;
                    break;
                  }
                }
              }
              return {
                parent_connected: element.isConnected,
                parent_role: element.getAttribute("role"),
                aria_haspopup: element.getAttribute("aria-haspopup"),
                aria_expanded: element.getAttribute("aria-expanded"),
                aria_controls: controlsId || null,
                controls_id_valid: controlsIdValid,
                controlled_target_count: targets.length,
                controlled_target_connected_count: connectedTargets.length,
                controlled_target_visible_count: visibleTargets.length,
                controlled_role_menu_count: uniqueMenus.length,
                controlled_role_menu_connected_count: connectedMenus.length,
                controlled_role_menu_visible_count: visibleMenus.length,
                controlled_target_facts: facts(target),
                controlled_role_menu_facts: facts(menu),
                handoff_point: handoffPoint,
                handoff_hit_owned: handoffHitOwned,
              };
            }
            """,
            {
                "expectedControlsId": expected_controls_id,
                "handoffCoordinate": handoff_coordinate,
            },
            timeout=500,
        )
        return self._normalize_hover_handoff_target(
            raw,
            expected_controls_id=expected_controls_id,
        )

    def _handoff_hover_opened_download_submenu(
        self,
        parent,
        diagnostics: dict[str, Any],
    ):
        """Move into one exact hover-opened ARIA-controlled submenu, if proven."""
        try:
            raw = self._current_hover_handoff_target(parent)
        except Exception as exc:
            cancellation_checkpoint("download_submenu_wait")
            diagnostics["hover_controlled_handoff_probe_error"] = _safe_trace_header(
                str(exc)
            )
            return None

        expanded = _clean_text(str(raw.get("aria_expanded") or ""), 20).lower()
        controls_raw = str(raw.get("aria_controls") or "").strip()
        controls_id = (
            controls_raw
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", controls_raw)
            else None
        )
        target_count = int(raw.get("controlled_target_count") or 0)
        menu_count = int(raw.get("controlled_role_menu_count") or 0)
        diagnostics["hover_controlled_handoff_poll_count"] = int(
            diagnostics.get("hover_controlled_handoff_poll_count") or 0
        ) + 1
        diagnostics["aria_expanded"] = expanded or None
        diagnostics["aria_controls"] = controls_id
        if expanded == "true":
            diagnostics["expanded_seen"] = True
            diagnostics["aria_expanded_ever_true"] = True
        if controls_id is not None:
            diagnostics["aria_controls_ever_non_null"] = True
        if target_count:
            diagnostics["controlled_panel_seen_temporarily"] = True

        if diagnostics.get("_passive_parent_trace_enabled"):
            self._observe_before_hover_handoff_visibility(
                parent,
                diagnostics,
                expanded=expanded or None,
                controls_id=controls_id,
                source="handoff_decision",
            )

        if expanded == "true" and controls_raw and controls_id is None:
            diagnostics["hover_controlled_handoff_unsafe_controls_id"] = True
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_AMBIGUOUS: hover-opened Download parent exposed an "
                "unsafe aria-controls value."
            )
        if expanded == "true" and (target_count > 1 or menu_count > 1):
            diagnostics["ambiguity_persisted"] = True
            diagnostics["hover_controlled_handoff_ambiguous"] = True
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_AMBIGUOUS: hover-opened aria-controls target or its "
                "role=menu descendant was not unique."
            )

        def revalidation_summary(value: dict[str, Any]) -> dict[str, Any]:
            return {
                "relationship_unchanged": bool(value.get("relationship_unchanged")),
                "controlled_target_count": int(
                    value.get("controlled_target_count") or 0
                ),
                "controlled_target_connected_count": int(
                    value.get("controlled_target_connected_count") or 0
                ),
                "controlled_role_menu_count": int(
                    value.get("controlled_role_menu_count") or 0
                ),
                "controlled_role_menu_connected_count": int(
                    value.get("controlled_role_menu_connected_count") or 0
                ),
                "hit_owned": value.get("handoff_hit_owned") is True,
                "strict_production_visible": bool(
                    value.get("strict_production_visible")
                ),
                "handoff_enterable": bool(value.get("handoff_enterable")),
                "opacity_only_strict_visibility_difference": bool(
                    value.get("opacity_only_strict_visibility_difference")
                ),
                "passed": bool(value.get("handoff_enterable")),
            }

        diagnostics["strict_production_visible_before_handoff"] = bool(
            raw.get("strict_production_visible")
        )
        diagnostics["handoff_enterable"] = bool(raw.get("handoff_enterable"))
        diagnostics["handoff_opacity_only_strict_visibility_difference"] = bool(
            raw.get("opacity_only_strict_visibility_difference")
        )
        diagnostics["handoff_hit_owned"] = raw.get("handoff_hit_owned") is True
        if not raw.get("handoff_enterable"):
            return None

        try:
            prepared = self._current_hover_handoff_target(
                parent,
                expected_controls_id=controls_id,
            )
        except Exception as exc:
            cancellation_checkpoint("download_submenu_wait")
            diagnostics["hover_controlled_handoff_pre_move_error"] = _safe_trace_header(
                str(exc)
            )
            diagnostics["hover_controlled_handoff_lost"] = True
            return None
        diagnostics["hover_controlled_handoff_pre_move_revalidation"] = (
            revalidation_summary(prepared)
        )
        if (
            int(prepared.get("controlled_target_count") or 0) > 1
            or int(prepared.get("controlled_role_menu_count") or 0) > 1
        ):
            diagnostics["ambiguity_persisted"] = True
            diagnostics["hover_controlled_handoff_ambiguous"] = True
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_AMBIGUOUS: controlled submenu became ambiguous "
                "before hover handoff."
            )
        if not prepared.get("handoff_enterable"):
            diagnostics["hover_controlled_handoff_lost"] = True
            return None

        diagnostics["strict_production_visible_before_handoff"] = bool(
            prepared.get("strict_production_visible")
        )
        diagnostics["handoff_enterable"] = True
        diagnostics["handoff_opacity_only_strict_visibility_difference"] = bool(
            prepared.get("opacity_only_strict_visibility_difference")
        )
        diagnostics["handoff_hit_owned"] = prepared.get("handoff_hit_owned") is True
        handoff_point = prepared.get("handoff_point")
        try:
            handoff_x = float(handoff_point["x"])
            handoff_y = float(handoff_point["y"])
        except (KeyError, TypeError, ValueError):
            return None
        diagnostics["hover_controlled_handoff_attempted"] = True
        diagnostics["hover_controlled_handoff_point"] = {
            "x": handoff_x,
            "y": handoff_y,
        }
        interruptible_call(
            "download_submenu_wait", "submenu_pointer_handoff",
            self._require_page().mouse.move, handoff_x, handoff_y,
        )
        diagnostics["hover_controlled_handoff_mouse_move_completed"] = True

        try:
            post_move = self._current_hover_handoff_target(
                parent,
                expected_controls_id=controls_id,
                handoff_coordinate=diagnostics["hover_controlled_handoff_point"],
            )
        except Exception as exc:
            cancellation_checkpoint("download_submenu_wait")
            diagnostics["hover_controlled_handoff_post_move_revalidation"] = {
                "passed": False,
                "error": _safe_trace_header(str(exc)),
            }
            diagnostics["hover_controlled_handoff_lost"] = True
            return None
        diagnostics["hover_controlled_handoff_post_move_revalidation"] = (
            revalidation_summary(post_move)
        )
        if diagnostics.get("_passive_parent_trace_enabled"):
            try:
                diagnostics["after_hover_handoff"] = (
                    self._capture_after_hover_handoff_snapshot(
                        parent,
                        expected_panel_id=controls_id,
                        handoff_coordinate=diagnostics[
                            "hover_controlled_handoff_point"
                        ],
                    )
                )
            except Exception as exc:
                cancellation_checkpoint("download_submenu_wait")
                diagnostics["after_hover_handoff"] = {
                    "schema": "nano-banana-after-hover-handoff/v1",
                    "outcome": "diagnostic_snapshot_failed",
                    "error": _safe_trace_header(str(exc)),
                }

        if (
            int(post_move.get("controlled_target_count") or 0) > 1
            or int(post_move.get("controlled_role_menu_count") or 0) > 1
        ):
            diagnostics["ambiguity_persisted"] = True
            diagnostics["hover_controlled_handoff_ambiguous"] = True
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_AMBIGUOUS: controlled submenu became ambiguous "
                "after hover handoff."
            )
        if not post_move.get("handoff_enterable"):
            diagnostics["hover_controlled_handoff_lost"] = True
            return None

        try:
            matches, facts = self._current_associated_download_submenu(parent)
        except Exception as exc:
            cancellation_checkpoint("download_submenu_wait")
            diagnostics["hover_controlled_handoff_verify_error"] = _safe_trace_header(
                str(exc)
            )
            diagnostics["hover_controlled_handoff_lost"] = True
            return None
        diagnostics.update(facts)
        if len(matches) > 1:
            diagnostics["ambiguity_persisted"] = True
            diagnostics["hover_controlled_handoff_ambiguous"] = True
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_AMBIGUOUS: controlled submenu became ambiguous "
                "during hover handoff."
            )
        if (
            len(matches) == 1
            and facts.get("aria_expanded") == "true"
            and facts.get("aria_controls") == controls_id
            and facts.get("relationship_basis") == "aria_controls"
        ):
            diagnostics["controlled_submenu_ever_appeared"] = True
            diagnostics["expanded_transition_confirmed"] = True
            diagnostics["hover_controlled_handoff_succeeded"] = True
            return matches[0]

        diagnostics["hover_controlled_handoff_lost"] = True
        return None

    @staticmethod
    def _record_download_parent_probe_facts(
        diagnostics: dict[str, Any],
        facts: dict[str, Any],
    ) -> None:
        """Retain only safe current-parent facts for diagnostic failure reporting."""
        parent = facts.get("current_download_parent")
        if isinstance(parent, dict):
            diagnostics["final_parent"] = {
                "connected": parent.get("connected"),
                "visible": parent.get("visible"),
                "enabled": parent.get("enabled"),
                "pointer_events": parent.get("pointer_events"),
                "aria_expanded": diagnostics.get("aria_expanded"),
                "aria_controls": diagnostics.get("aria_controls"),
            }
        elif int(facts.get("semantic_download_parent_count") or 0) == 0:
            diagnostics["final_parent"] = {
                "connected": False,
                "visible": False,
                "enabled": False,
                "pointer_events": None,
                "aria_expanded": None,
                "aria_controls": None,
            }

    def _wait_for_associated_download_submenu(
        self,
        parent,
        diagnostics: dict[str, Any],
        *,
        timeout_ms: int,
    ):
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error: Exception | None = None
        previous_controls_id: str | None = None
        ambiguity_observed = False
        while time.monotonic() < deadline:
            cancellation_checkpoint("download_submenu_wait")
            try:
                parents, parent_facts = self._current_download_submenu_parents()
            except Exception as exc:
                cancellation_checkpoint("download_submenu_wait")
                last_error = exc
                diagnostics["poll_count"] = int(diagnostics.get("poll_count") or 0) + 1
                page.wait_for_timeout(100)
                continue
            diagnostics.update(parent_facts)
            self._record_download_parent_probe_facts(diagnostics, parent_facts)
            if len(parents) > 1:
                ambiguity_observed = True
                diagnostics["transition_or_remount_observed"] = True
                diagnostics["ambiguous_download_parent_count"] = len(parents)
                diagnostics["poll_count"] = int(diagnostics.get("poll_count") or 0) + 1
                page.wait_for_timeout(100)
                continue
            if not parents:
                diagnostics["poll_count"] = int(diagnostics.get("poll_count") or 0) + 1
                page.wait_for_timeout(100)
                continue

            current_parent = parents[0]
            self._maybe_checkpoint_download_parent_probe(
                current_parent,
                diagnostics,
                "submenu_poll",
                diagnostics.get("_download_parent_trace_pointer"),
            )
            if (
                diagnostics.get("hover_mouse_move_completed") is True
                and diagnostics.get("hover_controlled_handoff_attempted") is not True
                and diagnostics.get("hover_controlled_handoff_lost") is not True
            ):
                hover_submenu = self._handoff_hover_opened_download_submenu(
                    current_parent,
                    diagnostics,
                )
                if hover_submenu is not None:
                    return hover_submenu
            try:
                matches, facts = self._current_associated_download_submenu(current_parent)
            except Exception as exc:
                cancellation_checkpoint("download_submenu_wait")
                last_error = exc
                diagnostics["poll_count"] = int(diagnostics.get("poll_count") or 0) + 1
                page.wait_for_timeout(100)
                continue
            diagnostics.update(facts)
            diagnostics["poll_count"] = int(diagnostics.get("poll_count") or 0) + 1
            controls_id = facts.get("aria_controls")
            if diagnostics.get("_passive_parent_trace_enabled"):
                self._observe_before_hover_handoff_visibility(
                    current_parent,
                    diagnostics,
                    expanded=facts.get("aria_expanded"),
                    controls_id=controls_id,
                    source="associated_submenu_recheck",
                )
            final_parent = diagnostics.setdefault("final_parent", {})
            final_parent["aria_expanded"] = facts.get("aria_expanded")
            final_parent["aria_controls"] = controls_id
            diagnostics["aria_controls_ever_non_null"] = bool(
                diagnostics.get("aria_controls_ever_non_null") or controls_id
            )
            diagnostics["controlled_panel_seen_temporarily"] = bool(
                diagnostics.get("controlled_panel_seen_temporarily")
                or facts.get("controlled_panel_found")
            )
            diagnostics["controlled_submenu_ever_appeared"] = bool(
                diagnostics.get("controlled_submenu_ever_appeared") or matches
            )
            if (
                previous_controls_id is not None
                and controls_id is not None
                and controls_id != previous_controls_id
            ):
                diagnostics["transition_or_remount_observed"] = True
                diagnostics["aria_controls_changed"] = True
            if controls_id is not None:
                previous_controls_id = str(controls_id)
            if facts.get("aria_expanded") == "true":
                diagnostics["expanded_seen"] = True
                diagnostics["aria_expanded_ever_true"] = True
            if int(facts.get("visible_menu_panel_count") or 0) > 1:
                diagnostics["multiple_visible_panels_observed"] = True
            if len(matches) > 1:
                ambiguity_observed = True
                diagnostics["transition_or_remount_observed"] = True
                diagnostics["controlled_panel_overlap_observed"] = True
                page.wait_for_timeout(100)
                continue
            if (
                len(matches) == 1
                and facts["aria_expanded"] == "true"
                and facts["relationship_basis"] == "aria_controls"
            ):
                diagnostics["expanded_transition_confirmed"] = True
                return matches[0]
            page.wait_for_timeout(100)
        if last_error is not None:
            diagnostics["last_parent_probe_error"] = _safe_trace_header(str(last_error))
        diagnostics["ambiguity_persisted"] = ambiguity_observed
        return None

    def _wait_for_closed_download_submenu_parent(
        self,
        diagnostics: dict[str, Any],
        *,
        timeout_ms: int = 1_500,
    ) -> tuple[Any, dict[str, Any], dict[str, float]]:
        """Reacquire and settle the closed parent without locator actionability waits."""
        if not 250 <= timeout_ms <= 5_000:
            raise ValueError("Download parent settle wait must be between 250 and 5000 ms.")
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            cancellation_checkpoint("download_parent_settle")
            diagnostics["parent_settle_poll_count"] = (
                int(diagnostics.get("parent_settle_poll_count") or 0) + 1
            )
            parents, facts = self._current_download_submenu_parents()
            diagnostics.update(facts)
            self._record_download_parent_probe_facts(diagnostics, facts)
            if len(parents) > 1:
                raise FlowAutomationError(
                    "DOWNLOAD_SUBMENU_AMBIGUOUS: multiple live semantic Download parents."
                )
            if len(parents) == 1:
                current_parent = parents[0]
                try:
                    snapshot = self._inspect_download_menu_item(current_parent)
                    if snapshot.get("role") != "menuitem":
                        raise FlowAutomationError(
                            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: parent is not role=menuitem."
                        )
                    if str(snapshot.get("aria_haspopup") or "").lower() != "menu":
                        raise FlowAutomationError(
                            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: parent lacks aria-haspopup=menu."
                        )
                    if str(snapshot.get("aria_expanded") or "").lower() != "false":
                        raise FlowAutomationError(
                            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: parent is not closed."
                        )
                    stable_box = _stable_download_control_box(snapshot)
                    current_box = self._current_download_control_hit(
                        current_parent,
                        stable_box,
                    )
                    diagnostics["parent_stable"] = True
                    diagnostics["parent_hit_tested"] = True
                    diagnostics["parent_box"] = current_box
                    diagnostics["final_parent"] = {
                        "connected": snapshot.get("connected"),
                        "visible": snapshot.get("visible"),
                        "enabled": not bool(snapshot.get("disabled"))
                        and str(snapshot.get("aria_disabled") or "").lower()
                        != "true",
                        "pointer_events": snapshot.get("pointer_events"),
                        "aria_expanded": snapshot.get("aria_expanded"),
                        "aria_controls": None,
                    }
                    return current_parent, snapshot, current_box
                except Exception as exc:
                    cancellation_checkpoint("download_parent_settle")
                    last_error = exc
            page.wait_for_timeout(100)

        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        raise FlowAutomationError(
            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: closed Download parent did not "
            f"become stable and unobscured within {timeout_ms}ms{detail}"
        )

    def _expose_download_submenu(
        self,
        candidate_id: str,
        parent,
        parent_snapshot: dict[str, Any],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Expose the aria-controlled submenu; never treat the parent as a download action."""
        preserve_partial_diagnostics = diagnostics is not None
        if diagnostics is None:
            diagnostics = {}
        diagnostics.update(
            {
                "candidate_id": candidate_id,
                "parent_role": "menuitem",
                "aria_haspopup": "menu",
                "aria_expanded_before": "false",
                "has_aria_controls_before": bool(
                    parent_snapshot.get("has_aria_controls")
                ),
                "hover_attempted": True,
                "hover_method": "page_mouse_move",
                "hover_mouse_move_completed": False,
                "parent_activation_count": 0,
                "activation_dispatch_began": False,
                "activation_mouse_move_completed": False,
                "activation_mouse_down_completed": False,
                "activation_mouse_up_completed": False,
                "activation_dispatch_returned_normally": False,
                "pre_activation_poll_phase_entered": False,
                "pre_activation_poll_count": 0,
                "post_activation_poll_phase_entered": False,
                "post_activation_poll_count": 0,
                "expanded_transition_confirmed": False,
                "aria_expanded_ever_true": False,
                "aria_controls_ever_non_null": False,
                "controlled_submenu_ever_appeared": False,
                "controlled_panel_seen_temporarily": False,
                "structural_stable_polls": 0,
                "parent_settle_poll_count": 0,
                "parent_stable": False,
                "parent_hit_tested": False,
                "poll_count": 0,
            }
        )
        if parent_snapshot.get("role") != "menuitem":
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: first-level Download is not role=menuitem."
            )
        if str(parent_snapshot.get("aria_haspopup") or "").lower() != "menu":
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: first-level Download lacks aria-haspopup=menu."
            )
        if str(parent_snapshot.get("aria_expanded") or "").lower() != "false":
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: first-level Download was not initially aria-expanded=false."
            )
        prepared_parent_box = _stable_download_control_box(parent_snapshot)

        def wait_for_submenu_phase(control, *, timeout_ms: int, phase: str):
            entered_key = f"{phase}_activation_poll_phase_entered"
            count_key = f"{phase}_activation_poll_count"
            diagnostics[entered_key] = True
            count_before = int(diagnostics.get("poll_count") or 0)
            try:
                return self._wait_for_associated_download_submenu(
                    control,
                    diagnostics,
                    timeout_ms=timeout_ms,
                )
            finally:
                count_after = int(diagnostics.get("poll_count") or 0)
                diagnostics[count_key] = int(diagnostics.get(count_key) or 0) + max(
                    0,
                    count_after - count_before,
                )

        try:
            current_parents, current_facts = self._current_download_submenu_parents()
            diagnostics.update(current_facts)
            self._record_download_parent_probe_facts(diagnostics, current_facts)
            if len(current_parents) > 1:
                raise FlowAutomationError(
                    "DOWNLOAD_SUBMENU_AMBIGUOUS: multiple live semantic Download parents."
                )
            if len(current_parents) != 1:
                raise FlowAutomationError(
                    "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: prepared Download parent "
                    "is no longer uniquely present."
                )
            if (
                str(parent.get_attribute("role") or "").lower() != "menuitem"
                or str(parent.get_attribute("aria-haspopup") or "").lower() != "menu"
                or str(parent.get_attribute("aria-expanded") or "").lower() != "false"
            ):
                raise FlowAutomationError(
                    "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE: prepared Download parent "
                    "changed before hover."
                )
            current_box = self._current_download_control_hit(
                parent,
                prepared_parent_box,
            )
            current_parent = parent
            diagnostics["parent_stable"] = True
            diagnostics["parent_hit_tested"] = True
            diagnostics["parent_box"] = current_box
            diagnostics["final_parent"] = {
                "connected": True,
                "visible": True,
                "enabled": True,
                "pointer_events": parent_snapshot.get("pointer_events"),
                "aria_expanded": "false",
                "aria_controls": None,
            }
        except FlowAutomationError as exc:
            cancellation_checkpoint("download_submenu_wait")
            if str(exc).startswith("DOWNLOAD_SUBMENU_AMBIGUOUS:"):
                raise
            current_parent, _current_snapshot, current_box = (
                self._wait_for_closed_download_submenu_parent(diagnostics)
            )
        except Exception:
            cancellation_checkpoint("download_submenu_wait")
            current_parent, _current_snapshot, current_box = (
                self._wait_for_closed_download_submenu_parent(diagnostics)
            )
        center_x = float(current_box["x"] + current_box["width"] / 2)
        center_y = float(current_box["y"] + current_box["height"] / 2)
        diagnostics["_download_parent_trace_pointer"] = {
            "x": center_x,
            "y": center_y,
        }
        self._maybe_checkpoint_download_parent_probe(
            current_parent,
            diagnostics,
            "before_hover_move",
            diagnostics["_download_parent_trace_pointer"],
        )
        interruptible_call(
            "download_submenu_wait", "download_parent_hover",
            self._require_page().mouse.move, center_x, center_y,
        )
        diagnostics["hover_mouse_move_completed"] = True
        diagnostics["hover_center"] = {"x": center_x, "y": center_y}
        self._maybe_checkpoint_download_parent_probe(
            current_parent,
            diagnostics,
            "after_hover_move",
            diagnostics["_download_parent_trace_pointer"],
        )
        submenu = wait_for_submenu_phase(
            current_parent,
            timeout_ms=1_500,
            phase="pre",
        )
        if submenu is None:
            if diagnostics.get("expanded_seen"):
                submenu = wait_for_submenu_phase(
                    current_parent,
                    timeout_ms=2_000,
                    phase="pre",
                )
            else:
                refreshed_parent, refreshed, _ = (
                    self._wait_for_closed_download_submenu_parent(diagnostics)
                )
                # From this point onward an activation may have been consumed even if
                # Flow remounts/expands the parent during a locator probe. Never retry it.
                diagnostics["parent_activation_count"] = 1
                try:
                    activation_kwargs: dict[str, Any] = {
                        "prepared_snapshot": refreshed,
                    }
                    if preserve_partial_diagnostics:
                        activation_kwargs["diagnostic_trace"] = diagnostics
                    self._activate_download_control(
                        refreshed_parent, "mouse", **activation_kwargs
                    )
                    diagnostics["activation_dispatch_returned_normally"] = True
                except Exception as activation_error:
                    cancellation_checkpoint("download_submenu_wait")
                    diagnostics["parent_activation_probe_error"] = _safe_trace_header(
                        str(activation_error)
                    )
                    submenu = wait_for_submenu_phase(
                        refreshed_parent,
                        timeout_ms=2_000,
                        phase="post",
                    )
                    if submenu is not None:
                        diagnostics["parent_activation_probe_recovered"] = True
                        return submenu, diagnostics
                    diagnostics["final_safe_failure_classification"] = (
                        "DOWNLOAD_SUBMENU_PARENT_PROBE_FAILED"
                    )
                    raise FlowAutomationError(
                        "DOWNLOAD_SUBMENU_PARENT_PROBE_FAILED: Download-parent activation "
                        "became ambiguous and no fresh expanded submenu could be proven; "
                        f"last_probe={_safe_trace_header(str(activation_error))}."
                    ) from activation_error
                submenu = wait_for_submenu_phase(
                    refreshed_parent,
                    timeout_ms=2_000,
                    phase="post",
                )
        if submenu is None:
            safe_facts = {
                key: diagnostics.get(key)
                for key in (
                    "aria_expanded",
                    "aria_controls",
                    "visible_menu_panel_count",
                    "controlled_panel_found",
                    "controlled_panel_connected",
                    "controlled_panel_visible",
                    "visible_1k_in_controlled_panel",
                    "menu_panel_ids",
                    "transition_or_remount_observed",
                    "ambiguity_persisted",
                )
            }
            diagnostics["final_safe_failure_classification"] = (
                "DOWNLOAD_SUBMENU_NOT_FOUND"
            )
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu "
                f"appeared; diagnostics={json.dumps(safe_facts, sort_keys=True)}."
            )
        return submenu, diagnostics

    def _exact_1k_original_size_option(self, submenu) -> tuple[Any, dict[str, Any]]:
        """Return exactly one visible enabled semantic 1K Original size menu item."""
        matches = submenu.get_by_role(
            "menuitem",
            name=re.compile(r"^\s*1K\s+Original\s+size\s*$", re.I),
        ).all()
        visible = []
        for control in matches:
            try:
                facts = self._live_control_facts(control)
            except Exception:
                cancellation_checkpoint("download_1k_readiness_wait")
                continue
            if facts["connected"] and facts["visible"]:
                visible.append((control, facts))
        if len(visible) > 1:
            raise FlowAutomationError(
                f"DOWNLOAD_1K_OPTION_AMBIGUOUS: found {len(visible)} visible semantic matches."
            )
        if not visible:
            raise FlowAutomationError(
                "DOWNLOAD_1K_OPTION_NOT_FOUND: no visible '1K Original size' menu item."
            )
        control, facts = visible[0]
        if not facts["enabled"]:
            raise FlowAutomationError(
                "DOWNLOAD_1K_OPTION_DISABLED: '1K Original size' is disabled."
            )
        snapshot = self._inspect_download_menu_item(control)
        _stable_download_control_box(snapshot)
        return control, snapshot

    @staticmethod
    def _download_submenu_reacquisition_retry_allowed(
        error: FlowAutomationError,
        diagnostics: dict[str, Any],
    ) -> bool:
        """Allow one fresh UI cycle only after a proven inert parent activation."""
        final_parent = diagnostics.get("final_parent")
        final_expanded = (
            final_parent.get("aria_expanded")
            if isinstance(final_parent, dict)
            else diagnostics.get("aria_expanded")
        )
        return bool(
            str(error).startswith(
                "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu appeared"
            )
            and diagnostics.get("final_safe_failure_classification")
            == "DOWNLOAD_SUBMENU_NOT_FOUND"
            and int(diagnostics.get("parent_activation_count") or 0) == 1
            and diagnostics.get("activation_dispatch_returned_normally") is True
            and final_expanded == "false"
            and not diagnostics.get("aria_expanded_ever_true")
            and not diagnostics.get("controlled_submenu_ever_appeared")
            and not diagnostics.get("controlled_panel_seen_temporarily")
            and not diagnostics.get("ambiguity_persisted")
        )

    def _wait_for_current_1k_original_size_option(
        self,
        diagnostics: dict[str, Any],
        *,
        timeout_ms: int = 2_000,
    ) -> tuple[Any, dict[str, Any]]:
        """Reacquire the expanded parent, current panel, and exact 1K item each poll."""
        if not 250 <= timeout_ms <= 5_000:
            raise ValueError("1K submenu wait must be between 250 and 5000 ms.")
        page = self._require_page()
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error: Exception | None = None
        poll_count = 0
        ambiguity_observed = False
        previous_controls_id: str | None = None
        while time.monotonic() < deadline:
            cancellation_checkpoint("download_1k_readiness_wait")
            poll_count += 1
            try:
                parents, parent_facts = self._current_download_submenu_parents()
                diagnostics.update(parent_facts)
                if len(parents) > 1:
                    ambiguity_observed = True
                    diagnostics["transition_or_remount_observed"] = True
                    diagnostics["ambiguous_download_parent_count"] = len(parents)
                    page.wait_for_timeout(100)
                    continue
                if len(parents) != 1:
                    raise FlowAutomationError(
                        "DOWNLOAD_SUBMENU_NOT_FOUND: current Download parent is unavailable."
                    )
                submenus, relationship = self._current_associated_download_submenu(
                    parents[0]
                )
                diagnostics.update(relationship)
                controls_id = relationship.get("aria_controls")
                if (
                    previous_controls_id is not None
                    and controls_id is not None
                    and controls_id != previous_controls_id
                ):
                    diagnostics["transition_or_remount_observed"] = True
                    diagnostics["aria_controls_changed"] = True
                if controls_id is not None:
                    previous_controls_id = str(controls_id)
                if len(submenus) > 1:
                    ambiguity_observed = True
                    diagnostics["transition_or_remount_observed"] = True
                    diagnostics["controlled_panel_overlap_observed"] = True
                    page.wait_for_timeout(100)
                    continue
                if (
                    len(submenus) == 1
                    and relationship.get("aria_expanded") == "true"
                    and relationship.get("relationship_basis") == "aria_controls"
                ):
                    # This panel locator is used only within this poll. A failure
                    # restarts from the parent and obtains a fresh current panel.
                    option, snapshot = self._exact_1k_original_size_option(submenus[0])
                    diagnostics["one_k_poll_count"] = poll_count
                    diagnostics["one_k_relationship_basis"] = "aria_controls"
                    return option, snapshot
            except FlowAutomationError as exc:
                cancellation_checkpoint("download_1k_readiness_wait")
                last_error = exc
            except Exception as exc:
                cancellation_checkpoint("download_1k_readiness_wait")
                last_error = exc
            page.wait_for_timeout(100)

        diagnostics["one_k_poll_count"] = poll_count
        diagnostics["ambiguity_persisted"] = ambiguity_observed
        detail = (
            f"; last_check={_safe_trace_header(str(last_error))}"
            if last_error is not None
            else ""
        )
        safe_facts = {
            key: diagnostics.get(key)
            for key in (
                "aria_expanded",
                "aria_controls",
                "visible_menu_panel_count",
                "controlled_panel_found",
                "controlled_panel_connected",
                "controlled_panel_visible",
                "visible_1k_in_controlled_panel",
                "menu_panel_ids",
                "transition_or_remount_observed",
                "ambiguity_persisted",
            )
        }
        raise FlowAutomationError(
            "DOWNLOAD_1K_OPTION_NOT_READY: no single enabled '1K Original size' "
            f"item was proven inside the current expanded submenu within {timeout_ms}ms"
            f"{detail}; diagnostics={json.dumps(safe_facts, sort_keys=True)}."
        )

    def _mouse_click_control(self, control) -> None:
        """Send a trusted mouse click without waiting on Flow's animated stability."""
        page = self._require_page()
        box = control.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise FlowAutomationError(
                "DOWNLOAD_TRIGGER_NOT_CONFIRMED: control has no positive bounding box."
            )
        x = float(box["x"] + box["width"] / 2)
        y = float(box["y"] + box["height"] / 2)
        page.mouse.move(x, y)
        page.mouse.click(x, y)

    def _inspect_download_menu_item(
        self,
        control,
        *,
        sample_count: int = 4,
        sample_interval_ms: int = 100,
    ) -> dict[str, Any]:
        """Read only whitelisted DOM/actionability facts for the exact Download item."""
        if not 2 <= sample_count <= 8:
            raise ValueError("Download control inspection requires 2 to 8 bounding-box samples.")
        if not 50 <= sample_interval_ms <= 250:
            raise ValueError("Download control inspection interval must be 50 to 250 ms.")
        raw = control.evaluate(
            r"""
            async (element, args) => {
              const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 180);
              const menu = element.closest("[role='menu']");
              const describe = (node, includeText = true) => node instanceof Element ? {
                tag: node.tagName.toLowerCase(),
                role: clean(node.getAttribute("role")) || null,
                text: includeText ? clean(node.textContent) || null : null,
                aria_label: includeText ? clean(node.getAttribute("aria-label")) || null : null,
                title: includeText ? clean(node.getAttribute("title")) || null : null,
                connected: node.isConnected,
                inside_menu: Boolean(menu && menu.contains(node)),
              } : null;
              const toBox = (rect) => ({
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
              });
              const initial = element.getBoundingClientRect();
              const center = {
                x: initial.left + initial.width / 2,
                y: initial.top + initial.height / 2,
              };
              const hits = document.elementsFromPoint(center.x, center.y).slice(0, 8);
              const style = getComputedStyle(element);
              const result = {
                tag: element.tagName.toLowerCase(),
                role: clean(element.getAttribute("role")) || null,
                visible_text: clean(element.textContent) || null,
                aria_label: clean(element.getAttribute("aria-label")) || null,
                title: clean(element.getAttribute("title")) || null,
                disabled: Boolean(element.disabled),
                aria_disabled: clean(element.getAttribute("aria-disabled")) || null,
                aria_haspopup: clean(element.getAttribute("aria-haspopup")) || null,
                aria_expanded: clean(element.getAttribute("aria-expanded")) || null,
                has_aria_controls: element.hasAttribute("aria-controls"),
                has_aria_owns: element.hasAttribute("aria-owns"),
                tabindex: clean(element.getAttribute("tabindex")) || null,
                pointer_events: style.pointerEvents,
                opacity: style.opacity,
                visibility: style.visibility,
                display: style.display,
                connected: element.isConnected,
                visible: element.isConnected && initial.width > 0 && initial.height > 0 &&
                         style.visibility !== "hidden" && style.display !== "none" &&
                         Number.parseFloat(style.opacity || "1") > 0.01,
                bounding_box: toBox(initial),
                center_top: describe(hits[0]),
                elements_from_center: hits.map((node) => describe(node)),
                center_top_belongs_to_control: Boolean(
                  hits[0] && (hits[0] === element || element.contains(hits[0]))
                ),
                descendants: Array.from(element.querySelectorAll("*")).slice(0, 16)
                  .map((node) => describe(node)),
                ancestors: Array.from(function* () {
                  let node = element.parentElement;
                  while (node) { yield node; node = node.parentElement; }
                }()).slice(0, 8).map((node) => describe(node, false)),
                active_element: describe(document.activeElement),
                bounding_box_samples: [],
              };
              for (let index = 0; index < args.sampleCount; index += 1) {
                result.bounding_box_samples.push(toBox(element.getBoundingClientRect()));
                if (index + 1 < args.sampleCount) {
                  await new Promise((resolve) => setTimeout(resolve, args.sampleIntervalMs));
                }
              }
              result.connected = element.isConnected;
              return result;
            }
            """,
            {"sampleCount": sample_count, "sampleIntervalMs": sample_interval_ms},
            timeout=2_000,
        )
        return _normalize_download_control_snapshot(raw)

    def _current_download_control_hit(
        self,
        control,
        prior_box: dict[str, float],
        *,
        tolerance: float = 1.0,
    ) -> dict[str, float]:
        raw = control.evaluate(
            r"""
            (element) => {
              const rect = element.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const top = document.elementFromPoint(x, y);
              const style = getComputedStyle(element);
              return {
                connected: element.isConnected,
                visible: element.isConnected && rect.width > 0 && rect.height > 0 &&
                         style.visibility !== "hidden" && style.display !== "none",
                disabled: Boolean(element.disabled),
                aria_disabled: element.getAttribute("aria-disabled"),
                pointer_events: style.pointerEvents,
                center_top_belongs_to_control: Boolean(
                  top && (top === element || element.contains(top))
                ),
                bounding_box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                bounding_box_samples: [
                  {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                  {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                ],
              };
            }
            """,
            timeout=1_000,
        )
        snapshot = _normalize_download_control_snapshot(raw)
        current = _stable_download_control_box(snapshot, tolerance=tolerance)
        if any(abs(current[key] - prior_box[key]) > tolerance for key in ("x", "y", "width", "height")):
            raise FlowAutomationError(
                "DOWNLOAD_CONTROL_BBOX_UNSTABLE: box changed between stability check and input."
            )
        return current

    def _activate_download_control(
        self,
        control,
        activation: str,
        *,
        prepared_snapshot: dict[str, Any] | None = None,
        diagnostic_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch one conservative, user-selected activation strategy."""
        if activation not in {"locator", "mouse", "keyboard"}:
            raise ValueError("Activation must be locator, mouse, or keyboard.")
        page = self._require_page()
        snapshot = prepared_snapshot or self._inspect_download_menu_item(control)
        # Keep the same strict unobscured elementFromPoint gate for every input
        # primitive, including the minimized-only semantic keyboard path.
        box = _stable_download_control_box(snapshot)
        details: dict[str, Any] = {
            "strategy": activation,
            "control_before": snapshot,
        }
        if activation == "locator":
            control.click(timeout=3_000)
            details["input"] = "locator_click"
            return details
        if activation == "keyboard":
            if snapshot.get("role") != "menuitem":
                raise FlowAutomationError(
                    "DOWNLOAD_CONTROL_NOT_ACTIONABLE: keyboard activation requires "
                    "the exact semantic menuitem."
                )
            current = self._current_download_control_hit(control, box)
            control.focus(timeout=3_000)
            focused = bool(
                control.evaluate(
                    "element => document.activeElement === element || element.contains(document.activeElement)"
                )
            )
            if not focused:
                raise FlowAutomationError(
                    "DOWNLOAD_CONTROL_NOT_ACTIONABLE: keyboard focus did not reach the exact item."
                )
            page.keyboard.down("Enter")
            page.keyboard.up("Enter")
            details.update(
                {
                    "input": "keyboard",
                    "key": "Enter",
                    "focused": True,
                    "hit_verified_before_focus": True,
                    "box_before_focus": current,
                    "active_element_at_dispatch": {
                        "tag": snapshot.get("tag"),
                        "role": snapshot.get("role"),
                        "text": snapshot.get("visible_text"),
                        "connected": True,
                        "inside_menu": True,
                    },
                }
            )
            return details

        current = interruptible_call(
            "download_control_activation", "download_control_hit_test",
            self._current_download_control_hit, control, box,
        )
        x = float(current["x"] + current["width"] / 2)
        y = float(current["y"] + current["height"] / 2)
        trace_pointer = {"x": x, "y": y}
        self._maybe_checkpoint_download_parent_probe(
            control,
            diagnostic_trace,
            "before_activation_move",
            trace_pointer,
        )
        if diagnostic_trace is not None:
            diagnostic_trace["activation_dispatch_began"] = True
        interruptible_call(
            "download_control_activation", "download_control_mouse_move",
            page.mouse.move, x, y,
        )
        if diagnostic_trace is not None:
            diagnostic_trace["activation_mouse_move_completed"] = True
        self._maybe_checkpoint_download_parent_probe(
            control,
            diagnostic_trace,
            "after_activation_move",
            trace_pointer,
        )
        down_box = interruptible_call(
            "download_control_activation", "download_control_before_down_hit_test",
            self._current_download_control_hit, control, current,
        )
        self._maybe_checkpoint_download_parent_probe(
            control,
            diagnostic_trace,
            "before_down",
            trace_pointer,
        )
        interruptible_call(
            "download_control_activation", "download_control_mouse_down",
            page.mouse.down, button="left",
        )
        if diagnostic_trace is not None:
            diagnostic_trace["activation_mouse_down_completed"] = True
        self._maybe_checkpoint_download_parent_probe(
            control,
            diagnostic_trace,
            "after_down",
            trace_pointer,
        )
        try:
            up_box = interruptible_call(
                "download_control_activation", "download_control_before_up_hit_test",
                self._current_download_control_hit, control, down_box,
            )
            self._maybe_checkpoint_download_parent_probe(
                control,
                diagnostic_trace,
                "before_up",
                trace_pointer,
            )
        finally:
            if not cancellation_requested():
                interruptible_call(
                    "download_1k_activation",
                    "download_control_mouse_up",
                    page.mouse.up,
                    button="left",
                )
                if diagnostic_trace is not None:
                    diagnostic_trace["activation_mouse_up_completed"] = True
                self._maybe_checkpoint_download_parent_probe(
                    control,
                    diagnostic_trace,
                    "after_up",
                    trace_pointer,
                )
        details.update(
            {
                "input": "mouse_move_down_up",
                "center": {"x": x, "y": y},
                "hit_verified_before_down": True,
                "hit_verified_before_up": True,
                "box_before_up": up_box,
            }
        )
        if diagnostic_trace is not None:
            diagnostic_trace["activation_dispatch_returned_normally"] = True
        return details

    @staticmethod
    def _download_activation_method(
        window_state: str,
        snapshot: dict[str, Any],
    ) -> str:
        """Use one semantic activation only for the exact minimized 1K menuitem."""
        if (
            window_state == "minimized"
            and snapshot.get("role") == "menuitem"
            and _clean_text(snapshot.get("visible_text")).casefold()
            == "1k original size"
        ):
            return "keyboard"
        return "mouse"

    @staticmethod
    def _new_download_files(
        download_dir: Path,
        before_names: set[str],
    ) -> tuple[list[Path], list[Path]]:
        new_files = [
            path
            for path in download_dir.iterdir()
            if path.is_file() and path.name not in before_names
        ]
        partial = [path for path in new_files if path.name.lower().endswith(".crdownload")]
        complete = [path for path in new_files if path not in partial]
        return complete, partial

    @staticmethod
    def _download_failure_message(
        code: str,
        *,
        candidate_id: str,
        trigger_dispatched: bool,
        menu_item_visible_after_trigger: bool | None,
        begins: list[dict[str, Any]],
        browser_guid: str | None,
        suggested_filename: str | None,
        progress_events: list[dict[str, Any]],
        final_state: str | None,
        complete_files: list[Path],
        partial_files: list[Path],
        timeout_stage: str,
        activation_method: str = "mouse",
        window_state: str = "unknown",
        required_background_flags_present: bool | None = None,
        page_lifecycle_keepalive_applied: bool | str = "unsupported",
        activation_acknowledged: bool = False,
    ) -> str:
        def flag(value: bool | None) -> str:
            if value is None:
                return "unknown"
            return "yes" if value else "no"

        def names(paths: list[Path]) -> str:
            safe_names = [_clean_text(path.name, 100) for path in paths]
            return ",".join(name for name in safe_names if name) or "none"

        lifecycle_status = (
            flag(page_lifecycle_keepalive_applied)
            if isinstance(page_lifecycle_keepalive_applied, bool)
            else "unsupported"
        )

        return (
            f"{code}: candidate={candidate_id}; candidate_tile_matched=yes; "
            "more_options_count=1; download_menu_item_count=1; "
            f"trigger_method={activation_method}; activation_method={activation_method}; "
            f"trigger_dispatched={flag(trigger_dispatched)}; "
            f"activation_acknowledged={flag(activation_acknowledged)}; "
            f"menu_item_visible_after_trigger={flag(menu_item_visible_after_trigger)}; "
            f"window_state={window_state}; "
            "required_background_flags_present="
            f"{flag(required_background_flags_present)}; "
            "page_lifecycle_keepalive_applied="
            f"{lifecycle_status}; "
            f"download_will_begin_count={len(begins)}; "
            f"guid={_clean_text(browser_guid, 100) or 'none'}; "
            f"suggested_filename={_clean_text(suggested_filename, 100) or 'none'}; "
            f"download_progress_count={len(progress_events)}; "
            f"final_cdp_state={_clean_text(final_state, 40) or 'none'}; "
            f"staging_complete={names(complete_files)}; "
            f"staging_partial={names(partial_files)}; timeout_stage={timeout_stage}"
        )

    @staticmethod
    def _install_download_trace_hooks(control, candidate_id: str) -> None:
        control.evaluate(
            r"""
            (targetElement, args) => {
              const candidateId = args.candidateId;
              const KEY = "__nanoBatchDownloadTriggerTraceV2";
              if (window[KEY]) throw new Error("download trigger trace hooks already installed");
              const allowedHost = (host) => {
                host = (host || "").toLowerCase();
                return host === "flow.google.com" || host === "flow-content.google" ||
                       host === "googleusercontent.com" || host.endsWith(".googleusercontent.com");
              };
              const pathShape = (path) => {
                let candidateInPath = false;
                const sourceParts = path.split("/").filter(Boolean);
                const asbPath = sourceParts.length > 0 && sourceParts[0].toLowerCase() === "asb";
                const parts = sourceParts.map((part, index) => {
                  if (part === candidateId) {
                    candidateInPath = true;
                    return "[candidate-id]";
                  }
                  if (asbPath && index > 0) return "[opaque]";
                  if (/^[A-Za-z_][A-Za-z0-9._-]{0,31}$/.test(part)) return part;
                  if (/^\d+$/.test(part)) return "[number]";
                  return "[opaque]";
                });
                return {value: "/" + parts.join("/"), candidateInPath};
              };
              const safeUrl = (raw) => {
                if (!raw) return null;
                try {
                  if (String(raw).startsWith("blob:")) {
                    const inner = new URL(String(raw).slice(5));
                    if (!allowedHost(inner.host)) return null;
                    return {
                      scheme: "blob",
                      host: inner.host.toLowerCase(),
                      path_shape: "/[blob]",
                      query_redacted: Boolean(inner.search),
                      fragment_redacted: Boolean(inner.hash),
                      candidate_id_in_path: inner.pathname.includes(candidateId),
                    };
                  }
                  const url = new URL(String(raw), location.href);
                  if (!/^https?:$/.test(url.protocol) || !allowedHost(url.host)) return null;
                  const shape = pathShape(url.pathname);
                  return {
                    scheme: url.protocol.slice(0, -1),
                    host: url.host.toLowerCase(),
                    path_shape: shape.value,
                    query_redacted: Boolean(url.search),
                    fragment_redacted: Boolean(url.hash),
                    candidate_id_in_path: shape.candidateInPath,
                  };
                } catch {
                  return null;
                }
              };
              const safeHeader = (raw) => {
                if (!raw) return null;
                return String(raw)
                  .replace(/[\r\n]+/g, " ")
                  .replace(/\b(token|secret|authorization|cookie|password)\s*=\s*(?:"[^"]*"|[^;]+)/gi, "$1=[redacted]")
                  .replace(/https?:\/\/[^\s;]+/gi, "[redacted-url]")
                  .replace(/\s+/g, " ")
                  .trim()
                  .slice(0, 200) || null;
              };
              const state = {
                events: [],
                startedAt: performance.now(),
                targetElement,
                targetMenu: targetElement.closest("[role='menu']"),
                inputEventTypes: [
                  "pointerdown", "mousedown", "pointerup", "mouseup", "click", "keydown", "keyup"
                ],
                originalOpen: window.open,
                originalCreateObjectURL: URL.createObjectURL,
                originalAnchorClick: HTMLAnchorElement.prototype.click,
                originalFetch: window.fetch,
              };
              // Publish the cleanup record before any page API is wrapped so a
              // partial installation remains discoverable by the outer finally.
              window[KEY] = state;
              const record = (kind, data = {}) => {
                if (state.events.length >= 250) return null;
                const entry = {
                  kind,
                  offset_ms: Math.max(0, performance.now() - state.startedAt),
                  ...data,
                };
                state.events.push(entry);
                return entry;
              };
              const safeNode = (node) => {
                if (!(node instanceof Element)) return null;
                const label = (node.getAttribute("aria-label") || node.textContent || "")
                  .replace(/\bGoogle Account\b/gi, "[redacted account control]")
                  .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
                  .replace(/\s+/g, " ").trim().slice(0, 120);
                return {
                  tag: node.tagName.toLowerCase(),
                  role: node.getAttribute("role") || node.closest("[role]")?.getAttribute("role") || null,
                  text: label || null,
                  connected: node.isConnected,
                  inside_menu: Boolean(state.targetMenu && state.targetMenu.contains(node)),
                };
              };

              state.openWrapper = function(...args) {
                record("window_open", {url: safeUrl(args[0])});
                return state.originalOpen.apply(this, args);
              };
              state.createObjectURLWrapper = function(object) {
                const result = state.originalCreateObjectURL.call(this, object);
                record("blob_created", {
                  url: safeUrl(result),
                  object_type: object && typeof object.type === "string" ? safeHeader(object.type) : null,
                  object_size: object && Number.isFinite(object.size) ? object.size : null,
                });
                return result;
              };
              state.anchorClickWrapper = function(...args) {
                record("anchor_programmatic_click", {
                  url: safeUrl(this.href),
                  download_attribute: safeHeader(this.getAttribute("download")),
                  tag: "a",
                });
                return state.originalAnchorClick.apply(this, args);
              };
              state.fetchWrapper = function(...args) {
                const input = args[0];
                const init = args[1] || {};
                const rawUrl = typeof input === "string" || input instanceof URL ? input : input && input.url;
                const method = String(init.method || (input && input.method) || "GET").toUpperCase().slice(0, 16);
                const safe = safeUrl(rawUrl);
                if (safe) record("fetch_call", {url: safe, method, resource_type: "fetch"});
                let result;
                try {
                  result = state.originalFetch.apply(this, args);
                } catch (error) {
                  if (safe) record("fetch_failed", {url: safe, method, resource_type: "fetch"});
                  throw error;
                }
                result.then(
                  (response) => {
                    const responseUrl = safeUrl(response.url || rawUrl);
                    if (responseUrl) {
                      record("fetch_response", {
                        url: responseUrl,
                        method,
                        resource_type: "fetch",
                        status: response.status,
                        content_type: safeHeader(response.headers.get("content-type")),
                        content_disposition: safeHeader(response.headers.get("content-disposition")),
                      });
                    }
                  },
                  () => {
                    if (safe) record("fetch_failed", {url: safe, method, resource_type: "fetch"});
                  }
                );
                return result;
              };
              state.inputListener = (event) => {
                const eventTarget = event.target instanceof Element ? event.target : null;
                if (!eventTarget || !(
                  eventTarget === state.targetElement || state.targetElement.contains(eventTarget)
                )) return;
                const described = safeNode(eventTarget) || {};
                const data = {
                  event_type: event.type,
                  is_trusted: Boolean(event.isTrusted),
                  tag: described.tag || null,
                  role: described.role || null,
                  target_label: described.text || null,
                  default_prevented: Boolean(event.defaultPrevented),
                };
                if (event instanceof MouseEvent) {
                  data.button = event.button;
                  data.buttons = event.buttons;
                }
                if (event instanceof KeyboardEvent && ["Enter", " ", "Space", "Spacebar"].includes(event.key)) {
                  data.key = event.key;
                }
                const inputEntry = record("input_event", data);
                queueMicrotask(() => {
                  if (inputEntry) inputEntry.default_prevented = Boolean(event.defaultPrevented);
                });
                if (event.type !== "click") return;
                const anchor = eventTarget.closest("a");
                if (anchor && (
                  anchor === state.targetElement || state.targetElement.contains(anchor)
                )) {
                  record("anchor_activation", {
                    url: safeUrl(anchor.href),
                    download_attribute: safeHeader(anchor.getAttribute("download")),
                    tag: "a",
                    role: anchor.getAttribute("role"),
                    is_trusted: Boolean(event.isTrusted),
                  });
                } else {
                  record("download_control_activation", {
                    tag: state.targetElement.tagName.toLowerCase(),
                    role: state.targetElement.getAttribute("role"),
                    is_trusted: Boolean(event.isTrusted),
                  });
                }
              };
              state.observer = new MutationObserver((mutations) => {
                for (const mutation of mutations) {
                  for (const node of mutation.addedNodes) {
                    if (!(node instanceof Element)) continue;
                    const elements = [node, ...node.querySelectorAll("a,[role='alert'],[role='status']")];
                    for (const element of elements) {
                      if (element instanceof HTMLAnchorElement &&
                          (element.hasAttribute("download") || String(element.href).startsWith("blob:"))) {
                        record("anchor_added", {
                          url: safeUrl(element.href),
                          download_attribute: safeHeader(element.getAttribute("download")),
                          tag: "a",
                        });
                      }
                      const role = element.getAttribute("role");
                      if (role === "alert" || role === "status") {
                        const match = (element.textContent || "").match(/download|saved|failed|error/i);
                        if (match) record("dom_status", {role, matched_keyword: match[0].toLowerCase()});
                      }
                    }
                  }
                }
              });

              window.open = state.openWrapper;
              URL.createObjectURL = state.createObjectURLWrapper;
              HTMLAnchorElement.prototype.click = state.anchorClickWrapper;
              window.fetch = state.fetchWrapper;
              for (const eventType of state.inputEventTypes) {
                document.addEventListener(eventType, state.inputListener, true);
              }
              state.observer.observe(document.documentElement, {childList: true, subtree: true});
            }
            """,
            {"candidateId": candidate_id},
        )

    @staticmethod
    def _install_manual_download_trace_hooks(page, candidate_id: str) -> None:
        """Install passive observers; this function never opens or activates a control."""
        page.evaluate(
            r"""
            (candidateId) => {
              const KEY = "__nanoBatchManualDownloadTraceV1";
              if (window[KEY]) throw new Error("manual download trace hooks already installed");
              const images = [...document.querySelectorAll(`img[data-media-id="${CSS.escape(candidateId)}"]`)];
              if (images.length !== 1) throw new Error("expected one exact candidate image");
              const candidateTile = images[0].closest("flow-image-tile");
              if (!candidateTile) throw new Error("exact candidate tile is missing");

              const allowedHost = (host) => {
                host = (host || "").toLowerCase();
                return host === "flow.google.com" || host === "flow-content.google" ||
                       host === "googleusercontent.com" || host.endsWith(".googleusercontent.com");
              };
              const pathShape = (path) => {
                let candidateInPath = false;
                const sourceParts = path.split("/").filter(Boolean);
                const asbPath = sourceParts.length > 0 && sourceParts[0].toLowerCase() === "asb";
                const parts = sourceParts.map((part, index) => {
                  if (part === candidateId) {
                    candidateInPath = true;
                    return "[candidate-id]";
                  }
                  if (asbPath && index > 0) return "[opaque]";
                  if (/^[A-Za-z_][A-Za-z0-9._-]{0,31}$/.test(part)) return part;
                  if (/^\d+$/.test(part)) return "[number]";
                  return "[opaque]";
                });
                return {value: "/" + parts.join("/"), candidateInPath};
              };
              const safeUrl = (raw) => {
                if (!raw) return null;
                try {
                  if (String(raw).startsWith("blob:")) {
                    const inner = new URL(String(raw).slice(5));
                    if (!allowedHost(inner.host)) return null;
                    return {
                      scheme: "blob", host: inner.host.toLowerCase(), path_shape: "/[blob]",
                      query_redacted: Boolean(inner.search), fragment_redacted: Boolean(inner.hash),
                      candidate_id_in_path: inner.pathname.includes(candidateId),
                    };
                  }
                  const url = new URL(String(raw), location.href);
                  if (!/^https?:$/.test(url.protocol) || !allowedHost(url.host)) return null;
                  const shape = pathShape(url.pathname);
                  return {
                    scheme: url.protocol.slice(0, -1), host: url.host.toLowerCase(),
                    path_shape: shape.value, query_redacted: Boolean(url.search),
                    fragment_redacted: Boolean(url.hash),
                    candidate_id_in_path: shape.candidateInPath,
                  };
                } catch { return null; }
              };
              const safeText = (raw, limit = 120) => String(raw || "")
                .replace(/\bGoogle Account\b/gi, "[redacted account control]")
                .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
                .replace(/\s+/g, " ").trim().slice(0, limit) || null;
              const safeHeader = (raw) => {
                if (!raw) return null;
                return String(raw).replace(/[\r\n]+/g, " ")
                  .replace(/\b(token|secret|authorization|cookie|password)\s*=\s*(?:"[^"]*"|[^;]+)/gi, "$1=[redacted]")
                  .replace(/https?:\/\/[^\s;]+/gi, "[redacted-url]")
                  .replace(/\s+/g, " ").trim().slice(0, 200) || null;
              };
              const visible = (element) => {
                if (!(element instanceof Element) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" &&
                       style.display !== "none";
              };
              const labelOf = (element) => safeText(
                element?.getAttribute("aria-label") || element?.textContent || ""
              );
              const compactLabel = (element) => (labelOf(element) || "")
                .toLowerCase().replace(/[^a-z]+/g, "");
              const safeNode = (node, menu = null) => {
                if (!(node instanceof Element)) return null;
                return {
                  tag: node.tagName.toLowerCase(),
                  role: node.getAttribute("role") || node.closest("[role]")?.getAttribute("role") || null,
                  text: labelOf(node), connected: node.isConnected,
                  inside_menu: Boolean(menu && menu.contains(node)),
                };
              };
              const state = {
                events: [], startedAt: performance.now(), candidateTile,
                candidateMenuRequested: false, targetElement: null, targetMenu: null,
                menuEverOpened: false, downloadControlSeen: false, activationSeen: false,
                menuOpenBeforeActivation: null, activeBeforeActivation: null,
                lastMenuSignature: null,
                inputEventTypes: ["pointerdown", "mousedown", "pointerup", "mouseup", "click"],
                originalOpen: window.open, originalCreateObjectURL: URL.createObjectURL,
                originalAnchorClick: HTMLAnchorElement.prototype.click, originalFetch: window.fetch,
              };
              window[KEY] = state;
              const record = (kind, data = {}) => {
                if (state.events.length >= 250) return null;
                const entry = {kind, offset_ms: Math.max(0, performance.now() - state.startedAt), ...data};
                state.events.push(entry);
                return entry;
              };
              const liveDownloadControls = () => [...document.querySelectorAll("[role='menuitem']")]
                .filter((element) => {
                  const label = compactLabel(element);
                  return visible(element) && (label === "download" || label === "downloaddownload") &&
                         Boolean(element.closest("[role='menu']"));
                });
              const inspectTarget = (target) => {
                const rect = target.getBoundingClientRect();
                const style = getComputedStyle(target);
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                const top = document.elementFromPoint(centerX, centerY);
                return {
                  tag: target.tagName.toLowerCase(), role: target.getAttribute("role"),
                  visible_text: safeText(target.textContent),
                  aria_label: safeText(target.getAttribute("aria-label")),
                  title: safeText(target.getAttribute("title")),
                  disabled: Boolean(target.disabled),
                  aria_disabled: safeText(target.getAttribute("aria-disabled")),
                  tabindex: safeText(target.getAttribute("tabindex")),
                  menu_connected: Boolean(target.closest("[role='menu']")?.isConnected),
                  target_connected: target.isConnected, target_visible: visible(target),
                  pointer_events: style.pointerEvents,
                  center_top_belongs_to_control: Boolean(top && (top === target || target.contains(top))),
                  bounding_box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                };
              };
              const updateTarget = () => {
                if (!state.candidateMenuRequested && !state.targetElement) return [];
                const matches = liveDownloadControls();
                if (matches.length === 1) {
                  const target = matches[0];
                  state.targetElement = target;
                  state.targetMenu = target.closest("[role='menu']");
                  if (!state.downloadControlSeen) {
                    state.downloadControlSeen = true;
                    record("download_control_seen", inspectTarget(target));
                  }
                }
                return matches;
              };
              const sampleMenu = () => {
                const matches = updateTarget();
                const menu = state.targetMenu;
                const menuVisible = Boolean(menu && visible(menu));
                if (menuVisible) state.menuEverOpened = true;
                const signature = `${menuVisible}:${Boolean(menu?.isConnected)}:${matches.length}`;
                if (signature !== state.lastMenuSignature) {
                  state.lastMenuSignature = signature;
                  record("menu_state", {
                    menu_visible: menuVisible, menu_connected: Boolean(menu?.isConnected),
                    live_menu_count: [...document.querySelectorAll("[role='menu']")].filter(visible).length,
                    download_match_count: matches.length,
                  });
                }
              };
              const candidateMoreControl = (eventTarget) => {
                const control = eventTarget?.closest?.("button,[role='button']");
                if (!control || !state.candidateTile.contains(control)) return null;
                return compactLabel(control).includes("moreoptions") ? control : null;
              };
              state.inputListener = (event) => {
                const eventTarget = event.target instanceof Element ? event.target : null;
                if (!eventTarget) return;
                const more = candidateMoreControl(eventTarget);
                updateTarget();
                const target = state.targetElement;
                const isDownload = Boolean(target && (eventTarget === target || target.contains(eventTarget)));
                if (!more && !isDownload) return;
                const menu = isDownload ? state.targetMenu : null;
                const described = safeNode(eventTarget, menu) || {};
                const inputEntry = record("input_event", {
                  event_type: event.type, target_scope: isDownload ? "download" : "candidate_more_options",
                  is_trusted: Boolean(event.isTrusted), tag: described.tag || null,
                  role: described.role || null, target_label: described.text || null,
                  default_prevented: Boolean(event.defaultPrevented),
                  button: event instanceof MouseEvent ? event.button : null,
                  buttons: event instanceof MouseEvent ? event.buttons : null,
                });
                queueMicrotask(() => {
                  if (inputEntry) inputEntry.default_prevented = Boolean(event.defaultPrevented);
                  sampleMenu();
                });
                if (more && event.type === "click") {
                  state.candidateMenuRequested = true;
                  record("candidate_menu_request", {
                    tag: more.tagName.toLowerCase(), role: more.getAttribute("role"),
                    is_trusted: Boolean(event.isTrusted),
                  });
                }
                if (!isDownload || event.type !== "click") return;
                state.activationSeen = true;
                state.menuOpenBeforeActivation = Boolean(state.targetMenu && visible(state.targetMenu));
                state.activeBeforeActivation = safeNode(document.activeElement, state.targetMenu);
                const anchor = eventTarget.closest("a");
                if (anchor && (anchor === target || target.contains(anchor))) {
                  record("anchor_activation", {
                    url: safeUrl(anchor.href), download_attribute: safeHeader(anchor.getAttribute("download")),
                    tag: "a", role: anchor.getAttribute("role"), is_trusted: Boolean(event.isTrusted),
                  });
                } else {
                  record("download_control_activation", {
                    tag: target.tagName.toLowerCase(), role: target.getAttribute("role"),
                    is_trusted: Boolean(event.isTrusted),
                  });
                }
              };
              state.openWrapper = function(...args) {
                record("window_open", {url: safeUrl(args[0])});
                return state.originalOpen.apply(this, args);
              };
              state.createObjectURLWrapper = function(object) {
                const result = state.originalCreateObjectURL.call(this, object);
                record("blob_created", {
                  url: safeUrl(result), object_type: object && typeof object.type === "string" ? safeHeader(object.type) : null,
                  object_size: object && Number.isFinite(object.size) ? object.size : null,
                });
                return result;
              };
              state.anchorClickWrapper = function(...args) {
                record("anchor_programmatic_click", {
                  url: safeUrl(this.href), download_attribute: safeHeader(this.getAttribute("download")), tag: "a",
                });
                return state.originalAnchorClick.apply(this, args);
              };
              state.fetchWrapper = function(...args) {
                const input = args[0]; const init = args[1] || {};
                const rawUrl = typeof input === "string" || input instanceof URL ? input : input && input.url;
                const method = String(init.method || (input && input.method) || "GET").toUpperCase().slice(0, 16);
                const safe = safeUrl(rawUrl);
                if (safe) record("fetch_call", {url: safe, method, resource_type: "fetch"});
                let result;
                try { result = state.originalFetch.apply(this, args); }
                catch (error) { if (safe) record("fetch_failed", {url: safe, method, resource_type: "fetch"}); throw error; }
                result.then((response) => {
                  const responseUrl = safeUrl(response.url || rawUrl);
                  if (responseUrl) record("fetch_response", {
                    url: responseUrl, method, resource_type: "fetch", status: response.status,
                    content_type: safeHeader(response.headers.get("content-type")),
                    content_disposition: safeHeader(response.headers.get("content-disposition")),
                  });
                }, () => { if (safe) record("fetch_failed", {url: safe, method, resource_type: "fetch"}); });
                return result;
              };
              state.observer = new MutationObserver((mutations) => {
                sampleMenu();
                for (const mutation of mutations) for (const node of mutation.addedNodes) {
                  if (!(node instanceof Element)) continue;
                  for (const element of [node, ...node.querySelectorAll("a,[role='alert'],[role='status']")]) {
                    if (element instanceof HTMLAnchorElement &&
                        (element.hasAttribute("download") || String(element.href).startsWith("blob:"))) {
                      record("anchor_added", {url: safeUrl(element.href),
                        download_attribute: safeHeader(element.getAttribute("download")), tag: "a"});
                    }
                    const role = element.getAttribute("role");
                    if (role === "alert" || role === "status") {
                      const match = (element.textContent || "").match(/download|saved|failed|error/i);
                      if (match) record("dom_status", {role, matched_keyword: match[0].toLowerCase()});
                    }
                  }
                }
              });
              window.open = state.openWrapper;
              URL.createObjectURL = state.createObjectURLWrapper;
              HTMLAnchorElement.prototype.click = state.anchorClickWrapper;
              window.fetch = state.fetchWrapper;
              for (const eventType of state.inputEventTypes) document.addEventListener(eventType, state.inputListener, true);
              state.observer.observe(document.documentElement, {childList: true, subtree: true});
              sampleMenu();
            }
            """,
            candidate_id,
        )

    @staticmethod
    def _remove_download_trace_hooks(page) -> dict[str, Any]:
        raw_result = page.evaluate(
            r"""
            () => {
              const keys = [
                "__nanoBatchManualDownloadTraceV1",
                "__nanoBatchDownloadTriggerTraceV2",
                "__nanoBatchDownloadTriggerTraceV1",
              ];
              const events = [];
              const final = {};
              const foundKeys = [];
              let cleanupErrorCount = 0;
              const attempt = (operation) => {
                try { operation(); } catch { cleanupErrorCount += 1; }
              };
              for (const key of keys) {
                const state = window[key];
                if (!state) continue;
                const manual = key.includes("ManualDownloadTrace");
                foundKeys.push(manual ? "manual_v1" : (key.endsWith("V2") ? "v2" : "v1"));
                if (manual) {
                  final.menu_ever_opened = Boolean(state.menuEverOpened);
                  final.download_control_seen = Boolean(state.downloadControlSeen);
                  final.download_activation_observed = Boolean(state.activationSeen);
                  final.menu_open_before_activation = state.menuOpenBeforeActivation;
                  final.active_element_before = state.activeBeforeActivation || null;
                }
                if ((key.endsWith("V2") || manual) && state.targetElement instanceof Element) {
                  attempt(() => {
                    const targetRect = state.targetElement.getBoundingClientRect();
                    const targetStyle = getComputedStyle(state.targetElement);
                    const active = document.activeElement;
                    final.menu_connected = Boolean(state.targetMenu && state.targetMenu.isConnected);
                    final.menu_visible = Boolean(
                      state.targetMenu && state.targetMenu.isConnected &&
                      state.targetMenu.getBoundingClientRect().width > 0 &&
                      state.targetMenu.getBoundingClientRect().height > 0
                    );
                    final.target_connected = state.targetElement.isConnected;
                    final.target_visible = Boolean(
                      state.targetElement.isConnected && targetRect.width > 0 && targetRect.height > 0 &&
                      targetStyle.visibility !== "hidden" && targetStyle.display !== "none"
                    );
                    final.active_element = active instanceof Element ? {
                      tag: active.tagName.toLowerCase(),
                      role: active.getAttribute("role") || active.closest("[role]")?.getAttribute("role") || null,
                      text: (active.getAttribute("aria-label") || active.textContent || "")
                        .replace(/\bGoogle Account\b/gi, "[redacted account control]")
                        .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
                        .replace(/\s+/g, " ").trim().slice(0, 120) || null,
                      connected: active.isConnected,
                      inside_menu: Boolean(state.targetMenu && state.targetMenu.contains(active)),
                    } : null;
                  });
                } else if (manual) {
                  attempt(() => {
                    const active = document.activeElement;
                    final.menu_connected = false;
                    final.menu_visible = false;
                    final.target_connected = false;
                    final.target_visible = false;
                    final.active_element = active instanceof Element ? {
                      tag: active.tagName.toLowerCase(),
                      role: active.getAttribute("role") || active.closest("[role]")?.getAttribute("role") || null,
                      text: (active.getAttribute("aria-label") || active.textContent || "")
                        .replace(/\bGoogle Account\b/gi, "[redacted account control]")
                        .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
                        .replace(/\s+/g, " ").trim().slice(0, 120) || null,
                      connected: active.isConnected,
                      inside_menu: false,
                    } : null;
                  });
                }
                if (Array.isArray(state.events)) {
                  events.push(...state.events.slice(0, 250 - events.length));
                }
                attempt(() => state.observer?.disconnect());
                if (Array.isArray(state.inputEventTypes) && state.inputListener) {
                  for (const eventType of state.inputEventTypes) {
                    attempt(() => document.removeEventListener(eventType, state.inputListener, true));
                  }
                }
                if (state.clickListener) {
                  attempt(() => document.removeEventListener("click", state.clickListener, true));
                }
                attempt(() => {
                  if (window.open === state.openWrapper) window.open = state.originalOpen;
                });
                attempt(() => {
                  if (URL.createObjectURL === state.createObjectURLWrapper) {
                    URL.createObjectURL = state.originalCreateObjectURL;
                  }
                });
                attempt(() => {
                  if (HTMLAnchorElement.prototype.click === state.anchorClickWrapper) {
                    HTMLAnchorElement.prototype.click = state.originalAnchorClick;
                  }
                });
                attempt(() => {
                  if (window.fetch === state.fetchWrapper) window.fetch = state.originalFetch;
                });
                attempt(() => { delete window[key]; });
              }
              attempt(() => { delete window.__nanoBatchCandidateMenuNodeV1; });
              return {
                events: events.slice(0, 250),
                final,
                found_keys: foundKeys,
                cleanup_error_count: cleanupErrorCount,
              };
            }
            """
        )
        if isinstance(raw_result, list):
            raw_events = raw_result
            raw_final = {}
        elif isinstance(raw_result, dict):
            raw_events = raw_result.get("events") or []
            raw_final = raw_result.get("final") or {}
        else:
            raw_events = []
            raw_final = {}
        normalized = []
        for raw in raw_events or []:
            event = _normalize_trace_hook_event(raw)
            if event is not None:
                normalized.append(event)
        return {
            "events": normalized,
            "found_keys": [
                value
                for value in (raw_result.get("found_keys") or [])
                if value in {"v1", "v2", "manual_v1"}
            ]
            if isinstance(raw_result, dict)
            else [],
            "restored": not bool(
                raw_result.get("cleanup_error_count")
                if isinstance(raw_result, dict)
                else 0
            ),
            "final": {
                "menu_connected": bool(raw_final.get("menu_connected")),
                "menu_visible": bool(raw_final.get("menu_visible")),
                "target_connected": bool(raw_final.get("target_connected")),
                "target_visible": bool(raw_final.get("target_visible")),
                "active_element": _normalize_trace_node(raw_final.get("active_element")),
                "menu_ever_opened": bool(raw_final.get("menu_ever_opened")),
                "download_control_seen": bool(raw_final.get("download_control_seen")),
                "download_activation_observed": bool(
                    raw_final.get("download_activation_observed")
                ),
                "menu_open_before_activation": (
                    bool(raw_final.get("menu_open_before_activation"))
                    if raw_final.get("menu_open_before_activation") is not None
                    else None
                ),
                "active_element_before": _normalize_trace_node(
                    raw_final.get("active_element_before")
                ),
            },
        }

    @staticmethod
    def _trace_staging_files(staging_dir: Path) -> list[dict[str, Any]]:
        return [
            {
                "name": _clean_text(_safe_label(path.name), 100),
                "byte_size": path.stat().st_size,
                "partial": path.name.lower().endswith(".crdownload"),
            }
            for path in sorted(staging_dir.iterdir(), key=lambda value: value.name)
            if path.is_file()
        ]

    @staticmethod
    def _download_ui_probe_page_state(page) -> dict[str, Any]:
        raw = page.evaluate(
            "() => ({hasFocus: document.hasFocus(), visibilityState: document.visibilityState})"
        )
        return {
            "document_has_focus": bool((raw or {}).get("hasFocus")),
            "document_visibility_state": (
                _clean_text(str((raw or {}).get("visibilityState") or ""), 20)
                or None
            ),
        }

    @staticmethod
    def _normalize_download_parent_probe_trace(raw: Any) -> dict[str, Any]:
        """Whitelist the bounded passive Download-parent probe trace."""
        if not isinstance(raw, dict):
            return {"installed": False, "cleaned_up": False, "trace_error": "invalid_result"}

        def text(value: Any, limit: int = 80) -> str | None:
            return _clean_text(str(value or ""), limit) or None

        def offset(value: Any) -> float | None:
            if not isinstance(value, (int, float)):
                return None
            return round(min(60_000.0, max(0.0, float(value))), 3)

        def point(value: Any) -> dict[str, float] | None:
            if not isinstance(value, dict):
                return None
            x = value.get("x")
            y = value.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return None
            return {"x": round(float(x), 3), "y": round(float(y), 3)}

        def aria_expanded(value: Any) -> str | None:
            normalized = str(value or "").lower()
            return normalized if normalized in {"true", "false"} else None

        def aria_controls(value: Any) -> str | None:
            normalized = str(value or "")
            return (
                normalized
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", normalized)
                else None
            )

        def node(value: Any) -> dict[str, Any] | None:
            if not isinstance(value, dict):
                return None
            node_id = str(value.get("node_id") or "")
            result = {
                "node_id": (
                    node_id if re.fullmatch(r"download-node-[1-9][0-9]{0,3}", node_id) else None
                ),
                "tag": (text(value.get("tag"), 40) or "").lower() or None,
                "role": (text(value.get("role"), 40) or "").lower() or None,
                "aria_haspopup": (text(value.get("aria_haspopup"), 40) or "").lower()
                or None,
                "aria_expanded": aria_expanded(value.get("aria_expanded")),
                "aria_controls": aria_controls(value.get("aria_controls")),
                "connected": bool(value.get("connected")),
                "hover": bool(value.get("hover")),
                "bounding_box": _normalize_submenu_box(value.get("bounding_box")),
                "class_summary": [
                    item
                    for raw_item in (value.get("class_summary") or [])[:4]
                    if (
                        (item := text(raw_item, 60)) is not None
                        and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", item)
                    )
                ],
            }
            return result

        allowed_events = {
            "pointerenter",
            "pointerover",
            "mouseenter",
            "mouseover",
            "pointerdown",
            "mousedown",
            "pointerup",
            "mouseup",
            "click",
        }
        events = []
        for value in (raw.get("events") or [])[:96]:
            if not isinstance(value, dict) or value.get("event_type") not in allowed_events:
                continue
            normalized = {
                "event_type": value["event_type"],
                "offset_ms": offset(value.get("offset_ms")),
                "is_trusted": bool(value.get("is_trusted")),
                "target": node(value.get("target")),
                "current_target": node(value.get("current_target")),
                "pointer": point(value.get("pointer")),
                "hover": bool(value.get("hover")),
            }
            if isinstance(value.get("button"), (int, float)):
                normalized["button"] = int(value["button"])
            if isinstance(value.get("buttons"), (int, float)):
                normalized["buttons"] = int(value["buttons"])
            events.append(normalized)

        allowed_stages = {
            "installed",
            "before_hover_move",
            "after_hover_move",
            "submenu_poll",
            "before_activation_move",
            "after_activation_move",
            "before_down",
            "after_down",
            "before_up",
            "after_up",
            "final",
        }
        checkpoints = []
        for value in (raw.get("checkpoints") or [])[:64]:
            if not isinstance(value, dict) or value.get("stage") not in allowed_stages:
                continue
            viewport = value.get("viewport") or {}
            checkpoints.append(
                {
                    "stage": value["stage"],
                    "offset_ms": offset(value.get("offset_ms")),
                    "last_offset_ms": offset(value.get("last_offset_ms")),
                    "repeat_count": max(1, min(100, int(value.get("repeat_count") or 1))),
                    "pointer": point(value.get("pointer")),
                    "viewport": {
                        "width": max(0, int(viewport.get("width") or 0)),
                        "height": max(0, int(viewport.get("height") or 0)),
                    },
                    "parent": node(value.get("parent")),
                    "original_connected": bool(value.get("original_connected")),
                    "reacquired_parent_count": max(
                        0, min(16, int(value.get("reacquired_parent_count") or 0))
                    ),
                    "reacquired_same_node": (
                        bool(value.get("reacquired_same_node"))
                        if value.get("reacquired_same_node") is not None
                        else None
                    ),
                    "node_replaced": bool(value.get("node_replaced")),
                    "hit_target": node(value.get("hit_target")),
                    "hit_owned_by_parent": bool(value.get("hit_owned_by_parent")),
                }
            )

        aria_timeline = []
        for value in (raw.get("aria_timeline") or [])[:64]:
            if not isinstance(value, dict) or value.get("attribute") not in {
                "aria-expanded",
                "aria-controls",
            }:
                continue
            attribute = value["attribute"]
            normalize_value = aria_expanded if attribute == "aria-expanded" else aria_controls
            aria_timeline.append(
                {
                    "offset_ms": offset(value.get("offset_ms")),
                    "node_id": node({"node_id": value.get("node_id")}).get("node_id"),
                    "attribute": attribute,
                    "old_value": normalize_value(value.get("old_value")),
                    "new_value": normalize_value(value.get("new_value")),
                    "reason": (
                        value.get("reason")
                        if value.get("reason") in {"initial", "mutation"}
                        else None
                    ),
                }
            )

        lifecycle = []
        for value in (raw.get("lifecycle") or [])[:32]:
            if not isinstance(value, dict):
                continue
            current_ids = [
                item
                for raw_item in (value.get("current_node_ids") or [])[:16]
                if re.fullmatch(
                    r"download-node-[1-9][0-9]{0,3}",
                    (item := str(raw_item or "")),
                )
            ]
            lifecycle.append(
                {
                    "offset_ms": offset(value.get("offset_ms")),
                    "reason": text(value.get("reason"), 40),
                    "original_connected": bool(value.get("original_connected")),
                    "current_node_ids": current_ids,
                    "same_node": bool(value.get("same_node")),
                }
            )

        expanded_sequence: list[str | None] = []
        for entry in aria_timeline:
            if entry["attribute"] != "aria-expanded":
                continue
            for value in (entry["old_value"], entry["new_value"]):
                if value is not None and (not expanded_sequence or expanded_sequence[-1] != value):
                    expanded_sequence.append(value)
        replacement_observed = any(checkpoint["node_replaced"] for checkpoint in checkpoints) or any(
            not entry["same_node"] and bool(entry["current_node_ids"])
            for entry in lifecycle
        )
        return {
            "installed": bool(raw.get("installed")),
            "cleaned_up": bool(raw.get("cleaned_up")),
            "cleanup_error_count": max(
                0, min(32, int(raw.get("cleanup_error_count") or 0))
            ),
            "initial_node_id": (
                raw.get("initial_node_id")
                if re.fullmatch(
                    r"download-node-[1-9][0-9]{0,3}",
                    str(raw.get("initial_node_id") or ""),
                )
                else None
            ),
            "events": events,
            "aria_timeline": aria_timeline,
            "checkpoints": checkpoints,
            "lifecycle": lifecycle,
            "summary": {
                "event_types": sorted({event["event_type"] for event in events}),
                "trusted_event_count": sum(event["is_trusted"] for event in events),
                "owned_hit_observed": any(
                    checkpoint["hit_owned_by_parent"] for checkpoint in checkpoints
                ),
                "unowned_hit_observed": any(
                    checkpoint["hit_target"] is not None
                    and not checkpoint["hit_owned_by_parent"]
                    for checkpoint in checkpoints
                ),
                "node_replacement_observed": replacement_observed,
                "aria_expanded_sequence": expanded_sequence,
                "aria_expanded_never_changed": len(expanded_sequence) <= 1,
            },
        }

    @staticmethod
    def _install_download_parent_probe_trace(parent) -> None:
        """Install bounded passive listeners around one exact Download parent."""
        parent.evaluate(
            r"""
            (initialNode) => {
              const KEY = "__nanoBatchDownloadParentProbeTraceV1";
              if (window[KEY]) throw new Error("Download-parent probe trace already installed");
              const startedAt = performance.now();
              const eventTypes = [
                "pointerenter", "pointerover", "mouseenter", "mouseover",
                "pointerdown", "mousedown", "pointerup", "mouseup", "click"
              ];
              const safeToken = (value, limit = 80) => {
                const text = String(value || "").slice(0, limit);
                return /^[A-Za-z0-9_.:-]+$/.test(text) ? text : null;
              };
              const compact = (node) => String(
                node?.getAttribute?.("aria-label") || node?.textContent || ""
              ).toLowerCase().replace(/[^a-z]+/g, "");
              const visible = (node) => {
                if (!(node instanceof Element) || !node.isConnected) return false;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== "hidden" && style.display !== "none";
              };
              const isDownloadParent = (node) => {
                if (!(node instanceof Element)) return false;
                if (node.getAttribute("role") !== "menuitem" ||
                    node.getAttribute("aria-haspopup") !== "menu") return false;
                const label = compact(node);
                return label === "download" || label === "downloaddownload" ||
                  /^downloaddownload(?:chevronright|arrowright|arrowdropright|keyboardarrowright|navigatenext)$/.test(label);
              };
              const state = {
                startedAt, initialNode, nextNodeId: 1, nodeIds: new WeakMap(),
                attached: new Set(), listeners: [], events: [], ariaTimeline: [],
                checkpoints: [], lifecycle: [], lastLifecycleSignature: null,
                observer: null,
              };
              window[KEY] = state;
              const elapsed = () => Math.max(0, performance.now() - startedAt);
              const nodeId = (node) => {
                if (!(node instanceof Element)) return null;
                if (!state.nodeIds.has(node)) {
                  state.nodeIds.set(node, `download-node-${state.nextNodeId++}`);
                }
                return state.nodeIds.get(node);
              };
              const box = (node) => {
                if (!(node instanceof Element)) return null;
                const rect = node.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
              };
              const nodeFacts = (node) => node instanceof Element ? {
                node_id: nodeId(node), tag: node.tagName.toLowerCase(),
                role: safeToken(node.getAttribute("role"), 40),
                aria_haspopup: safeToken(node.getAttribute("aria-haspopup"), 40),
                aria_expanded: ["true", "false"].includes(node.getAttribute("aria-expanded"))
                  ? node.getAttribute("aria-expanded") : null,
                aria_controls: safeToken(node.getAttribute("aria-controls"), 128),
                connected: node.isConnected, hover: node.matches(":hover"),
                bounding_box: box(node),
                class_summary: [...node.classList]
                  .filter((value) => /^[A-Za-z0-9_-]{1,60}$/.test(value)).slice(0, 4),
              } : null;
              const currentParents = () => [...document.querySelectorAll('[role="menuitem"][aria-haspopup="menu"]')]
                .filter((node) => isDownloadParent(node) && visible(node));
              const recordAria = (node, attribute, oldValue, newValue, reason) => {
                if (state.ariaTimeline.length >= 64) return;
                const normalize = attribute === "aria-expanded"
                  ? (value) => ["true", "false"].includes(value) ? value : null
                  : (value) => safeToken(value, 128);
                state.ariaTimeline.push({
                  offset_ms: elapsed(), node_id: nodeId(node), attribute,
                  old_value: normalize(oldValue), new_value: normalize(newValue), reason,
                });
              };
              const attach = (node) => {
                if (!(node instanceof Element) || state.attached.has(node)) return;
                state.attached.add(node);
                recordAria(node, "aria-expanded", null, node.getAttribute("aria-expanded"), "initial");
                recordAria(node, "aria-controls", null, node.getAttribute("aria-controls"), "initial");
                for (const eventType of eventTypes) {
                  const listener = (event) => {
                    if (state.events.length >= 96) return;
                    const pointer = event instanceof MouseEvent
                      ? {x: event.clientX, y: event.clientY} : null;
                    state.events.push({
                      event_type: event.type, offset_ms: elapsed(),
                      is_trusted: Boolean(event.isTrusted),
                      target: nodeFacts(event.target), current_target: nodeFacts(event.currentTarget),
                      pointer, button: event instanceof MouseEvent ? event.button : null,
                      buttons: event instanceof MouseEvent ? event.buttons : null,
                      hover: node.matches(":hover"),
                    });
                  };
                  node.addEventListener(eventType, listener, true);
                  state.listeners.push({node, eventType, listener});
                }
              };
              const recordLifecycle = (reason) => {
                const current = currentParents();
                current.forEach(attach);
                const currentIds = current.map(nodeId);
                const signature = `${initialNode.isConnected}|${currentIds.join(",")}`;
                if (signature === state.lastLifecycleSignature || state.lifecycle.length >= 32) return;
                state.lastLifecycleSignature = signature;
                state.lifecycle.push({
                  offset_ms: elapsed(), reason, original_connected: initialNode.isConnected,
                  current_node_ids: currentIds,
                  same_node: current.length === 1 && current[0] === initialNode,
                });
              };
              state.checkpoint = (node, stage, pointer) => {
                recordLifecycle(stage);
                const current = currentParents();
                current.forEach(attach);
                const selectedPoint = pointer && Number.isFinite(pointer.x) && Number.isFinite(pointer.y)
                  ? {x: pointer.x, y: pointer.y}
                  : (() => {
                      const rect = node.getBoundingClientRect();
                      return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
                    })();
                const hit = document.elementFromPoint(selectedPoint.x, selectedPoint.y);
                const entry = {
                  stage, offset_ms: elapsed(), repeat_count: 1,
                  pointer: selectedPoint, viewport: {width: innerWidth, height: innerHeight},
                  parent: nodeFacts(node), original_connected: initialNode.isConnected,
                  reacquired_parent_count: current.length,
                  reacquired_same_node: current.length === 1 ? current[0] === initialNode : null,
                  node_replaced: !initialNode.isConnected || current.some((item) => item !== initialNode),
                  hit_target: nodeFacts(hit),
                  hit_owned_by_parent: Boolean(hit && (hit === node || node.contains(hit))),
                };
                const prior = state.checkpoints[state.checkpoints.length - 1];
                const signature = JSON.stringify({
                  stage: entry.stage, pointer: entry.pointer, parent: entry.parent,
                  original_connected: entry.original_connected,
                  reacquired_parent_count: entry.reacquired_parent_count,
                  reacquired_same_node: entry.reacquired_same_node,
                  node_replaced: entry.node_replaced, hit_target: entry.hit_target,
                  hit_owned_by_parent: entry.hit_owned_by_parent,
                });
                if (stage === "submenu_poll" && prior && prior._signature === signature) {
                  prior.repeat_count += 1;
                  prior.last_offset_ms = entry.offset_ms;
                  return;
                }
                if (state.checkpoints.length < 64) {
                  entry._signature = signature;
                  state.checkpoints.push(entry);
                }
              };
              attach(initialNode);
              recordLifecycle("installed");
              state.checkpoint(initialNode, "installed", null);
              state.observer = new MutationObserver((mutations) => {
                const attributes = mutations.filter((mutation) =>
                  mutation.type === "attributes" &&
                  ["aria-expanded", "aria-controls"].includes(mutation.attributeName) &&
                  mutation.target instanceof Element &&
                  (state.attached.has(mutation.target) || isDownloadParent(mutation.target))
                );
                attributes.forEach((mutation, index) => {
                  attach(mutation.target);
                  const next = attributes.slice(index + 1).find((candidate) =>
                    candidate.target === mutation.target &&
                    candidate.attributeName === mutation.attributeName
                  );
                  const newValue = next
                    ? next.oldValue
                    : mutation.target.getAttribute(mutation.attributeName);
                  recordAria(
                    mutation.target, mutation.attributeName,
                    mutation.oldValue, newValue, "mutation"
                  );
                });
                recordLifecycle("mutation");
              });
              state.observer.observe(document.documentElement, {
                subtree: true, childList: true, attributes: true,
                attributeOldValue: true, attributeFilter: ["aria-expanded", "aria-controls"],
              });
            }
            """
        )

    @staticmethod
    def _download_parent_probe_trace_checkpoint(
        control,
        stage: str,
        pointer: dict[str, float] | None = None,
    ) -> None:
        control.evaluate(
            r"""
            (node, args) => {
              const state = window.__nanoBatchDownloadParentProbeTraceV1;
              if (!state || typeof state.checkpoint !== "function") {
                throw new Error("Download-parent probe trace is unavailable");
              }
              state.checkpoint(node, args.stage, args.pointer || null);
            }
            """,
            {"stage": stage, "pointer": pointer},
            timeout=1_000,
        )

    def _maybe_checkpoint_download_parent_probe(
        self,
        control,
        diagnostics: dict[str, Any] | None,
        stage: str,
        pointer: dict[str, float] | None = None,
    ) -> None:
        """Add one passive probe checkpoint without changing production input."""
        if not diagnostics or not diagnostics.get("_passive_parent_trace_enabled"):
            return
        try:
            self._download_parent_probe_trace_checkpoint(control, stage, pointer)
        except Exception as exc:
            diagnostics["passive_parent_trace_checkpoint_error_count"] = int(
                diagnostics.get("passive_parent_trace_checkpoint_error_count") or 0
            ) + 1
            diagnostics["passive_parent_trace_last_error"] = _safe_trace_header(str(exc))

    @classmethod
    def _remove_download_parent_probe_trace(cls, page) -> dict[str, Any]:
        raw = page.evaluate(
            r"""
            () => {
              const KEY = "__nanoBatchDownloadParentProbeTraceV1";
              const state = window[KEY];
              if (!state) {
                return {installed: false, cleaned_up: true, cleanup_error_count: 0};
              }
              let cleanupErrorCount = 0;
              const attempt = (action) => {
                try { action(); } catch { cleanupErrorCount += 1; }
              };
              attempt(() => {
                const current = [...document.querySelectorAll('[role="menuitem"][aria-haspopup="menu"]')]
                  .filter((node) => {
                    const label = String(node.getAttribute("aria-label") || node.textContent || "")
                      .toLowerCase().replace(/[^a-z]+/g, "");
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    const semantic = label === "download" || label === "downloaddownload" ||
                      /^downloaddownload(?:chevronright|arrowright|arrowdropright|keyboardarrowright|navigatenext)$/.test(label);
                    return semantic && node.isConnected && rect.width > 0 && rect.height > 0 &&
                      style.visibility !== "hidden" && style.display !== "none";
                  });
                const node = current.length === 1 ? current[0] : state.initialNode;
                state.checkpoint(node, "final", null);
              });
              attempt(() => state.observer?.disconnect());
              for (const item of state.listeners || []) {
                attempt(() => item.node.removeEventListener(item.eventType, item.listener, true));
              }
              const result = {
                installed: true, cleaned_up: cleanupErrorCount === 0,
                cleanup_error_count: cleanupErrorCount,
                initial_node_id: state.nodeIds.get(state.initialNode) || null,
                events: (state.events || []).slice(0, 96),
                aria_timeline: (state.ariaTimeline || []).slice(0, 64),
                checkpoints: (state.checkpoints || []).slice(0, 64).map((entry) => {
                  const copy = {...entry}; delete copy._signature; return copy;
                }),
                lifecycle: (state.lifecycle || []).slice(0, 32),
              };
              attempt(() => { delete window[KEY]; });
              result.cleaned_up = cleanupErrorCount === 0;
              result.cleanup_error_count = cleanupErrorCount;
              return result;
            }
            """
        )
        return cls._normalize_download_parent_probe_trace(raw)

    def _download_ui_probe_visible_menu_counts(self, page) -> dict[str, Any]:
        """Count only visible candidate and Download-submenu panels."""
        candidate_menu_count = 0
        submenu_count = 0
        probe_error_count = 0
        for menu in page.get_by_role("menu").all():
            try:
                menu_facts = self._live_control_facts(menu)
            except Exception:
                probe_error_count += 1
                continue
            if not (menu_facts["connected"] and menu_facts["visible"]):
                continue

            def has_live_item(name: re.Pattern[str]) -> bool:
                nonlocal probe_error_count
                try:
                    items = menu.get_by_role("menuitem", name=name).all()
                except Exception:
                    probe_error_count += 1
                    return False
                for item in items:
                    try:
                        facts = self._live_control_facts(item)
                    except Exception:
                        probe_error_count += 1
                        continue
                    if facts["connected"] and facts["visible"]:
                        return True
                return False

            candidate_menu_count += int(
                has_live_item(re.compile(r"^(?:download\s+)?download$", re.I))
            )
            submenu_count += int(
                has_live_item(
                    re.compile(r"^\s*1K\s+Original\s+size\s*$", re.I)
                )
            )
        return {
            "remaining_visible_menu_count": candidate_menu_count,
            "remaining_visible_submenu_count": submenu_count,
            "cleanup_probe_error_count": probe_error_count,
        }

    def _dismiss_download_ui_probe_menus(
        self,
        page,
        *,
        timeout_ms: int = 1_000,
    ) -> dict[str, Any]:
        """Dismiss two diagnostic menu levels and positively verify neutral state."""
        if not 250 <= timeout_ms <= 3_000:
            raise ValueError("Diagnostic menu cleanup wait must be 250 to 3000 ms.")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)

        deadline = time.monotonic() + timeout_ms / 1_000
        poll_count = 0
        last = {
            "remaining_visible_menu_count": 0,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
        }
        while True:
            poll_count += 1
            last = self._download_ui_probe_visible_menu_counts(page)
            neutral = (
                last["remaining_visible_menu_count"] == 0
                and last["remaining_visible_submenu_count"] == 0
                and last["cleanup_probe_error_count"] == 0
            )
            if neutral or time.monotonic() >= deadline:
                return {
                    "cleanup_neutral_state": neutral,
                    **last,
                    "cleanup_poll_count": poll_count,
                }
            page.wait_for_timeout(100)

    @staticmethod
    def _copy_download_ui_probe_submenu_diagnostics(
        sample: dict[str, Any],
        menu_diagnostics: dict[str, Any],
        submenu_diagnostics: dict[str, Any],
    ) -> None:
        """Copy safe counters even when submenu exposure raised an exception."""
        parent_reacquisitions = int(
            submenu_diagnostics.get("parent_settle_poll_count") or 0
        ) + int(submenu_diagnostics.get("poll_count") or 0)
        sample["download_parent_reacquisitions"] = parent_reacquisitions
        sample["submenu_polls"] = max(
            int(sample.get("submenu_polls") or 0),
            int(submenu_diagnostics.get("poll_count") or 0),
        )
        sample["parent_activation_count"] = int(
            submenu_diagnostics.get("parent_activation_count") or 0
        )
        for key in (
            "activation_dispatch_began",
            "activation_mouse_move_completed",
            "activation_mouse_down_completed",
            "activation_mouse_up_completed",
            "activation_dispatch_returned_normally",
            "pre_activation_poll_phase_entered",
            "post_activation_poll_phase_entered",
            "aria_expanded_ever_true",
            "aria_controls_ever_non_null",
            "controlled_submenu_ever_appeared",
            "controlled_panel_seen_temporarily",
            "hover_mouse_move_completed",
            "transition_or_remount_observed",
            "hover_controlled_handoff_attempted",
            "hover_controlled_handoff_mouse_move_completed",
            "hover_controlled_handoff_succeeded",
            "hover_controlled_handoff_lost",
            "strict_production_visible_before_handoff",
            "handoff_enterable",
            "handoff_opacity_only_strict_visibility_difference",
            "handoff_hit_owned",
        ):
            sample[key] = bool(submenu_diagnostics.get(key))
        sample["pre_activation_poll_count"] = int(
            submenu_diagnostics.get("pre_activation_poll_count") or 0
        )
        sample["post_activation_poll_count"] = int(
            submenu_diagnostics.get("post_activation_poll_count") or 0
        )
        sample["aria_controls"] = submenu_diagnostics.get("aria_controls")
        sample["aria_controls_changed"] = bool(
            submenu_diagnostics.get("aria_controls_changed")
        )
        sample["download_parent_remounted"] = bool(
            sample.get("download_parent_remounted")
            or menu_diagnostics.get("download_node_identity_changed_after_open")
        )
        sample["parent_reacquired_or_remounted"] = bool(
            parent_reacquisitions or sample["download_parent_remounted"]
        )
        parent = submenu_diagnostics.get("final_parent")
        sample["final_parent"] = {
            key: parent.get(key)
            for key in (
                "connected",
                "visible",
                "enabled",
                "pointer_events",
                "aria_expanded",
                "aria_controls",
            )
        } if isinstance(parent, dict) else None
        sample["multiple_menu_panels_overlapped"] = bool(
            submenu_diagnostics.get("controlled_panel_overlap_observed")
        )
        snapshot = submenu_diagnostics.get("after_hover_handoff")
        sample["after_hover_handoff"] = snapshot if isinstance(snapshot, dict) else None
        for key in (
            "hover_controlled_handoff_pre_move_revalidation",
            "hover_controlled_handoff_post_move_revalidation",
        ):
            value = submenu_diagnostics.get(key)
            sample[key] = value if isinstance(value, dict) else None
        before_handoff = submenu_diagnostics.get("before_hover_handoff")
        if (
            isinstance(before_handoff, dict)
            and before_handoff.get("visibility_timeline")
            and before_handoff.get("visibility_timeline_stop_reason") is None
        ):
            before_handoff["visibility_timeline_stop_reason"] = "exposure_loop_ended"
        sample["before_hover_handoff"] = (
            before_handoff if isinstance(before_handoff, dict) else None
        )

    def probe_download_ui(
        self,
        *,
        candidate: CandidateRecord,
        label: str,
        samples: int = 3,
    ) -> dict[str, Any]:
        """Measure candidate-to-1K UI readiness without activating the final item."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", label):
            raise ValueError("Probe label must be 1-40 safe ASCII characters.")
        if not 1 <= samples <= 10:
            raise ValueError("Download UI probe samples must be between 1 and 10.")

        candidate_id = self._candidate_media_id(candidate)
        report: dict[str, Any] = {
            "schema": "nano-banana-download-ui-probe/v2",
            "label": label,
            "candidate_id": candidate_id,
            "requested_samples": samples,
            "samples": [],
            "ownership_verified_before_ui_action": False,
            "focus_emulation_active": False,
            "final_document_has_focus": None,
            "final_document_visibility_state": None,
            "safety": {
                "start_generation_activated": False,
                "one_k_activated": False,
                "native_download_tracking_configured": False,
                "canonical_asset_committed": False,
                "bring_to_front_called": False,
            },
        }
        page = None
        try:
            self.open()
            page = self._require_page()
            self._assert_download_context()
            report["focus_emulation_active"] = self._page_cdp_session is not None
            verified_id, _verified_image, _verified_tile = self._exact_candidate_tile(
                candidate
            )
            if verified_id != candidate_id:
                raise FlowAutomationError(
                    "Probe candidate identity changed before diagnostic UI input."
                )
            report["ownership_verified_before_ui_action"] = True

            for sample_number in range(1, samples + 1):
                sample: dict[str, Any] = {
                    "condition": label,
                    "sample": sample_number,
                    "status": "running",
                    "stage": "cleanup_before_sample",
                    "candidate_tile_ms": None,
                    "toolbar_ms": None,
                    "more_options_ms": None,
                    "download_parent_ms": None,
                    "submenu_ms": None,
                    "one_k_ready_ms": None,
                    "total_ms": None,
                    "reveal_attempts": 0,
                    "download_parent_reacquisitions": 0,
                    "submenu_polls": 0,
                    "parent_activation_count": 0,
                    "activation_dispatch_began": False,
                    "activation_mouse_move_completed": False,
                    "activation_mouse_down_completed": False,
                    "activation_mouse_up_completed": False,
                    "activation_dispatch_returned_normally": False,
                    "pre_activation_poll_phase_entered": False,
                    "pre_activation_poll_count": 0,
                    "post_activation_poll_phase_entered": False,
                    "post_activation_poll_count": 0,
                    "aria_expanded_ever_true": False,
                    "aria_controls_ever_non_null": False,
                    "controlled_submenu_ever_appeared": False,
                    "controlled_panel_seen_temporarily": False,
                    "final_parent": None,
                    "candidate_tile_remounted": False,
                    "more_options_remounted": False,
                    "download_parent_remounted": False,
                    "aria_controls_changed": False,
                    "multiple_menu_panels_overlapped": False,
                    "aria_controls": None,
                    "document_has_focus": None,
                    "document_visibility_state": None,
                    "cleanup_before_sample": None,
                    "cleanup_neutral_state": None,
                    "remaining_visible_menu_count": None,
                    "remaining_visible_submenu_count": None,
                    "cleanup_poll_count": 0,
                    "failure_classification": None,
                    "final_safe_failure_classification": None,
                    "error": None,
                    "download_parent_trace": None,
                    "before_hover_handoff": None,
                    "after_hover_handoff": None,
                    "strict_production_visible_before_handoff": False,
                    "handoff_enterable": False,
                    "handoff_opacity_only_strict_visibility_difference": False,
                    "handoff_hit_owned": False,
                    "hover_controlled_handoff_attempted": False,
                    "hover_controlled_handoff_mouse_move_completed": False,
                    "hover_controlled_handoff_succeeded": False,
                    "hover_controlled_handoff_lost": False,
                    "hover_controlled_handoff_pre_move_revalidation": None,
                    "hover_controlled_handoff_post_move_revalidation": None,
                }
                sample_started = time.monotonic()
                submenu_started_at: float | None = None
                trace_cleanup_required = False
                menu_diagnostics = self._empty_candidate_menu_diagnostics()
                submenu_diagnostics: dict[str, Any] = {}
                try:
                    sample["cleanup_before_sample"] = (
                        self._dismiss_download_ui_probe_menus(page)
                    )
                    sample_started = time.monotonic()

                    sample["stage"] = "candidate_tile_reacquisition"
                    stage_started = time.monotonic()
                    matched_id, image, tile = self._exact_candidate_tile(candidate)
                    sample["candidate_tile_ms"] = round(
                        (time.monotonic() - stage_started) * 1_000,
                        3,
                    )
                    if matched_id != candidate_id:
                        raise FlowAutomationError(
                            "Probe candidate identity changed unexpectedly."
                        )

                    sample["stage"] = "candidate_menu"
                    download_parent, parent_snapshot = self._open_candidate_menu(
                        candidate_id,
                        image,
                        tile,
                        menu_diagnostics,
                    )
                    timing = menu_diagnostics.get("timing_ms") or {}
                    sample["toolbar_ms"] = timing.get("toolbar_reveal")
                    sample["more_options_ms"] = timing.get(
                        "more_options_activation"
                    )
                    sample["download_parent_ms"] = timing.get(
                        "download_parent_acquisition"
                    )
                    before_activation = (
                        menu_diagnostics.get("more_options_before_activation") or {}
                    )
                    sample["reveal_attempts"] = int(
                        before_activation.get("reveal_attempt") or 0
                    )
                    prior_reveal_outcomes = set(
                        before_activation.get("prior_reveal_outcomes") or ()
                    )
                    sample["candidate_tile_remounted"] = bool(
                        "tile_replaced_during_reveal" in prior_reveal_outcomes
                    )
                    sample["more_options_remounted"] = bool(
                        "control_replaced_or_unstable" in prior_reveal_outcomes
                    )

                    sample["stage"] = "download_submenu"
                    submenu_started_at = time.monotonic()
                    trace_cleanup_required = True
                    self._install_download_parent_probe_trace(download_parent)
                    submenu_diagnostics["_passive_parent_trace_enabled"] = True
                    _submenu, returned_diagnostics = self._expose_download_submenu(
                        candidate_id,
                        download_parent,
                        parent_snapshot,
                        diagnostics=submenu_diagnostics,
                    )
                    if returned_diagnostics is not submenu_diagnostics:
                        submenu_diagnostics.update(returned_diagnostics)
                    sample["submenu_ms"] = round(
                        (time.monotonic() - submenu_started_at) * 1_000,
                        3,
                    )
                    self._copy_download_ui_probe_submenu_diagnostics(
                        sample,
                        menu_diagnostics,
                        submenu_diagnostics,
                    )

                    sample["stage"] = "one_k_readiness"
                    stage_started = time.monotonic()
                    _one_k, _one_k_snapshot = (
                        self._wait_for_current_1k_original_size_option(
                            submenu_diagnostics,
                            timeout_ms=2_000,
                        )
                    )
                    sample["one_k_ready_ms"] = round(
                        (time.monotonic() - stage_started) * 1_000,
                        3,
                    )
                    sample["submenu_polls"] = max(
                        sample["submenu_polls"],
                        int(submenu_diagnostics.get("one_k_poll_count") or 0),
                    )
                    sample["status"] = "success"
                    sample["stage"] = "one_k_ready_not_activated"
                except Exception as exc:
                    message = str(exc)
                    sample["status"] = "failure"
                    sample["error"] = _safe_trace_header(message)
                    classification = re.match(r"^([A-Z][A-Z0-9_]+):", message)
                    sample["failure_classification"] = (
                        classification.group(1)
                        if classification is not None
                        else "DIAGNOSTIC_PROBE_FAILURE"
                    )
                    sample["final_safe_failure_classification"] = (
                        submenu_diagnostics.get("final_safe_failure_classification")
                        or sample["failure_classification"]
                    )
                    sample["candidate_tile_remounted"] = bool(
                        sample["candidate_tile_remounted"]
                        or "CANDIDATE_TILE_REPLACED" in message
                    )
                    sample["more_options_remounted"] = bool(
                        sample["more_options_remounted"]
                        or "MORE_OPTIONS_REPLACED" in message
                        or "MORE_OPTIONS_BBOX_UNSTABLE" in message
                    )
                finally:
                    if trace_cleanup_required:
                        try:
                            sample["download_parent_trace"] = (
                                self._remove_download_parent_probe_trace(page)
                            )
                        except Exception as exc:
                            sample["download_parent_trace"] = {
                                "installed": True,
                                "cleaned_up": False,
                                "trace_error": _safe_trace_header(str(exc)),
                            }
                    if submenu_started_at is not None and sample["submenu_ms"] is None:
                        sample["submenu_ms"] = round(
                            (time.monotonic() - submenu_started_at) * 1_000,
                            3,
                        )
                    self._copy_download_ui_probe_submenu_diagnostics(
                        sample,
                        menu_diagnostics,
                        submenu_diagnostics,
                    )
                    trace_summary = (
                        (sample.get("download_parent_trace") or {}).get("summary") or {}
                    )
                    sample["download_parent_remounted"] = bool(
                        sample["download_parent_remounted"]
                        or trace_summary.get("node_replacement_observed")
                    )
                    sample["total_ms"] = round(
                        (time.monotonic() - sample_started) * 1_000,
                        3,
                    )
                    try:
                        sample.update(self._download_ui_probe_page_state(page))
                    except Exception as exc:
                        sample["page_state_error"] = _safe_trace_header(str(exc))
                    try:
                        cleanup = self._dismiss_download_ui_probe_menus(page)
                        sample.update(cleanup)
                        if not cleanup["cleanup_neutral_state"]:
                            sample["menu_cleanup_warning"] = (
                                "DIAGNOSTIC_MENU_CLEANUP_NOT_NEUTRAL"
                            )
                    except Exception as exc:
                        sample["menu_cleanup_error"] = _safe_trace_header(str(exc))
                        sample["cleanup_neutral_state"] = False
                        if sample["status"] == "success":
                            sample["status"] = "failure"
                            sample["error"] = "DIAGNOSTIC_MENU_CLEANUP_FAILED"
                            sample["failure_classification"] = (
                                "DIAGNOSTIC_MENU_CLEANUP_FAILED"
                            )
                            sample["final_safe_failure_classification"] = (
                                "DIAGNOSTIC_MENU_CLEANUP_FAILED"
                            )
                    report["samples"].append(sample)

            try:
                final_state = self._download_ui_probe_page_state(page)
                report["final_document_has_focus"] = final_state[
                    "document_has_focus"
                ]
                report["final_document_visibility_state"] = final_state[
                    "document_visibility_state"
                ]
            except Exception as exc:
                report["final_page_state_error"] = _safe_trace_header(str(exc))
        finally:
            self.close()

        successful = [
            sample for sample in report["samples"] if sample["status"] == "success"
        ]

        def median_ms(key: str) -> float | None:
            values = [
                float(sample[key])
                for sample in successful
                if isinstance(sample.get(key), (int, float))
            ]
            return round(float(statistics.median(values)), 3) if values else None

        report["summary"] = {
            "condition": label,
            "success_count": len(successful),
            "sample_count": len(report["samples"]),
            "success_rate": round(
                len(successful) / len(report["samples"]),
                3,
            )
            if report["samples"]
            else 0.0,
            "median_more_options_ms": median_ms("toolbar_ms"),
            "median_more_options_activation_ms": median_ms("more_options_ms"),
            "median_submenu_ms": median_ms("submenu_ms"),
            "median_total_1k_ready_ms": median_ms("total_ms"),
            "total_reveal_retries": sum(
                max(0, int(sample["reveal_attempts"]) - 1)
                for sample in report["samples"]
            ),
            "candidate_tile_remounts": sum(
                bool(sample["candidate_tile_remounted"])
                for sample in report["samples"]
            ),
            "more_options_remounts": sum(
                bool(sample["more_options_remounted"])
                for sample in report["samples"]
            ),
            "download_parent_remounts": sum(
                bool(sample["download_parent_remounted"])
                for sample in report["samples"]
            ),
        }
        return report

    def trace_download_trigger(
        self,
        *,
        candidate: CandidateRecord,
        staging_dir: Path,
        activation: str,
        timeout_seconds: int = 10,
    ) -> dict[str, Any]:
        """Trace one exact Download activation without mutating queue state or assets."""
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("Download trigger trace timeout must be between 1 and 30 seconds.")
        if activation not in {"locator", "mouse", "keyboard"}:
            raise ValueError("Activation must be locator, mouse, or keyboard.")
        staging_dir = staging_dir.resolve()
        if not staging_dir.is_dir():
            raise FileNotFoundError(f"Trace staging directory does not exist: {staging_dir}")
        if any(staging_dir.iterdir()):
            raise FileExistsError(f"Trace staging directory must be empty: {staging_dir}")

        candidate_id = self._candidate_media_id(candidate)
        report: dict[str, Any] = {
            "schema": "nano-banana-download-trigger-trace/v2",
            "candidate_id": candidate_id,
            "timeout_seconds": timeout_seconds,
            "activation": activation,
            "status": "initializing",
            "stage": "open_session",
            "ui": {
                "candidate_tile_matched": False,
                "more_options_count": 0,
                "download_menu_item_count": 0,
                "trigger_method": activation,
                "activation_strategy": activation,
                "download_click_dispatched": False,
                "activation_dispatched": False,
                "menu_open_before_activation": False,
                "menu_open_after_activation": None,
                "menu_item_visible_after_trigger": None,
                "active_element_before": None,
                "active_element_after": None,
                "focus_changed": None,
                "focus_left_menu": False,
                "candidate_menu": self._empty_candidate_menu_diagnostics(),
            },
            "events": [],
            "in_page_events": [],
            "cdp": {
                "behavior": "allowAndName",
                "events_enabled": True,
                "download_will_begin": [],
                "download_progress": [],
            },
            "staging_files": [],
            "cleanup": {
                "hooks_restore_attempted": False,
                "hooks_restored": False,
                "hooks_found": [],
                "download_behavior_restore_attempted": False,
                "download_behavior_restored": False,
            },
        }
        page = None
        context = None
        cdp = None
        hooks_installed = False
        behavior_configured = False
        page_handlers: list[tuple[str, Any]] = []
        context_handlers: list[tuple[str, Any]] = []
        trace_started = time.monotonic()

        def add_event(kind: str, **facts: Any) -> None:
            if len(report["events"]) >= _TRACE_EVENT_LIMIT:
                return
            report["events"].append(
                {
                    "kind": kind,
                    "offset_ms": round(max(0.0, (time.monotonic() - trace_started) * 1000), 1),
                    **facts,
                }
            )

        def request_facts(request) -> dict[str, Any] | None:
            safe_url = _sanitize_trace_url(str(request.url), candidate_id)
            if safe_url is None:
                return None
            return {
                "url": safe_url,
                "method": _clean_text(str(request.method), 16).upper(),
                "resource_type": _clean_text(str(request.resource_type), 40),
            }

        def on_request(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event("request", **facts)

        def on_response(response) -> None:
            facts = request_facts(response.request)
            if not facts:
                return
            try:
                content_type = _safe_trace_header(response.header_value("content-type"))
                content_disposition = _safe_trace_header(
                    response.header_value("content-disposition")
                )
            except Exception:
                content_type = None
                content_disposition = None
            candidate_bytes = bool(
                (content_type or "").lower().startswith("image/")
                or "attachment" in (content_disposition or "").lower()
            )
            add_event(
                "response",
                **facts,
                status=int(response.status),
                content_type=content_type,
                content_disposition=content_disposition,
                candidate_bytes=candidate_bytes,
            )

        def on_request_finished(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event("requestfinished", **facts)

        def on_request_failed(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event(
                    "requestfailed",
                    **facts,
                    failure=_safe_trace_header(str(request.failure or "")),
                )

        def on_popup(popup) -> None:
            add_event("popup", url=_sanitize_trace_url(str(popup.url), candidate_id))

        def on_new_page(new_page) -> None:
            add_event("new_page", url=_sanitize_trace_url(str(new_page.url), candidate_id))

        def on_frame_navigated(frame) -> None:
            safe_url = _sanitize_trace_url(str(frame.url), candidate_id)
            if safe_url:
                add_event(
                    "frame_navigation",
                    url=safe_url,
                    main_frame=bool(page is not None and frame == page.main_frame),
                )

        def on_download_will_begin(event: dict[str, Any]) -> None:
            fact = {
                "guid": _clean_text(str(event.get("guid") or ""), 100) or None,
                "suggested_filename": _clean_text(
                    _safe_label(Path(str(event.get("suggestedFilename") or "")).name),
                    100,
                )
                or None,
                "url": _sanitize_trace_url(str(event.get("url") or ""), candidate_id),
            }
            report["cdp"]["download_will_begin"].append(fact)
            add_event("cdp_download_will_begin", **fact)

        def on_download_progress(event: dict[str, Any]) -> None:
            fact = {
                "guid": _clean_text(str(event.get("guid") or ""), 100) or None,
                "state": _clean_text(str(event.get("state") or ""), 40) or None,
                "received_bytes": int(event.get("receivedBytes") or 0),
                "total_bytes": int(event.get("totalBytes") or 0),
                "file_name": _clean_text(
                    _safe_label(Path(str(event.get("filePath") or "")).name),
                    100,
                )
                or None,
            }
            report["cdp"]["download_progress"].append(fact)
            add_event("cdp_download_progress", **fact)

        try:
            self.open()
            page = self._require_page()
            if self._browser is None:
                raise RuntimeError("Flow browser connection is not open.")
            context = page.context
            report["page_url_before"] = _sanitize_trace_url(str(page.url), candidate_id)
            cdp = self._browser.new_browser_cdp_session()
            cdp.on("Browser.downloadWillBegin", on_download_will_begin)
            cdp.on("Browser.downloadProgress", on_download_progress)

            report["stage"] = "match_candidate_tile"
            matched_id, image, tile = self._exact_candidate_tile(candidate)
            if matched_id != candidate_id:
                raise FlowAutomationError("Trace candidate identity changed unexpectedly.")
            report["ui"]["candidate_tile_matched"] = True

            report["stage"] = "open_candidate_menu"
            download_action, snapshot = self._open_candidate_menu(
                candidate_id,
                image,
                tile,
                report["ui"]["candidate_menu"],
            )
            report["ui"]["more_options_count"] = int(
                report["ui"]["candidate_menu"]["more_options_before_activation"]
                is not None
            )
            report["ui"]["download_menu_item_count"] = 1
            report["ui"]["download_control"] = snapshot
            report["ui"]["menu_open_before_activation"] = bool(
                report["ui"]["candidate_menu"]["menu_visible_after_options_activation"]
                and snapshot.get("visible")
                and snapshot.get("connected")
            )
            report["ui"]["active_element_before"] = snapshot.get("active_element")

            report["stage"] = "arm_bounded_trace"
            trace_started = time.monotonic()
            for event_name, handler in (
                ("request", on_request),
                ("response", on_response),
                ("requestfinished", on_request_finished),
                ("requestfailed", on_request_failed),
                ("popup", on_popup),
                ("framenavigated", on_frame_navigated),
            ):
                page.on(event_name, handler)
                page_handlers.append((event_name, handler))
            context.on("page", on_new_page)
            context_handlers.append(("page", on_new_page))
            self._install_download_trace_hooks(download_action, candidate_id)
            hooks_installed = True
            cdp.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(staging_dir),
                    "eventsEnabled": True,
                },
            )
            behavior_configured = True

            report["stage"] = "dispatch_exact_download_activation"
            activation_details = self._activate_download_control(
                download_action,
                activation,
                prepared_snapshot=snapshot,
            )
            report["ui"]["activation_details"] = activation_details
            report["ui"]["download_click_dispatched"] = True
            report["ui"]["activation_dispatched"] = True
            deadline = time.monotonic() + timeout_seconds
            visibility_checked = False
            report["stage"] = "observe_post_click_window"
            while time.monotonic() < deadline:
                page.wait_for_timeout(100)
                if not visibility_checked:
                    visibility_checked = True
                    try:
                        report["ui"]["menu_item_visible_after_trigger"] = bool(
                            download_action.is_visible()
                        )
                    except Exception:
                        pass

            report["page_url_after"] = _sanitize_trace_url(str(page.url), candidate_id)
            report["status"] = "trace_complete"
            report["stage"] = "complete"
        except Exception as exc:
            report["status"] = "trace_failed"
            report["failure"] = {
                "stage": report["stage"],
                "type": type(exc).__name__,
                "message": _safe_trace_header(str(exc)),
            }
        finally:
            if page is not None:
                report["cleanup"]["hooks_restore_attempted"] = True
                try:
                    hook_result = self._remove_download_trace_hooks(page)
                    report["cleanup"]["hooks_found"] = hook_result.get("found_keys", [])
                    report["cleanup"]["hooks_restored"] = bool(
                        hook_result.get("restored", True)
                    )
                    if hooks_installed:
                        report["in_page_events"] = hook_result["events"]
                    final_ui = hook_result["final"]
                    if hooks_installed:
                        report["ui"]["menu_open_after_activation"] = bool(
                            final_ui.get("menu_visible") or final_ui.get("target_visible")
                        )
                        report["ui"]["menu_item_visible_after_trigger"] = bool(
                            final_ui.get("target_visible")
                        )
                        report["ui"]["active_element_after"] = final_ui.get("active_element")
                        before_active = report["ui"].get("active_element_before")
                        after_active = report["ui"].get("active_element_after")
                        report["ui"]["focus_changed"] = before_active != after_active
                        dispatch_active = (
                            report["ui"].get("activation_details") or {}
                        ).get("active_element_at_dispatch")
                        report["ui"]["focus_left_menu"] = bool(
                            isinstance(dispatch_active, dict)
                            and dispatch_active.get("inside_menu") is True
                            and not (
                                isinstance(after_active, dict)
                                and after_active.get("inside_menu") is True
                            )
                        )
                        remaining = max(0, _TRACE_EVENT_LIMIT - len(report["events"]))
                        report["events"].extend(report["in_page_events"][:remaining])
                        report["events"] = report["events"][:_TRACE_EVENT_LIMIT]
                    if not report["cleanup"]["hooks_restored"]:
                        report["cleanup"]["hook_restore_error"] = "partial_hook_cleanup"
                except Exception as exc:
                    report["cleanup"]["hook_restore_error"] = type(exc).__name__
            if page is not None:
                for event_name, handler in page_handlers:
                    try:
                        page.remove_listener(event_name, handler)
                    except Exception:
                        pass
            if context is not None:
                for event_name, handler in context_handlers:
                    try:
                        context.remove_listener(event_name, handler)
                    except Exception:
                        pass
            if cdp is not None:
                report["cleanup"]["download_behavior_restore_attempted"] = True
                try:
                    cdp.send(
                        "Browser.setDownloadBehavior",
                        {"behavior": "default", "eventsEnabled": False},
                    )
                    report["cleanup"]["download_behavior_restored"] = True
                except Exception as exc:
                    report["cleanup"]["download_behavior_restore_error"] = type(exc).__name__
                try:
                    cdp.detach()
                except Exception as exc:
                    report["cleanup"]["cdp_detach_error"] = type(exc).__name__
            if page is not None:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            try:
                self.close()
            except Exception as exc:
                report["cleanup"]["session_close_error"] = type(exc).__name__

        report["staging_files"] = self._trace_staging_files(staging_dir)
        report["correlation"] = _correlate_download_trace(candidate_id, report["events"])
        page_url_changed = (
            "page_url_before" in report
            and "page_url_after" in report
            and report["page_url_before"] != report["page_url_after"]
        )
        report["page_url_changed"] = page_url_changed
        report["observed_mechanisms"] = _observed_download_mechanisms(report)
        acceptance = _download_action_acceptance(report)
        report["acceptance"] = acceptance
        report["outcome"] = (
            acceptance["code"] if report["status"] == "trace_complete" else "TRACE_FAILED"
        )
        return report

    def trace_download_manual(
        self,
        *,
        candidate: CandidateRecord,
        staging_dir: Path,
        timeout_seconds: int = 30,
        ready_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Passively observe a user's physical Download interaction for one candidate."""
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Manual download trace timeout must be between 1 and 120 seconds.")
        staging_dir = staging_dir.resolve()
        if not staging_dir.is_dir():
            raise FileNotFoundError(f"Trace staging directory does not exist: {staging_dir}")
        if any(staging_dir.iterdir()):
            raise FileExistsError(f"Trace staging directory must be empty: {staging_dir}")

        candidate_id = self._candidate_media_id(candidate)
        report: dict[str, Any] = {
            "schema": "nano-banana-manual-download-trace/v1",
            "trace_mode": "passive_manual",
            "candidate_id": candidate_id,
            "timeout_seconds": timeout_seconds,
            "status": "initializing",
            "stage": "open_session",
            "ui": {
                "candidate_tile_matched": False,
                "automated_ui_actions_dispatched": False,
                "more_options_input_observed": False,
                "download_control_seen": False,
                "download_activation_observed": False,
                "download_click_dispatched_by_automation": False,
                "menu_ever_opened": False,
                "menu_open_before_activation": None,
                "menu_open_after_activation": None,
                "active_element_before": None,
                "active_element_after": None,
                "focus_changed": None,
                "focus_left_menu": False,
                "download_control": None,
            },
            "events": [],
            "in_page_events": [],
            "cdp": {
                "behavior": "allowAndName",
                "events_enabled": True,
                "download_will_begin": [],
                "download_progress": [],
            },
            "staging_files": [],
            "cleanup": {
                "hooks_restore_attempted": False,
                "hooks_restored": False,
                "hooks_found": [],
                "download_behavior_restore_attempted": False,
                "download_behavior_restored": False,
            },
        }
        page = None
        context = None
        cdp = None
        hooks_installed = False
        page_handlers: list[tuple[str, Any]] = []
        context_handlers: list[tuple[str, Any]] = []
        trace_started = time.monotonic()

        def add_event(kind: str, **facts: Any) -> None:
            if len(report["events"]) >= _TRACE_EVENT_LIMIT:
                return
            report["events"].append(
                {
                    "kind": kind,
                    "offset_ms": round(max(0.0, (time.monotonic() - trace_started) * 1000), 1),
                    **facts,
                }
            )

        def request_facts(request) -> dict[str, Any] | None:
            safe_url = _sanitize_trace_url(str(request.url), candidate_id)
            if safe_url is None:
                return None
            return {
                "url": safe_url,
                "method": _clean_text(str(request.method), 16).upper(),
                "resource_type": _clean_text(str(request.resource_type), 40),
            }

        def on_request(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event("request", **facts)

        def on_response(response) -> None:
            facts = request_facts(response.request)
            if not facts:
                return
            try:
                content_type = _safe_trace_header(response.header_value("content-type"))
                content_disposition = _safe_trace_header(
                    response.header_value("content-disposition")
                )
            except Exception:
                content_type = None
                content_disposition = None
            add_event(
                "response",
                **facts,
                status=int(response.status),
                content_type=content_type,
                content_disposition=content_disposition,
                candidate_bytes=bool(
                    (content_type or "").lower().startswith("image/")
                    or "attachment" in (content_disposition or "").lower()
                ),
            )

        def on_request_finished(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event("requestfinished", **facts)

        def on_request_failed(request) -> None:
            facts = request_facts(request)
            if facts:
                add_event(
                    "requestfailed",
                    **facts,
                    failure=_safe_trace_header(str(request.failure or "")),
                )

        def on_popup(popup) -> None:
            add_event("popup", url=_sanitize_trace_url(str(popup.url), candidate_id))

        def on_new_page(new_page) -> None:
            add_event("new_page", url=_sanitize_trace_url(str(new_page.url), candidate_id))

        def on_frame_navigated(frame) -> None:
            safe_url = _sanitize_trace_url(str(frame.url), candidate_id)
            if safe_url:
                add_event(
                    "frame_navigation",
                    url=safe_url,
                    main_frame=bool(page is not None and frame == page.main_frame),
                )

        def on_download_will_begin(event: dict[str, Any]) -> None:
            fact = {
                "guid": _clean_text(str(event.get("guid") or ""), 100) or None,
                "suggested_filename": _clean_text(
                    _safe_label(Path(str(event.get("suggestedFilename") or "")).name),
                    100,
                )
                or None,
                "url": _sanitize_trace_url(str(event.get("url") or ""), candidate_id),
            }
            report["cdp"]["download_will_begin"].append(fact)
            add_event("cdp_download_will_begin", **fact)

        def on_download_progress(event: dict[str, Any]) -> None:
            fact = {
                "guid": _clean_text(str(event.get("guid") or ""), 100) or None,
                "state": _clean_text(str(event.get("state") or ""), 40) or None,
                "received_bytes": int(event.get("receivedBytes") or 0),
                "total_bytes": int(event.get("totalBytes") or 0),
                "file_name": _clean_text(
                    _safe_label(Path(str(event.get("filePath") or "")).name),
                    100,
                )
                or None,
            }
            report["cdp"]["download_progress"].append(fact)
            add_event("cdp_download_progress", **fact)

        try:
            self.open()
            page = self._require_page()
            if self._browser is None:
                raise RuntimeError("Flow browser connection is not open.")
            context = page.context
            report["page_url_before"] = _sanitize_trace_url(str(page.url), candidate_id)
            cdp = self._browser.new_browser_cdp_session()
            cdp.on("Browser.downloadWillBegin", on_download_will_begin)
            cdp.on("Browser.downloadProgress", on_download_progress)

            report["stage"] = "match_candidate_tile_read_only"
            matched_id, _image, _tile = self._exact_candidate_tile(candidate)
            if matched_id != candidate_id:
                raise FlowAutomationError("Trace candidate identity changed unexpectedly.")
            report["ui"]["candidate_tile_matched"] = True

            report["stage"] = "arm_passive_observers"
            trace_started = time.monotonic()
            for event_name, handler in (
                ("request", on_request),
                ("response", on_response),
                ("requestfinished", on_request_finished),
                ("requestfailed", on_request_failed),
                ("popup", on_popup),
                ("framenavigated", on_frame_navigated),
            ):
                page.on(event_name, handler)
                page_handlers.append((event_name, handler))
            context.on("page", on_new_page)
            context_handlers.append(("page", on_new_page))
            self._install_manual_download_trace_hooks(page, candidate_id)
            hooks_installed = True
            cdp.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(staging_dir),
                    "eventsEnabled": True,
                },
            )

            report["stage"] = "wait_for_physical_user_interaction"
            if ready_callback is not None:
                ready_callback()
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                page.wait_for_timeout(100)

            report["page_url_after"] = _sanitize_trace_url(str(page.url), candidate_id)
            report["status"] = "trace_complete"
            report["stage"] = "complete"
        except Exception as exc:
            report["status"] = "trace_failed"
            report["failure"] = {
                "stage": report["stage"],
                "type": type(exc).__name__,
                "message": _safe_trace_header(str(exc)),
            }
        finally:
            if page is not None:
                report["cleanup"]["hooks_restore_attempted"] = True
                try:
                    hook_result = self._remove_download_trace_hooks(page)
                    report["cleanup"]["hooks_found"] = hook_result.get("found_keys", [])
                    report["cleanup"]["hooks_restored"] = bool(
                        hook_result.get("restored", True)
                    )
                    if hooks_installed:
                        report["in_page_events"] = hook_result.get("events", [])
                        final_ui = hook_result.get("final", {})
                        report["ui"]["menu_ever_opened"] = bool(
                            final_ui.get("menu_ever_opened")
                        )
                        report["ui"]["download_control_seen"] = bool(
                            final_ui.get("download_control_seen")
                        )
                        report["ui"]["download_activation_observed"] = bool(
                            final_ui.get("download_activation_observed")
                        )
                        report["ui"]["menu_open_before_activation"] = final_ui.get(
                            "menu_open_before_activation"
                        )
                        report["ui"]["menu_open_after_activation"] = bool(
                            final_ui.get("menu_visible") or final_ui.get("target_visible")
                        )
                        report["ui"]["active_element_before"] = final_ui.get(
                            "active_element_before"
                        )
                        report["ui"]["active_element_after"] = final_ui.get(
                            "active_element"
                        )
                        before_active = report["ui"].get("active_element_before")
                        after_active = report["ui"].get("active_element_after")
                        report["ui"]["focus_changed"] = (
                            before_active != after_active
                            if before_active is not None
                            else None
                        )
                        report["ui"]["focus_left_menu"] = bool(
                            isinstance(before_active, dict)
                            and before_active.get("inside_menu") is True
                            and not (
                                isinstance(after_active, dict)
                                and after_active.get("inside_menu") is True
                            )
                        )
                        report["ui"]["more_options_input_observed"] = any(
                            event.get("kind") == "candidate_menu_request"
                            for event in report["in_page_events"]
                        )
                        control_event = next(
                            (
                                event
                                for event in report["in_page_events"]
                                if event.get("kind") == "download_control_seen"
                            ),
                            None,
                        )
                        if control_event:
                            report["ui"]["download_control"] = control_event.get("control")
                        remaining = max(0, _TRACE_EVENT_LIMIT - len(report["events"]))
                        report["events"].extend(report["in_page_events"][:remaining])
                        report["events"] = report["events"][:_TRACE_EVENT_LIMIT]
                    if not report["cleanup"]["hooks_restored"]:
                        report["cleanup"]["hook_restore_error"] = "partial_hook_cleanup"
                except Exception as exc:
                    report["cleanup"]["hook_restore_error"] = type(exc).__name__
            if page is not None:
                for event_name, handler in page_handlers:
                    try:
                        page.remove_listener(event_name, handler)
                    except Exception:
                        pass
            if context is not None:
                for event_name, handler in context_handlers:
                    try:
                        context.remove_listener(event_name, handler)
                    except Exception:
                        pass
            if cdp is not None:
                report["cleanup"]["download_behavior_restore_attempted"] = True
                try:
                    cdp.send(
                        "Browser.setDownloadBehavior",
                        {"behavior": "default", "eventsEnabled": False},
                    )
                    report["cleanup"]["download_behavior_restored"] = True
                except Exception as exc:
                    report["cleanup"]["download_behavior_restore_error"] = type(exc).__name__
                try:
                    cdp.detach()
                except Exception as exc:
                    report["cleanup"]["cdp_detach_error"] = type(exc).__name__
            try:
                self.close()
            except Exception as exc:
                report["cleanup"]["session_close_error"] = type(exc).__name__

        report["staging_files"] = self._trace_staging_files(staging_dir)
        report["correlation"] = _correlate_download_trace(candidate_id, report["events"])
        report["page_url_changed"] = bool(
            "page_url_before" in report
            and "page_url_after" in report
            and report["page_url_before"] != report["page_url_after"]
        )
        report["observed_mechanisms"] = _observed_download_mechanisms(report)
        acceptance = _download_action_acceptance(report)
        report["acceptance"] = acceptance
        if report["status"] != "trace_complete":
            report["outcome"] = "TRACE_FAILED"
        elif report["ui"]["download_activation_observed"]:
            report["outcome"] = acceptance["code"]
        elif acceptance["accepted"]:
            report["outcome"] = "MANUAL_TRACE_DOWNSTREAM_WITHOUT_MATCHED_ACTIVATION"
        else:
            report["outcome"] = "MANUAL_TRACE_NO_DOWNLOAD_ACTIVATION"
        report["comparison"] = _manual_download_comparison(report)
        return report

    def inspect_download_submenu(
        self,
        *,
        candidate: CandidateRecord,
        timeout_seconds: int = 30,
        ready_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Passively capture the visible Download parent/submenu DOM after user input."""
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Download submenu inspection timeout must be between 1 and 120 seconds.")
        candidate_id = self._candidate_media_id(candidate)
        report: dict[str, Any] = {
            "schema": "nano-banana-download-submenu-inspection/v1",
            "inspection_mode": "passive_manual_dom",
            "candidate_id": candidate_id,
            "timeout_seconds": timeout_seconds,
            "status": "initializing",
            "stage": "open_session",
            "automated_ui_actions_dispatched": False,
            "candidate_tile_matched": False,
            "sample_count": 0,
            "structural_change_count": 0,
            "observations": [],
            "temporal_menu_transition": {
                "observed": False,
                "classification": "not_observed",
                "parent_observation_index": None,
                "next_menu_observation_index": None,
            },
            "probe_error_types": [],
            "submenu_stable_polls": 0,
            "snapshot": _normalize_download_submenu_snapshot({}),
            "cleanup": {"session_close_attempted": False, "session_closed": False},
        }
        page = None
        best_score = -1
        prior_signature = None
        stable_detected_polls = 0
        parent_observation_index = None

        def score(snapshot: dict[str, Any]) -> int:
            item_count = sum(len(menu.get("items") or []) for menu in snapshot.get("menus") or [])
            return (
                (10_000 if snapshot.get("submenu_detected") else 0)
                + int(snapshot.get("visible_menu_count") or 0) * 100
                + item_count
                + int((snapshot.get("download_parent") or {}).get("match_count") == 1) * 10
            )

        try:
            self.open()
            page = self._require_page()
            report["page_url"] = _sanitize_trace_url(str(page.url), candidate_id)
            report["stage"] = "match_candidate_tile_read_only"
            matched_id, _image, _tile = self._exact_candidate_tile(candidate)
            if matched_id != candidate_id:
                raise FlowAutomationError("Submenu candidate identity changed unexpectedly.")
            report["candidate_tile_matched"] = True

            report["stage"] = "wait_for_manual_submenu"
            if ready_callback is not None:
                ready_callback()
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                try:
                    snapshot = self._inspect_download_submenu_dom(page)
                    report["sample_count"] += 1
                    signature = _download_submenu_signature(snapshot)
                    if signature != prior_signature:
                        report["structural_change_count"] += 1
                        if len(report["observations"]) < 12:
                            observation_index = len(report["observations"])
                            report["observations"].append(
                                {
                                    "index": observation_index,
                                    "sample_number": report["sample_count"],
                                    "snapshot": snapshot,
                                }
                            )
                            parent = snapshot.get("download_parent") or {}
                            indicator_present = bool(
                                parent.get("aria_haspopup")
                                or any(
                                    re.search(
                                        r"chevron|arrow|navigate.?next|right",
                                        " ".join(
                                            str(icon.get(key) or "")
                                            for key in ("text", "aria_label", "title")
                                        ),
                                        re.I,
                                    )
                                    for icon in parent.get("submenu_indicator") or []
                                )
                            )
                            if parent.get("match_count") == 1 and indicator_present:
                                parent_observation_index = observation_index
                            elif (
                                parent_observation_index is not None
                                and int(snapshot.get("visible_menu_count") or 0) > 0
                                and parent.get("match_count") == 0
                            ):
                                report["temporal_menu_transition"] = {
                                    "observed": True,
                                    "classification": "candidate_parent_to_next_visible_menu",
                                    "parent_observation_index": parent_observation_index,
                                    "next_menu_observation_index": observation_index,
                                }
                    prior_signature = signature
                    snapshot_score = score(snapshot)
                    if snapshot_score > best_score or (
                        snapshot_score == best_score and snapshot.get("submenu_detected")
                    ):
                        best_score = snapshot_score
                        report["snapshot"] = snapshot
                    if snapshot.get("submenu_detected"):
                        stable_detected_polls += 1
                    else:
                        stable_detected_polls = 0
                    report["submenu_stable_polls"] = stable_detected_polls
                    if stable_detected_polls >= 3:
                        break
                except Exception as exc:
                    error_type = type(exc).__name__
                    if error_type not in report["probe_error_types"]:
                        report["probe_error_types"].append(error_type)
                    stable_detected_polls = 0
                page.wait_for_timeout(200)

            report["status"] = "inspection_complete"
            report["stage"] = "complete"
            if report["snapshot"].get("submenu_detected"):
                report["outcome"] = "DOWNLOAD_SUBMENU_OBSERVED"
            elif report["temporal_menu_transition"]["observed"]:
                report["outcome"] = "DOWNLOAD_SUBMENU_CANDIDATE_TRANSITION_OBSERVED"
            elif report["sample_count"]:
                report["outcome"] = "DOWNLOAD_SUBMENU_NOT_OBSERVED"
            else:
                report["outcome"] = "DOWNLOAD_SUBMENU_DOM_PROBE_FAILED"
        except Exception as exc:
            report["status"] = "inspection_failed"
            report["outcome"] = "INSPECTION_FAILED"
            report["failure"] = {
                "stage": report["stage"],
                "type": type(exc).__name__,
                "message": _safe_trace_header(str(exc)),
            }
        finally:
            report["cleanup"]["session_close_attempted"] = True
            try:
                self.close()
                report["cleanup"]["session_closed"] = True
            except Exception as exc:
                report["cleanup"]["session_close_error"] = type(exc).__name__
        return report

    def _download_with_cdp(
        self,
        *,
        candidate_id: str,
        download_action,
        destination: Path,
        prepared_snapshot: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
    ) -> BrowserDownloadResult:
        """Capture one browser download through the attached Chrome's Browser domain."""
        page = self._require_page()
        if self._browser is None:
            raise RuntimeError("Flow browser connection is not open.")
        download_dir = destination.parent.resolve()
        before_names = interruptible_call(
            "download_file_appearance_poll",
            "staging_directory_baseline_scan",
            lambda: {path.name for path in download_dir.iterdir()},
        )
        begins: list[dict[str, Any]] = []
        progress: dict[str, dict[str, Any]] = {}
        progress_events: list[dict[str, Any]] = []
        browser_guid = None
        suggested_filename = None
        final_state = None
        trigger_dispatched = False
        menu_item_visible_after_trigger = None
        window_state = interruptible_call(
            "download_window_state",
            "download_window_state_read",
            self._exact_window_state,
        )
        activation_snapshot = prepared_snapshot
        if window_state == "minimized":
            activation_snapshot = activation_snapshot or self._inspect_download_menu_item(
                download_action
            )
            activation_method = self._download_activation_method(
                window_state,
                activation_snapshot,
            )
        else:
            activation_method = "mouse"
        cdp = interruptible_call(
            "download_cdp_tracking",
            "new_browser_cdp_session",
            self._browser.new_browser_cdp_session,
        )

        # Playwright annotates callbacks while wrapping them. A bound built-in
        # such as ``begins.append`` cannot accept those attributes, so always
        # register ordinary Python functions here.
        def record_download_begin(event: dict[str, Any]) -> None:
            begins.append(
                {
                    "guid": event.get("guid"),
                    "suggestedFilename": event.get("suggestedFilename"),
                }
            )

        def record_download_progress(event: dict[str, Any]) -> None:
            safe_event = {
                "guid": event.get("guid"),
                "state": event.get("state"),
                "filePath": event.get("filePath"),
            }
            progress_events.append(safe_event)
            progress[str(event.get("guid") or "")] = safe_event

        def failure(code: str, timeout_stage: str) -> FlowAutomationError:
            cancellation_checkpoint("download_failure_reconciliation")
            complete, partial = self._new_download_files(download_dir, before_names)
            activation_acknowledged = bool(begins or complete or partial)
            return FlowAutomationError(
                self._download_failure_message(
                    code,
                    candidate_id=candidate_id,
                    trigger_dispatched=trigger_dispatched,
                    menu_item_visible_after_trigger=menu_item_visible_after_trigger,
                    begins=begins,
                    browser_guid=browser_guid,
                    suggested_filename=suggested_filename,
                    progress_events=progress_events,
                    final_state=final_state,
                    complete_files=complete,
                    partial_files=partial,
                    timeout_stage=timeout_stage,
                    activation_method=activation_method,
                    window_state=window_state,
                    required_background_flags_present=(
                        self._required_background_flags_present
                    ),
                    page_lifecycle_keepalive_applied=(
                        self._page_lifecycle_keepalive_applied
                    ),
                    activation_acknowledged=activation_acknowledged,
                )
            )

        cdp.on("Browser.downloadWillBegin", record_download_begin)
        cdp.on("Browser.downloadProgress", record_download_progress)
        behavior_configured = False
        try:
            cancellation_checkpoint("download_cdp_configuration")
            try:
                interruptible_call(
                    "download_cdp_tracking",
                    "set_download_behavior",
                    cdp.send,
                    "Browser.setDownloadBehavior",
                    {
                        "behavior": "allowAndName",
                        "downloadPath": str(download_dir),
                        "eventsEnabled": True,
                    },
                )
                behavior_configured = True
            except Exception as exc:
                cancellation_checkpoint("download_cdp_configuration")
                raise failure(
                    "CDP_DOWNLOAD_CONFIGURATION_FAILED",
                    "configure_download_behavior",
                ) from exc
            cancellation_checkpoint("download_final_activation")
            try:
                interruptible_call(
                    "download_1k_activation",
                    "activate_1k_download_control",
                    self._activate_download_control,
                    download_action,
                    activation_method,
                    prepared_snapshot=activation_snapshot,
                )
                trigger_dispatched = True
            except Exception as exc:
                cancellation_checkpoint("download_final_activation")
                raise failure(
                    "DOWNLOAD_TRIGGER_NOT_CONFIRMED",
                    f"dispatch_final_1k_{activation_method}_activation",
                ) from exc
            deadline = time.monotonic() + timeout_seconds
            source_path = None
            while timeout_seconds > 0:
                cancellation_checkpoint("download_progress_wait")
                if menu_item_visible_after_trigger is None:
                    try:
                        menu_item_visible_after_trigger = bool(
                            interruptible_call(
                                "download_event_wait",
                                "download_action_visibility_read",
                                download_action.is_visible,
                            )
                        )
                    except Exception:
                        pass
                if len(begins) > 1:
                    browser_guid = ",".join(
                        _clean_text(str(event.get("guid") or ""), 100)
                        for event in begins
                    )
                    raise failure(
                        "MULTIPLE_DOWNLOADS_FOR_CANDIDATE",
                        "correlate_download_will_begin",
                    )
                if begins:
                    begin = begins[0]
                    browser_guid = str(begin.get("guid") or "")
                    if not browser_guid:
                        raise failure(
                            "DOWNLOAD_GUID_MISSING",
                            "correlate_download_will_begin",
                        )
                    raw_suggested = str(begin.get("suggestedFilename") or "")
                    suggested_filename = _clean_text(
                        _safe_label(Path(raw_suggested).name), 100
                    ) or None
                    latest = progress.get(browser_guid)
                    final_state = str(latest.get("state") or "") if latest else None
                    if final_state == "canceled":
                        raise failure(
                            "DOWNLOAD_CANCELLED",
                            "download_progress",
                        )
                    if final_state == "completed":
                        raw_path = latest.get("filePath")
                        completed_path = (
                            Path(str(raw_path)).resolve()
                            if raw_path
                            else (download_dir / browser_guid).resolve()
                        )
                        if interruptible_call(
                            "download_file_appearance_poll", "completed_staging_file_exists",
                            completed_path.is_file,
                        ):
                            source_path = completed_path
                            break
                if time.monotonic() >= deadline:
                    break
                interruptible_call(
                    "download_event_wait",
                    "download_progress_poll_wait",
                    page.wait_for_timeout,
                    100,
                )

            if source_path is None:
                complete, partial = interruptible_call(
                    "download_file_appearance_poll",
                    "staging_directory_reconciliation",
                    self._new_download_files,
                    download_dir,
                    before_names,
                )
                if len(begins) > 1 or len(complete) > 1:
                    raise failure(
                        "MULTIPLE_DOWNLOADS_FOR_CANDIDATE",
                        "reconcile_staging_files",
                    )
                elif partial:
                    raise failure(
                        "STAGING_DOWNLOAD_INCOMPLETE",
                        "reconcile_staging_files",
                    )
                elif begins and final_state == "completed":
                    raise failure(
                        "STAGING_FILE_MISSING",
                        "locate_completed_staging_file",
                    )
                elif begins:
                    raise failure(
                        "DOWNLOAD_PROGRESS_TIMEOUT",
                        "download_progress",
                    )
                elif complete:
                    raise failure(
                        "DOWNLOAD_WILL_BEGIN_TIMEOUT",
                        "require_download_will_begin",
                    )
                elif menu_item_visible_after_trigger is True:
                    raise failure(
                        (
                            "DOWNLOAD_ACTION_NOT_ACCEPTED_MINIMIZED"
                            if window_state == "minimized"
                            else "DOWNLOAD_ACTION_NOT_ACCEPTED"
                        ),
                        "download_menu_activation",
                    )
                else:
                    raise failure(
                        "DOWNLOAD_WILL_BEGIN_TIMEOUT",
                        "download_will_begin",
                    )

            try:
                source_path.relative_to(download_dir)
            except ValueError as exc:
                raise failure(
                    "STAGING_PATH_ESCAPED",
                    "validate_staging_containment",
                ) from exc

            # A terminal event can precede another begin queued by the same UI
            # activation. Keep a short bounded observation window before commit
            # so one final input can never silently install multiple downloads.
            duplicate_guard_deadline = time.monotonic() + 0.5
            while time.monotonic() < duplicate_guard_deadline:
                cancellation_checkpoint("download_duplicate_guard")
                interruptible_call(
                    "download_duplicate_guard",
                    "download_duplicate_guard_wait",
                    page.wait_for_timeout,
                    100,
                )
                complete, partial = interruptible_call(
                    "download_duplicate_guard",
                    "download_duplicate_guard_scan",
                    self._new_download_files,
                    download_dir,
                    before_names,
                )
                extra_complete = [path for path in complete if path.resolve() != source_path]
                if len(begins) > 1 or extra_complete or partial:
                    raise failure(
                        "MULTIPLE_DOWNLOADS_FOR_CANDIDATE",
                        "post_completion_duplicate_guard",
                    )
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite staging download: {destination}")
            interruptible_call(
                "atomic_download_staging_commit",
                "staging_file_rename",
                os.replace,
                source_path,
                destination,
            )
            return BrowserDownloadResult(
                candidate_id=candidate_id,
                staged_path=str(destination),
                browser_suggested_filename=suggested_filename,
                browser_download_guid=browser_guid,
                mechanism="cdp_download_events",
            )
        finally:
            if not cancellation_requested():
                try:
                    if behavior_configured:
                        interruptible_call(
                            "download_cleanup",
                            "restore_default_download_behavior",
                            cdp.send,
                            "Browser.setDownloadBehavior",
                            {"behavior": "default", "eventsEnabled": False},
                        )
                except Exception as exc:
                    raise FlowAutomationError(
                        "Could not restore Chrome's default download behavior."
                    ) from exc
                finally:
                    if not cancellation_requested():
                        interruptible_call(
                            "download_cleanup",
                            "detach_download_cdp_session",
                            cdp.detach,
                        )

    def download_candidate(
        self,
        *,
        candidate: CandidateRecord,
        destination: Path,
    ) -> BrowserDownloadResult:
        """Download one exact persisted candidate through Flow's visible tile menu."""
        destination = destination.resolve()
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite staging download: {destination}")
        if not destination.parent.is_dir():
            raise FileNotFoundError(f"Download staging directory does not exist: {destination.parent}")

        with cancellation_blocking_operation(
            "download_candidate_pipeline",
            "download_candidate_session",
        ), self:
            self._candidate_history_recovery = None
            interruptible_call(
                "download_window_readiness",
                "download_window_normalization",
                self.ensure_production_window_usable,
                reason="startup_nonmaximized",
                candidate_id=self._candidate_media_id(candidate),
            )
            interruptible_call(
                "download_context_validation",
                "download_project_context_read",
                self._assert_download_context,
            )
            candidate_id, image, tile = interruptible_call(
                "candidate_reacquisition",
                "exact_candidate_reacquisition",
                self._exact_candidate_tile,
                candidate,
                reveal_virtualized_history=True,
            )
            page = self._require_page()
            try:
                submenu_diagnostics: dict[str, Any] = {}
                for exposure_attempt in (1, 2):
                    if exposure_attempt == 2:
                        reacquired_id, image, tile = interruptible_call(
                            "candidate_reacquisition",
                            "download_retry_candidate_reacquisition",
                            self._exact_candidate_tile,
                            candidate,
                            reveal_virtualized_history=True,
                        )
                        if reacquired_id != candidate_id:
                            raise FlowAutomationError(
                                "Candidate identity changed during Download submenu "
                                "reacquisition; refusing further UI input."
                            )
                    download_parent, parent_snapshot = interruptible_call(
                        "download_button_interaction",
                        "candidate_download_menu_open",
                        self._open_candidate_menu,
                        candidate_id,
                        image,
                        tile,
                        self._empty_candidate_menu_diagnostics(),
                    )
                    submenu_diagnostics = {
                        "exposure_attempt": exposure_attempt,
                        "reacquisition_retry_count": exposure_attempt - 1,
                    }
                    try:
                        _submenu, submenu_diagnostics = interruptible_call(
                            "download_submenu_wait",
                            "download_submenu_exposure",
                            self._expose_download_submenu,
                            candidate_id,
                            download_parent,
                            parent_snapshot,
                            diagnostics=submenu_diagnostics,
                        )
                        break
                    except FlowAutomationError as exc:
                        cancellation_checkpoint("download_submenu_wait")
                        if exposure_attempt == 1 and (
                            self._download_submenu_reacquisition_retry_allowed(
                                exc,
                                submenu_diagnostics,
                            )
                        ):
                            continue
                        raise
                download_1k, final_snapshot = interruptible_call(
                    "download_1k_option_wait",
                    "download_1k_readiness",
                    self._wait_for_current_1k_original_size_option,
                    submenu_diagnostics,
                )
                return interruptible_call(
                    "download_event_wait",
                    "cdp_download_pipeline",
                    self._download_with_cdp,
                    candidate_id=candidate_id,
                    download_action=download_1k,
                    destination=destination,
                    prepared_snapshot=final_snapshot,
                )
            finally:
                if not cancellation_requested():
                    try:
                        interruptible_call(
                            "download_cleanup",
                            "dismiss_download_menu",
                            page.keyboard.press,
                            "Escape",
                        )
                    except Exception:
                        pass
                    if not cancellation_requested():
                        interruptible_call(
                            "download_cleanup",
                            "restore_candidate_history_view",
                            self._restore_candidate_history_view,
                            candidate_id,
                        )

    def _generate_one_in_open_session(
        self,
        *,
        item: BatchItem,
        settings: GenerationSettings,
        forbidden_candidate_ids: frozenset[str] = frozenset(),
        inspection: FlowInspection | None = None,
    ) -> GenerationResult:
        """Run one generation attempt using the already-open Flow page."""
        recovery_event_start = len(self._window_recovery_events)
        self._set_generation_interrupt_checkpoint("window_readiness")
        self.ensure_production_window_usable(reason="startup_nonmaximized")
        self._set_generation_interrupt_checkpoint("editor_readiness")
        configuration = (
            self.configure_generation(settings)
            if inspection is None
            else self.configure_generation(settings, inspection=inspection)
        )
        self._set_generation_interrupt_checkpoint("pre_start_baseline")
        self._begin_prestart_timing()
        self._prestart_mark("candidate_baseline_start_ms")
        baseline_assets = self._asset_snapshot()
        baseline = {asset["fingerprint"] for asset in baseline_assets}
        baseline_candidate_ids = {
            str(asset["mediaId"])
            for asset in baseline_assets
            if re.fullmatch(
                r"[A-Za-z0-9_-]{8,100}",
                str(asset.get("mediaId") or ""),
            )
        }
        self._prestart_mark("candidate_baseline_complete_ms")
        self._prestart_mark("progress_baseline_start_ms")
        baseline_progress = set(self._progress_signals())
        self._prestart_mark("progress_baseline_complete_ms")
        submission = None
        self._generation_result_observation = None
        self._candidate_correlation_failure_observation = None
        try:
            self.ensure_production_window_usable(reason="startup_nonmaximized")
            submission = self.submit_prompt(
                item.prompt,
                baseline_fingerprints=baseline,
                baseline_candidate_ids=baseline_candidate_ids,
                forbidden_candidate_ids=forbidden_candidate_ids,
                baseline_progress=baseline_progress,
                keep_attempt_observer=True,
            )
            self._set_generation_interrupt_checkpoint(
                "result_wait",
                start_mouse_down_sent=True,
                start_acknowledged=True,
            )
            candidates = self.wait_for_result(
                baseline,
                settings.result_timeout_seconds,
                baseline_progress=baseline_progress,
                expected_count=configuration["configured_variants"],
                baseline_candidate_ids=baseline_candidate_ids,
                forbidden_candidate_ids=forbidden_candidate_ids,
            )
        except Exception as exc:
            cancellation_checkpoint("generation_failure_handling")
            if not isinstance(exc, (GenerationPreflightError, TemporaryGenerationBlock)):
                try:
                    observation = self._generation_failure_observation(
                        error=exc,
                        submission=submission,
                        baseline_fingerprints=baseline,
                        baseline_progress=baseline_progress,
                        baseline_candidate_ids=baseline_candidate_ids,
                        forbidden_candidate_ids=forbidden_candidate_ids,
                    )
                    exc.args = (
                        f"{exc} [generation_observation="
                        f"{json.dumps(observation, sort_keys=True, separators=(',', ':'))}]",
                    )
                except Exception:
                    pass  # Diagnostics must never replace the original failure.
            raise
        finally:
            self._stop_current_generation_attempt_observation()
        generation_recoveries = self._window_recovery_events[recovery_event_start:]
        if generation_recoveries:
            configuration = {
                **configuration,
                "window_recovery_events": generation_recoveries,
            }
        if isinstance(submission, dict) and isinstance(
            submission.get("prestart_timing"), dict
        ):
            configuration = {
                **configuration,
                "prestart_timing": submission["prestart_timing"],
            }
        if isinstance(submission, dict) and isinstance(
            submission.get("post_start_ack_timing"), dict
        ):
            saved_timing = submission["post_start_ack_timing"]
            if self.prestart_timing_debug and self._post_start_ack_timing is not None:
                saved_timing = {
                    **saved_timing,
                    "observer_read_breakdown": self._post_start_ack_timing.observer_read_snapshot(),
                }
            configuration = {
                **configuration,
                "post_start_ack_timing": saved_timing,
            }
        details = json.dumps(configuration, ensure_ascii=False, sort_keys=True)
        return GenerationResult(candidates=candidates, details=details)

    def generate_one(
        self,
        *,
        item: BatchItem,
        settings: GenerationSettings,
        forbidden_candidate_ids: frozenset[str] = frozenset(),
    ) -> GenerationResult:
        """Configure Flow, submit exactly one prompt, and discover visible results."""
        with self._generation_interrupt_scope(), self:
            return self._generate_one_in_open_session(
                item=item,
                settings=settings,
                forbidden_candidate_ids=forbidden_candidate_ids,
            )

    def generate_one_with_unusual_activity_recovery(
        self,
        *,
        item: BatchItem,
        settings: GenerationSettings,
        forbidden_candidate_ids: frozenset[str],
        project: FlowProjectReference,
        retry_started: Callable[[], None],
        generic_failure_retry_started: Callable[
            [FlowGenericGenerationFailed], bool
        ] | None = None,
        policy_failure_retry_started: Callable[[FlowPolicyRejected], bool] | None = None,
    ) -> GenerationResult:
        """Apply bounded exact-failure retries inside the same open session."""
        with self._generation_interrupt_scope(), self:
            unusual_retry_used = False
            inspection = None
            while True:
                cancellation_checkpoint("generation_attempt_dispatch")
                try:
                    return self._generate_one_in_open_session(
                        item=item,
                        settings=settings,
                        forbidden_candidate_ids=forbidden_candidate_ids,
                        inspection=inspection,
                    )
                except FlowUnusualActivity:
                    if unusual_retry_used:
                        raise
                    inspection = self._recover_unusual_activity_in_open_session(
                        project=project,
                    )
                    self._reset_generation_interrupt_checkpoint()
                    self._set_generation_interrupt_checkpoint(
                        "unusual_activity_retry_preparation"
                    )
                    retry_started()
                    unusual_retry_used = True
                except FlowGenericGenerationFailed as exc:
                    self._reset_generation_interrupt_checkpoint()
                    self._set_generation_interrupt_checkpoint(
                        "generic_failure_retry_preparation"
                    )
                    if (
                        generic_failure_retry_started is None
                        or not generic_failure_retry_started(exc)
                    ):
                        raise
                    inspection = None
                    self._set_generation_interrupt_checkpoint(
                        "generic_failure_retry_dispatch"
                    )
                except FlowPolicyRejected as exc:
                    self._reset_generation_interrupt_checkpoint()
                    self._set_generation_interrupt_checkpoint("policy_retry_preparation")
                    if (
                        policy_failure_retry_started is None
                        or not policy_failure_retry_started(exc)
                    ):
                        raise
                    self._reset_generation_interrupt_checkpoint()
                    self._set_generation_interrupt_checkpoint("policy_retry_dispatch")
                    inspection = None
