import hashlib
import inspect
import json
import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters import flow
from nano_batch.adapters.flow import FlowSession, _GenerationNetworkTiming, _PostStartAckTimingRecorder, _generation_network_endpoint_fingerprint
from nano_batch.cancellation import authoritative_cancellation_scope
from tests.test_flow_generation_network_timing import CDP, Clock


class EndpointFingerprintTests(unittest.TestCase):
    def fixture(self):
        clock, cdp = Clock(), CDP()
        diagnostic = _GenerationNetworkTiming(cdp, clock)
        diagnostic.arm()
        recorder = _PostStartAckTimingRecorder(clock, initial_composer_token=None, origin_source="offline")
        recorder._origin = 10.1
        recorder.set_browser_anchor(dict(browser_now_ms=900, python_midpoint=10, uncertainty_ms=1))
        raw = dict(pointerdown_at_ms=1000, read_at_ms=9200)
        return clock, cdp, diagnostic, recorder, raw

    @staticmethod
    def request(request_id="PRIVATE_REQUEST_ID", url="https://FLOW.google.com/PRIVATE_PATH?token=PRIVATE_TOKEN#PRIVATE_FRAGMENT", **updates):
        value = dict(requestId=request_id, timestamp=101.142, type="Fetch", request=dict(method="POST", url=url, hasPostData=True), initiator=dict(type="script"))
        value.update(updates)
        return value

    @staticmethod
    def response(request_id="PRIVATE_REQUEST_ID", **updates):
        value = dict(requestId=request_id, timestamp=101.340, response=dict(status=200, mimeType="application/json", url="PRIVATE_URL", headers={"Cookie": "PRIVATE_COOKIE"}))
        value.update(updates)
        return value

    @staticmethod
    def snapshot(diagnostic, recorder, raw):
        return diagnostic.snapshot(raw, recorder, {})

    def test_canonical_input_is_lowercase_hostname_newline_exact_path(self):
        expected = hashlib.sha256(b"flow.google.com\n/Case/opaque%2Fsegment").hexdigest()[:12]
        self.assertEqual(_generation_network_endpoint_fingerprint("https://FLOW.google.com:443/Case/opaque%2Fsegment"), expected)

    def test_same_endpoint_is_stable_across_calls_and_processes(self):
        url = "https://flow.google.com/stable/path"
        expected = _generation_network_endpoint_fingerprint(url)
        self.assertEqual(expected, _generation_network_endpoint_fingerprint(url))
        import sys
        result = subprocess.run([sys.executable, "-c", "from nano_batch.adapters.flow import _generation_network_endpoint_fingerprint as fp; print(fp('https://flow.google.com/stable/path'))"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), expected)

    def test_different_paths_hosts_case_and_dynamic_segments_are_not_collapsed(self):
        urls = ("https://a.test/path", "https://a.test/Path", "https://b.test/path", "https://a.test/path/id1", "https://a.test/path/id2")
        self.assertEqual(len({_generation_network_endpoint_fingerprint(url) for url in urls}), len(urls))

    def test_queries_fragments_userinfo_scheme_port_do_not_enter_hash(self):
        urls = ("https://flow.google.com/path?a=1#fragment", "https://flow.google.com/path?a=2#different", "http://user:secret@flow.google.com:9000/path")
        self.assertEqual(len({_generation_network_endpoint_fingerprint(url) for url in urls}), 1)

    def test_path_escaping_and_empty_path_are_preserved(self):
        self.assertNotEqual(_generation_network_endpoint_fingerprint("https://a.test/a%2Fb"), _generation_network_endpoint_fingerprint("https://a.test/a/b"))
        self.assertNotEqual(_generation_network_endpoint_fingerprint("https://a.test"), _generation_network_endpoint_fingerprint("https://a.test/"))

    def test_body_method_request_id_and_time_do_not_affect_fingerprint(self):
        _, cdp, diagnostic, _, _ = self.fixture()
        for body, request_id, stamp in (("PRIVATE_PROMPT_A", "id1", 101.1), ("PRIVATE_PROMPT_B", "id2", 102.2)):
            request = self.request(request_id=request_id, timestamp=stamp)
            request["request"]["postData"] = body
            cdp.emit(request)
        self.assertEqual(diagnostic.events[0]["endpoint_fp"], diagnostic.events[1]["endpoint_fp"])
        source = inspect.getsource(_generation_network_endpoint_fingerprint)
        self.assertNotIn("method", source)
        self.assertNotIn("timestamp", source)

    def test_correct_response_pairing_out_of_order_and_missing_request(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request("first", "https://flow.google.com/first"))
        cdp.emit(self.request("second", "https://flow.google.com/second"))
        cdp.emit(self.response("second", response=dict(status=202, mimeType="text/plain")), "Network.responseReceived")
        cdp.emit(self.response("unknown"), "Network.responseReceived")
        cdp.emit(self.response("first"), "Network.responseReceived")
        values = self.snapshot(diagnostic, recorder, raw)["candidates"]
        self.assertEqual([item["http_status"] for item in values], [200, 202])
        self.assertEqual([item["mime_type"] for item in values], ["application/json", "text/plain"])

    def test_browser_response_finish_and_python_receipts_are_separate(self):
        clock, cdp, diagnostic, recorder, raw = self.fixture()
        clock.now = 17.31
        cdp.emit(self.request())
        clock.now = 17.4
        cdp.emit(self.response(), "Network.responseReceived")
        clock.now = 17.45
        cdp.emit(dict(requestId="PRIVATE_REQUEST_ID", timestamp=101.351, encodedDataLength=1234), "Network.loadingFinished")
        value = self.snapshot(diagnostic, recorder, raw)["candidates"][0]
        self.assertEqual([value[key] for key in ("request_start_ms", "response_received_ms", "loading_finished_ms")], [142, 340, 351])
        self.assertEqual([value[key] for key in ("python_event_receive_ms", "python_response_event_receive_ms", "python_finish_event_receive_ms")], [7210, 7300, 7350])
        self.assertEqual(value["encoded_response_size"], 1234)

    def test_missing_response_metadata_stays_null(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request())
        value = self.snapshot(diagnostic, recorder, raw)["candidates"][0]
        for key in ("response_received_ms", "loading_finished_ms", "http_status", "mime_type", "encoded_response_size", "python_response_event_receive_ms"):
            self.assertIsNone(value[key])

    def test_unavailable_alignment_does_not_invent_response_times(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request())
        cdp.emit(self.response(), "Network.responseReceived")
        cdp.emit(dict(requestId="PRIVATE_REQUEST_ID", timestamp=101.351), "Network.loadingFinished")
        recorder.set_browser_anchor(None)
        value = self.snapshot(diagnostic, recorder, raw)["candidates"][0]
        self.assertEqual(value["http_status"], 200)
        for key in ("request_start_ms", "response_received_ms", "loading_finished_ms", "python_response_event_receive_ms"):
            self.assertIsNone(value[key])

    def test_sensitive_request_response_fields_are_never_read_or_output(self):
        class Sensitive(dict):
            def get(self, key, *args):
                if key in {"headers", "cookies", "postData", "postDataEntries", "body", "url"} and key != "url_allowed":
                    raise AssertionError("sensitive field read")
                return super().get(key, *args)
        _, cdp, diagnostic, recorder, raw = self.fixture()
        request = self.request()
        request["request"].update(postData="PRIVATE_PROMPT", headers={"Authorization": "PRIVATE_TOKEN"})
        cdp.emit(request)
        response = Sensitive(status=200, mimeType="application/json; account=PRIVATE_ACCOUNT", headers="PRIVATE_COOKIE", url="PRIVATE_URL", body="PRIVATE_PROMPT")
        cdp.emit(self.response(response=response), "Network.responseReceived")
        result = self.snapshot(diagnostic, recorder, raw)
        text = json.dumps(result) + FlowSession._format_generation_network_timing({"generation_network_timing": result})
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("path_pattern", json.dumps(diagnostic.events))
        self.assertNotIn("requestId", json.dumps(result))

    def test_unknown_or_sensitive_mime_is_unavailable(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request())
        cdp.emit(self.response(response=dict(status=200, mimeType="application/PRIVATE_ACCOUNT")), "Network.responseReceived")
        result = self.snapshot(diagnostic, recorder, raw)
        self.assertIsNone(result["candidates"][0]["mime_type"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_bounded_map_candidates_output_and_cleanup(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        for i in range(100):
            cdp.emit(self.request(str(i)))
        self.assertEqual(len(diagnostic.events), 32)
        self.assertEqual(len(diagnostic._requests), 32)
        result = self.snapshot(diagnostic, recorder, raw)
        self.assertEqual(len(result["candidates"]), 10)
        diagnostic.stop()
        self.assertEqual(diagnostic._requests, {})
        self.assertEqual(cdp.listeners, [])

    def test_evicted_candidate_response_is_not_paired(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        for i in range(40):
            cdp.emit(self.request(str(i)))
        cdp.emit(self.response("39"), "Network.responseReceived")
        self.assertTrue(all(item["http_status"] is None for item in diagnostic.events))

    def test_redirect_id_is_rebound_only_to_current_retained_hop(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request("shared", "https://flow.google.com/first"))
        cdp.emit(self.request("shared", "https://flow.google.com/second"))
        cdp.emit(self.response("shared"), "Network.responseReceived")
        values = self.snapshot(diagnostic, recorder, raw)["candidates"]
        self.assertEqual([item["http_status"] for item in values], [None, 200])

    def test_redirect_to_noncandidate_does_not_pair_with_old_post(self):
        _, cdp, diagnostic, _, _ = self.fixture()
        cdp.emit(self.request("shared"))
        cdp.emit(self.request("shared", request=dict(method="GET", url="https://flow.google.com/redirect")))
        cdp.emit(self.response("shared"), "Network.responseReceived")
        self.assertIsNone(diagnostic.events[0]["http_status"])

    def test_fingerprint_recurrence_cannot_promote_exact_request(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        for i in range(3):
            cdp.emit(self.request(str(i)))
        result = self.snapshot(diagnostic, recorder, raw)
        self.assertIsNone(result["exact_generation_request"])
        self.assertEqual(len({item["endpoint_fp"] for item in result["candidates"]}), 1)

    def test_disabled_diagnostic_adds_no_fingerprinting_or_response_listeners(self):
        session = FlowSession("https://flow.google.com/", Path("unused"), prestart_timing_debug=False)
        session._page_cdp_session = CDP()
        with patch.object(flow, "_generation_network_endpoint_fingerprint") as fingerprint:
            session._arm_generation_network_timing()
        fingerprint.assert_not_called()
        self.assertEqual(session._page_cdp_session.listeners, [])
        self.assertEqual(session._page_cdp_session.calls, [])

    def test_repeated_attempts_remove_all_three_listeners(self):
        cdp = CDP()
        for _ in range(5):
            diagnostic = _GenerationNetworkTiming(cdp)
            diagnostic.arm()
            self.assertEqual(len(cdp.listeners), 3)
            diagnostic.stop()
            self.assertEqual(cdp.listeners, [])

    def test_one_sigint_clears_ids_and_listeners_without_cleanup_rpc(self):
        session = FlowSession("https://flow.google.com/", Path("unused"), prestart_timing_debug=True)
        session._page_cdp_session = CDP()
        session.page = MagicMock()
        session._arm_generation_network_timing()
        session._page_cdp_session.emit(self.request())
        calls = list(session._page_cdp_session.calls)
        session.page.evaluate.side_effect = lambda *_: signal.raise_signal(signal.SIGINT)
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            session._capture_event_loop_sentinel()
        self.assertEqual(session._generation_network_timing._requests, {})
        self.assertEqual(session._page_cdp_session.listeners, [])
        self.assertEqual(session._page_cdp_session.calls, calls)

    def test_formatter_filters_corrupt_persisted_metadata_and_never_prints_path(self):
        _, cdp, diagnostic, recorder, raw = self.fixture()
        cdp.emit(self.request())
        result = self.snapshot(diagnostic, recorder, raw)
        result["candidates"][0].update(endpoint_fp="PRIVATE_TOKEN", mime_type="PRIVATE_ACCOUNT", path_pattern="/PRIVATE_PATH?PRIVATE_QUERY", host="PRIVATE_HOST")
        text = FlowSession._format_generation_network_timing({"generation_network_timing": result})
        self.assertNotIn("PRIVATE", text)
        self.assertIn("fp=-", text)
        self.assertIn("host=[other-host]", text)


if __name__ == "__main__":
    unittest.main()
