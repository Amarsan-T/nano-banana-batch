import inspect
import json
import signal
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters import flow
from nano_batch.adapters.flow import FlowAutomationError, FlowSession, _GenerationNetworkTiming, _PostStartAckTimingRecorder
from nano_batch.cancellation import authoritative_cancellation_scope


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class CDP:
    def __init__(self):
        self.listeners = []
        self.calls = []
        self.fail = None
        self.removal_fails = False

    def on(self, name, handler):
        assert name in {"Network.requestWillBeSent", "Network.responseReceived", "Network.loadingFinished"}
        self.listeners.append(handler)

    def remove_listener(self, name, handler):
        assert name in {"Network.requestWillBeSent", "Network.responseReceived", "Network.loadingFinished"}
        if self.removal_fails:
            raise RuntimeError("PRIVATE_TOKEN")
        self.listeners.remove(handler)

    def send(self, name, *args):
        self.calls.append((name, args))
        if self.fail == name:
            raise RuntimeError("PRIVATE_PROMPT PRIVATE_TOKEN")
        return {"metrics": [{"name": "Timestamp", "value": 100.9}, {"name": "NavigationStart", "value": 100.0}]}

    def emit(self, event, name="Network.requestWillBeSent"):
        # The fake retains callbacks in registration order for lifecycle assertions.
        offset = {"Network.requestWillBeSent": 0, "Network.responseReceived": 1, "Network.loadingFinished": 2}[name]
        for handler in tuple(self.listeners)[offset::3]:
            handler(event)


class GenerationNetworkTimingTests(unittest.TestCase):
    endpoint = "https://synthetic.googleapis.com/v1/images:generateImages"
    verified = frozenset({("synthetic.googleapis.com", "/v1/images:generateImages")})

    def setup_timing(self):
        clock, cdp = Clock(), CDP()
        diagnostic = _GenerationNetworkTiming(cdp, clock)
        diagnostic.arm()
        recorder = _PostStartAckTimingRecorder(clock, initial_composer_token=None, origin_source="offline")
        recorder._origin = 10.1
        recorder.set_browser_anchor(dict(browser_now_ms=900, python_midpoint=10, uncertainty_ms=1))
        recorder._marks["ack_ms"] = 7600
        return clock, cdp, diagnostic, recorder

    def event(self, start=142, **updates):
        event = {"timestamp": (101000 + start) / 1000, "wallTime": 999999999,
                 "type": "Fetch", "request": {"method": "POST", "url": self.endpoint, "hasPostData": True},
                 "initiator": {"type": "script", "stack": "PRIVATE_ACCOUNT"}}
        event.update(updates)
        return event

    @staticmethod
    def raw(**updates):
        raw = {"pointerdown_at_ms": 1000, "click_at_ms": 1002, "read_at_ms": 9200,
               "longtask_supported": True, "longtask_count": 2, "first_longtask_at_ms": 1060,
               "longest_longtask_at_ms": 1110, "longest_longtask_ms": 6800}
        raw.update(updates)
        return raw

    def snapshot(self, diagnostic, recorder, **updates):
        raw = self.raw(**updates)
        sentinel = FlowSession._event_loop_sentinel_summary(raw, [{"wall_ms": 7100, "evaluate_enter_browser_ms": 8250}], 1)
        return diagnostic.snapshot(raw, recorder, sentinel)

    def test_early_browser_request_is_not_delayed_python_receipt(self):
        clock, cdp, diagnostic, recorder = self.setup_timing()
        clock.now = 17.31
        with patch.object(flow, "_GENERATION_NETWORK_VERIFIED_ENDPOINTS", self.verified):
            cdp.emit(self.event())
        result = self.snapshot(diagnostic, recorder)
        self.assertEqual(result["generation_request_start_relative_to_pointerdown_ms"], 142)
        self.assertEqual(result["exact_generation_request"]["python_event_receive_ms"], 7210)
        self.assertEqual(result["exact_generation_request"]["timing_relation"], "during_longtask")
        self.assertEqual(result["ack_ms"], 7600)
        self.assertEqual(result["observer_evaluate_start_ms"], 7250)
        self.assertFalse(result["server_receipt_or_work_start_measured"])

    def test_late_browser_request_after_paired_longest_task(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        with patch.object(flow, "_GENERATION_NETWORK_VERIFIED_ENDPOINTS", self.verified):
            cdp.emit(self.event(7040))
        result = self.snapshot(diagnostic, recorder)
        self.assertEqual(result["generation_request_start_relative_to_pointerdown_ms"], 7040)
        self.assertEqual(result["exact_generation_request"]["timing_relation"], "after_longtask")

    def test_python_receive_delay_does_not_change_browser_start(self):
        starts = []
        for received in (10.3, 17.31):
            clock, cdp, diagnostic, recorder = self.setup_timing()
            clock.now = received
            cdp.emit(self.event())
            starts.append(self.snapshot(diagnostic, recorder)["candidates"][0]["request_start_ms"])
        self.assertEqual(starts, [142, 142])

    def test_before_task_and_uncertain_boundary_are_not_causal_claims(self):
        for start, relation in ((30, "before_longtask"), (110, "boundary_uncertain")):
            _, cdp, diagnostic, recorder = self.setup_timing()
            cdp.emit(self.event(start))
            self.assertEqual(self.snapshot(diagnostic, recorder)["candidates"][0]["timing_relation"], relation)

    def test_first_task_start_is_not_combined_with_longest_duration(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        cdp.emit(self.event(100))
        result = self.snapshot(diagnostic, recorder)
        self.assertEqual(result["first_longtask_start_ms"], 60)
        self.assertEqual(result["longest_longtask_start_ms"], 110)
        self.assertEqual(result["candidates"][0]["timing_relation"], "before_longtask")
        result = self.snapshot(diagnostic, recorder, longest_longtask_at_ms=None)
        self.assertEqual(result["candidates"][0]["timing_relation"], "unavailable")

    def test_no_endpoint_is_currently_verified_by_repository(self):
        self.assertEqual(flow._GENERATION_NETWORK_VERIFIED_ENDPOINTS, frozenset())
        _, cdp, diagnostic, recorder = self.setup_timing()
        cdp.emit(self.event())
        result = self.snapshot(diagnostic, recorder)
        self.assertIsNone(result["exact_generation_request"])
        self.assertEqual(result["candidates"][0]["identification_evidence"], "image_generation_rpc_semantics_unverified")
        self.assertIsNone(result["generation_request_start_relative_to_pointerdown_ms"])

    def test_multiple_strong_candidates_remain_unresolved(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        with patch.object(flow, "_GENERATION_NETWORK_VERIFIED_ENDPOINTS", self.verified):
            cdp.emit(self.event(142))
            cdp.emit(self.event(180))
        self.assertIsNone(self.snapshot(diagnostic, recorder)["exact_generation_request"])

    def test_pre_pointerdown_and_after_read_requests_excluded(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        for start in (-150, 142, 9000):
            cdp.emit(self.event(start))
        self.assertEqual([e["request_start_ms"] for e in self.snapshot(diagnostic, recorder)["candidates"]], [142])

    def test_missing_or_unreliable_clock_makes_request_timing_unavailable(self):
        for missing in ("anchor", "navigation", "inconsistent", "uncertain", "pointer", "expired"):
            _, cdp, diagnostic, recorder = self.setup_timing()
            cdp.emit(self.event())
            updates = {}
            if missing == "anchor":
                recorder.set_browser_anchor(None)
            elif missing == "navigation":
                diagnostic.metrics.pop("NavigationStart")
            elif missing == "inconsistent":
                diagnostic.metrics["NavigationStart"] += 1000
            elif missing == "uncertain":
                recorder._browser_anchor["uncertainty_ms"] = 100
            elif missing == "pointer":
                updates["pointerdown_at_ms"] = None
            else:
                updates["capture_expired"] = True
            result = self.snapshot(diagnostic, recorder, **updates)
            self.assertIsNone(result["candidates"][0]["request_start_ms"], missing)
            self.assertIsNone(result["exact_generation_request"], missing)

    def test_request_sensitive_fields_are_never_read_or_retained(self):
        class SensitiveRequest(dict):
            def get(self, key, *args):
                if key in {"postData", "postDataEntries", "headers", "cookies"}:
                    raise AssertionError("sensitive field read")
                return super().get(key, *args)
        _, cdp, diagnostic, recorder = self.setup_timing()
        request = SensitiveRequest(method="POST", hasPostData=True,
            url="https://private-account.googleapis.com/v1/projects/PRIVATE_UUID/images:generateImages?prompt=PRIVATE_PROMPT&token=PRIVATE_TOKEN#PRIVATE_EMAIL",
            postData="PRIVATE_PROMPT", headers={"Authorization": "PRIVATE_TOKEN", "Cookie": "PRIVATE_COOKIE"})
        cdp.emit(self.event(request=request))
        result = self.snapshot(diagnostic, recorder)
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("private-account", encoded)
        self.assertNotIn("?", encoded)
        self.assertNotIn("path_pattern", result["candidates"][0])
        self.assertRegex(result["candidates"][0]["endpoint_fp"], r"^[0-9a-f]{12}$")
        self.assertIsNone(result["candidates"][0]["payload_length"])

    def test_unknown_path_host_and_encoded_values_are_redacted(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        cdp.emit(self.event(request=dict(method="POST", url="https://PRIVATE_USER:PRIVATE_TOKEN@PRIVATE_HOST/PRIVATE_PROMPT/%70rivate/PRIVATE_UUID")))
        event = self.snapshot(diagnostic, recorder)["candidates"][0]
        self.assertEqual(event["host"], "[other-host]")
        self.assertNotIn("path_pattern", event)
        self.assertNotIn("PRIVATE", json.dumps(event))

    def test_candidate_storage_and_printing_are_bounded(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        for i in range(1100):
            cdp.emit(self.event(142 + i))
        result = self.snapshot(diagnostic, recorder)
        self.assertEqual(diagnostic.event_count, 1000)
        self.assertEqual(len(diagnostic.events), 32)
        self.assertEqual(len(result["candidates"]), 10)
        self.assertTrue(result["truncated"])
        self.assertIsNone(result["exact_generation_request"])
        text = FlowSession._format_generation_network_timing({"generation_network_timing": result})
        self.assertEqual(text.count("  candidate #"), 10)
        self.assertLess(len(text), 6500)

    def test_capture_expires_without_rpc_or_traffic_modification(self):
        clock, cdp, diagnostic, recorder = self.setup_timing()
        calls = list(cdp.calls)
        clock.now += 31
        cdp.emit(self.event())
        self.assertFalse(diagnostic.accepting)
        self.assertTrue(diagnostic.truncated)
        self.assertEqual(cdp.calls, calls)
        self.assertEqual(self.snapshot(diagnostic, recorder)["candidate_count"], 0)

    def test_listener_lifecycle_and_domains_are_attempt_scoped(self):
        _, cdp, diagnostic, _ = self.setup_timing()
        self.assertEqual([name for name, _ in cdp.calls], ["Network.enable", "Performance.enable", "Performance.getMetrics", "Performance.disable"])
        diagnostic.stop()
        diagnostic.stop()
        self.assertEqual(cdp.listeners, [])
        self.assertEqual([name for name, _ in cdp.calls].count("Network.disable"), 1)
        for _ in range(5):
            next_attempt = _GenerationNetworkTiming(cdp)
            next_attempt.arm()
            self.assertEqual(len(cdp.listeners), 3)
            next_attempt.stop()
        self.assertEqual(cdp.listeners, [])

    def session(self, enabled=True):
        session = FlowSession("https://flow.google.com", Path("unused"), prestart_timing_debug=enabled)
        session.page = MagicMock()
        session._page_cdp_session = CDP()
        return session

    def test_disabled_flag_does_not_enable_domains_or_install_listener(self):
        session = self.session(False)
        session._arm_generation_network_timing()
        session._capture_event_loop_sentinel()
        session._stop_generation_network_timing()
        self.assertEqual(session._page_cdp_session.calls, [])
        self.assertEqual(session._page_cdp_session.listeners, [])
        session.page.evaluate.assert_not_called()

    def test_existing_page_session_only_and_missing_cdp_is_nonfatal(self):
        session = self.session()
        cdp = session._page_cdp_session
        session._arm_generation_network_timing()
        self.assertIs(session._generation_network_timing.cdp, cdp)
        session._stop_generation_network_timing()
        session._page_cdp_session = None
        session._arm_generation_network_timing()
        self.assertEqual(session._generation_network_observation["status"], "existing_page_cdp_unavailable")
        session.page.context.new_cdp_session.assert_not_called()
        session.page.evaluate.assert_not_called()

    def test_failed_listener_removal_never_stacks_next_listener(self):
        session = self.session()
        session._arm_generation_network_timing()
        session._page_cdp_session.removal_fails = True
        session._arm_generation_network_timing()
        self.assertEqual(len(session._page_cdp_session.listeners), 3)
        self.assertFalse(session._generation_network_timing.accepting)
        self.assertEqual(session._generation_network_observation["status"], "previous_listener_removal_failed")

    def test_optional_setup_failures_do_not_fail_production(self):
        for method in ("Network.enable", "Performance.enable", "Performance.getMetrics", "Performance.disable"):
            session = self.session()
            session._page_cdp_session.fail = method
            session._arm_generation_network_timing()
            self.assertNotIn("PRIVATE", session._generation_network_timing.status)
            session._stop_generation_network_timing()
            self.assertEqual(session._page_cdp_session.listeners, [])

    def test_reuses_single_existing_post_ack_sentinel_read(self):
        session = self.session()
        session._arm_generation_network_timing()
        session._begin_post_start_ack_timing(origin_source="offline")
        session.page.evaluate.return_value = self.raw()
        session._capture_event_loop_sentinel()
        session._capture_event_loop_sentinel()
        session.page.evaluate.assert_called_once()
        self.assertEqual(session._page_cdp_session.listeners, [])
        self.assertIn("generation_network_timing", session._post_start_ack_timing_snapshot())

    def test_one_sigint_during_sentinel_read_starts_no_cleanup_rpc(self):
        session = self.session()
        session._arm_generation_network_timing()
        calls = list(session._page_cdp_session.calls)
        session.page.evaluate.side_effect = lambda *_: signal.raise_signal(signal.SIGINT)
        with authoritative_cancellation_scope() as state:
            with self.assertRaises(KeyboardInterrupt):
                session._capture_event_loop_sentinel()
            session._stop_current_generation_attempt_observation()
            self.assertEqual(state.interrupt_signal_count, 1)
        self.assertEqual(session._page_cdp_session.calls, calls)
        self.assertEqual(session._page_cdp_session.listeners, [])
        session.page.evaluate.assert_called_once()

    def test_latched_cancellation_removes_listener_without_browser_rpc(self):
        session = self.session()
        session._arm_generation_network_timing()
        calls = list(session._page_cdp_session.calls)
        with authoritative_cancellation_scope() as state:
            state.request()
            session._capture_event_loop_sentinel()
            session._stop_generation_network_timing()
        self.assertEqual(session._page_cdp_session.calls, calls)
        self.assertEqual(session._page_cdp_session.listeners, [])
        session.page.evaluate.assert_not_called()

    def test_normal_attempt_cleanup_removes_listener(self):
        session = self.session()
        session._arm_generation_network_timing()
        session._stop_current_generation_attempt_observation()
        self.assertEqual(session._page_cdp_session.listeners, [])
        self.assertEqual(session._page_cdp_session.calls[-1][0], "Network.disable")

    def test_same_submission_path_arms_before_start_and_stops_after_ack_or_error(self):
        for enabled in (False, True):
            for failure in (None, FlowAutomationError("existing terminal failure")):
                session = self.session(enabled)
                prompt, button = MagicMock(), MagicMock()
                sequence = []
                def activate(*_args):
                    sequence.append("start")
                    self.assertEqual(len(session._page_cdp_session.listeners), 3 * int(enabled))
                    session._begin_post_start_ack_timing(origin_source="offline")
                    return {}
                def wait(**_kwargs):
                    sequence.append("ack")
                    if failure:
                        raise failure
                    return {"signal": "new_generation_progress"}
                def read(*_args):
                    sequence.append("existing_sentinel_read")
                    return self.raw()
                session.page.evaluate.side_effect = read
                with patch.object(session, "_wait_for_owned_generation_control_structure", return_value=(prompt, {})), \
                     patch.object(session, "_wait_for_generation_submit_button", return_value=(prompt, button, {}, {})), \
                     patch.object(session, "_begin_current_generation_attempt_observation"), \
                     patch.object(session, "_arm_current_generation_attempt_observation"), \
                     patch.object(session, "_activate_generation_submit_button", side_effect=activate) as activation, \
                     patch.object(session, "_wait_for_generation_start", side_effect=wait) as ack_wait, \
                     patch.object(session, "_capture_latency_counts"), patch("builtins.print"):
                    if failure:
                        with self.assertRaises(FlowAutomationError) as caught:
                            session.submit_prompt("offline", baseline_fingerprints=set(), baseline_progress=set(), keep_attempt_observer=True)
                        self.assertIs(caught.exception, failure)
                    else:
                        session.submit_prompt("offline", baseline_fingerprints=set(), baseline_progress=set(), keep_attempt_observer=True)
                self.assertEqual(sequence, ["start", "ack"] + (["existing_sentinel_read"] if enabled else []))
                self.assertEqual(session._page_cdp_session.listeners, [])
                self.assertEqual(session.page.evaluate.call_count, int(enabled))
                activation.assert_called_once()
                ack_wait.assert_called_once()

    def test_one_sigint_during_network_setup_is_not_a_production_failure(self):
        session = self.session()
        def send(*_args):
            signal.raise_signal(signal.SIGINT)
        session._page_cdp_session.send = MagicMock(side_effect=send)
        with authoritative_cancellation_scope() as state:
            with self.assertRaises(KeyboardInterrupt):
                session._arm_generation_network_timing()
            self.assertEqual(state.interrupt_signal_count, 1)
        self.assertEqual(session._page_cdp_session.listeners, [])
        session._page_cdp_session.send.assert_called_once()

    def test_each_new_attempt_resets_observation_and_replaces_listener(self):
        session = self.session()
        for _ in range(5):
            session._begin_prestart_timing()
            self.assertIsNone(session._generation_network_observation)
            session._arm_generation_network_timing()
            self.assertEqual(len(session._page_cdp_session.listeners), 3)
            session.page.evaluate.return_value = self.raw()
            session._capture_event_loop_sentinel()
            self.assertEqual(session._page_cdp_session.listeners, [])
        self.assertEqual(session.page.evaluate.call_count, 5)

    def test_close_during_intentional_cancellation_uses_no_new_rpc(self):
        session = self.session()
        session._arm_generation_network_timing()
        cdp, calls = session._page_cdp_session, list(session._page_cdp_session.calls)
        session.close(intentional_cancellation=True)
        self.assertEqual(cdp.calls, calls)
        self.assertEqual(cdp.listeners, [])

    def test_formatter_redacts_corrupt_persisted_strings(self):
        _, cdp, diagnostic, recorder = self.setup_timing()
        cdp.emit(self.event())
        result = self.snapshot(diagnostic, recorder)
        result["candidates"][0].update(host="PRIVATE_ACCOUNT", path_pattern="/PRIVATE_PROMPT?token=PRIVATE_TOKEN",
            resource_type="PRIVATE_PROMPT", initiator_type="PRIVATE_TOKEN", identification_evidence="PRIVATE_COOKIE")
        result.update(conclusion_hint="PRIVATE_PROMPT", alignment_reason="PRIVATE_ACCOUNT", pointerdown_ms="PRIVATE_PROMPT")
        text = FlowSession._format_generation_network_timing({"generation_network_timing": result})
        self.assertNotIn("PRIVATE", text)
        self.assertIn("exact generation request: unresolved", text)

    def test_production_polls_download_and_recovery_paths_have_no_network_hooks(self):
        for method in (FlowSession._wait_for_generation_start, FlowSession.wait_for_result,
                       FlowSession.download_candidate, FlowSession._activate_generation_submit_button,
                       FlowSession._recover_unusual_activity_in_open_session):
            source = inspect.getsource(method)
            self.assertNotIn("generation_network", source)
            self.assertNotIn("Network.", source)


if __name__ == "__main__":
    unittest.main()
