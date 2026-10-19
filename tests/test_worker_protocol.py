"""Only synthetic bounded pipe frames; no runtime/browser data."""

import json
import unittest
from dataclasses import replace

from nano_batch.preview import PreviewRow, PreviewState, PreviewView
from nano_batch.progress import ProgressPhase, ProgressReporter
from nano_batch.worker_protocol import (
    MAX_FRAME, JobMode, JobRequest, ProtocolError, StopRequest, decode_message,
    decode_request, encode, message, preview_message, progress_message,
)
from nano_batch.worker_transport import MessageBuffer


class WorkerProtocolTests(unittest.TestCase):
    def test_ready_and_stop_round_trip(self):
        self.assertEqual(decode_message(encode(message("ready"))).kind, "ready")
        self.assertEqual(decode_request(encode(StopRequest())), StopRequest())

    def test_job_unicode_paths_are_private_inputs_not_diagnostics(self):
        request = JobRequest(JobMode.PREVIEW, "synthetic \u732b input", "synthetic run", "synthetic config")
        self.assertEqual(decode_request(encode(request)), request)
        self.assertNotIn("synthetic", repr(request))

    def test_wrong_versions_and_extra_fields_rejected(self):
        for data in ({"v": 2, "type": "ready"}, {"v": True, "type": "ready"},
                     {"v": 1, "type": "ready", "prompt": "synthetic"},
                     {"v": 1, "type": "unknown"}, {"v": 1, "type": []}):
            with self.subTest(data=data), self.assertRaises(ProtocolError):
                decode_message((json.dumps(data) + "\n").encode())

    def test_malformed_oversized_duplicate_keys_and_non_utf8_rejected(self):
        for frame in (b"{}", b"not json\n", b"\xff\n", b"[1]\n", b"null\n",
                      b'{"v":1,"v":1,"type":"ready"}\n', b" " * MAX_FRAME + b"\n",
                      b"[" * 1200 + b"]" * 1200 + b"\n"):
            with self.subTest(size=len(frame)), self.assertRaises(ProtocolError):
                decode_message(frame)

    def test_messages_cannot_be_used_as_requests(self):
        with self.assertRaises(ProtocolError):
            decode_request(encode(message("ready")))
        with self.assertRaises(ProtocolError):
            decode_message(encode(StopRequest()))

    def test_bad_jobs_fail_fixed_and_do_not_echo_private_text(self):
        base = {"v": 1, "type": "job", "mode": "execute", "prompt_file": "synthetic",
                "run_dir": "synthetic", "config_file": "synthetic"}
        for key, value in (("mode", []), ("mode", "unknown"), ("prompt_file", None),
                           ("run_dir", "\0synthetic"), ("config_file", "x" * 2049)):
            with self.subTest(key=key), self.assertRaises(ProtocolError) as caught:
                decode_request((json.dumps({**base, key: value}) + "\n").encode())
            self.assertNotIn("synthetic", str(caught.exception))

    def test_request_encoding_also_validates(self):
        request = JobRequest(JobMode.EXECUTE, "synthetic", "synthetic", "synthetic")
        for bad in (replace(request, mode="execute"), replace(request, run_dir=""),
                    replace(request, prompt_file="\u732b" * 2000)):
            with self.assertRaises(ProtocolError):
                encode(bad)

    def test_preview_counts_preserve_all_distinct_planner_states(self):
        view = PreviewView(tuple(PreviewRow(i, state, "fixed", "fixed", "fixed")
                                 for i, state in enumerate(PreviewState, 1)))
        result = decode_message(encode(preview_message(view))).as_dict()
        self.assertEqual(result["total"], 7)
        for name in ("pending", "completed", "download_only", "finalization_pending", "blocked", "waiting", "retry"):
            self.assertEqual(result[name], 1)

    def test_progress_is_fixed_numeric_projection_without_identifiers(self):
        reporter = ProgressReporter((1, 2))
        reporter.seed(((1, "completed"), (2, "pending")))
        reporter.record(ProgressPhase.DOWNLOAD, order=2, variant=1, code="synthetic private")
        value = progress_message(reporter.snapshot)
        result = decode_message(encode(value)).as_dict()
        self.assertEqual(result["percent"], 50)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["code"], "OPERATION_FAILED")
        self.assertNotIn("private", repr(result))

    def test_invalid_progress_types_percent_counts_and_codes_rejected(self):
        reporter = ProgressReporter((1,))
        base = progress_message(reporter.snapshot).as_dict()
        for key, value in (("total", True), ("sequence", -1), ("elapsed_ms", 2**60),
                           ("order", 0), ("phase", []), ("variant", 3), ("percent", 100),
                           ("completed", 2), ("code", "synthetic private"), ("verified", 1)):
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                decode_message((json.dumps({**base, key: value}) + "\n").encode())

    def test_all_terminal_outcomes_and_accurate_exit_codes(self):
        for outcome, code in (("completed", 0), ("preview_complete", 0), ("waiting", 3),
                              ("blocked", 2), ("failed", 2), ("interrupted", 130), ("communication_lost", 2)):
            value = message("terminal", outcome=outcome, exit_code=code, code=None, completed=1, total=1)
            self.assertEqual(decode_message(encode(value)), value)
            with self.assertRaises(ProtocolError):
                message("terminal", outcome=outcome, exit_code=99, code=None, completed=1, total=1)

    def test_terminal_cannot_claim_false_complete_or_private_error(self):
        for fields in ({"completed": 0}, {"completed": 2}, {"code": "synthetic private"},
                       {"code": "CHANNEL_LOST"}, {"outcome": []}):
            with self.assertRaises(ProtocolError):
                message("terminal", **{"outcome": "completed", "exit_code": 0, "code": None,
                                       "completed": 1, "total": 1, **fields})


class MessageBufferTests(unittest.TestCase):
    def test_progress_backpressure_retains_only_latest_snapshot(self):
        buffer = MessageBuffer()
        reporter = ProgressReporter((1,))
        for _ in range(10000):
            reporter.record(ProgressPhase.GENERATION, order=1)
            self.assertTrue(buffer.put(progress_message(reporter.snapshot)))
        self.assertEqual(buffer.coalesced, 9999)
        self.assertEqual(dict(buffer.take(0).fields)["sequence"], 10000)
        self.assertIsNone(buffer.take(0))

    def test_stop_acks_are_coalesced_and_terminal_is_reserved(self):
        buffer = MessageBuffer()
        for _ in range(100):
            self.assertTrue(buffer.put(message("stop_ack", status="duplicate")))
        self.assertTrue(buffer.put(message("terminal", outcome="interrupted", exit_code=130,
                                           code=None, completed=0, total=1)))
        self.assertFalse(buffer.put(message("ready")))
        self.assertEqual(buffer.take(0).kind, "stop_ack")
        self.assertEqual(buffer.take(0).kind, "terminal")
        self.assertIsNone(buffer.take(0))

    def test_control_overflow_is_explicit_not_unbounded(self):
        buffer = MessageBuffer()
        for _ in range(8):
            self.assertTrue(buffer.put(message("ready")))
        self.assertFalse(buffer.put(message("ready")))
        buffer.close()
        self.assertFalse(buffer.put(message("ready")))
