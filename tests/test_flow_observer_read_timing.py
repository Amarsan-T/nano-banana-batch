import inspect
import json
import shutil
import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowSession, FlowPolicyRejected, _OBSERVER_READ_STAGES,
    _PostStartAckTimingRecorder,
)
from nano_batch.cancellation import authoritative_cancellation_scope
from tests.test_flow_explicit_failures import (
    POLICY_FAILURE, _generation_failure_observer_scripts,
)


class ObserverReadTimingTests(unittest.TestCase):
    def browser_read(self, *, stage=None, enabled=True, count=1, historical=False,
                     partial=False, reused=False, broken=False):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for offline observer tests")
        begin, arm, poll = _generation_failure_observer_scripts()
        options = dict(stage=stage, enabled=enabled, count=count, historical=historical,
                       partial=partial, reused=reused, broken=broken)
        harness = r"""
const [begin, arm, poll, options, exact] = INPUT;
let now = 1000, clockReads = 0, charging = false, activeTiming = null;
const calls = {};
const cost = (name) => {
  if (charging) {
    calls[name] = (calls[name] || 0) + 1;
    if (name === options.stage) now += 7000;
  }
};
class Element {
  constructor(parent = null) {
    this.parentElement = parent; this.text = ''; this.children = [];
    if (parent) parent.children.push(this);
  }
  get isConnected() { cost('container_connectivity'); return true; }
  get innerText() {
    cost('inner_text_read');
    if (charging && options.broken) throw new Error('original getter failure');
    return this.text;
  }
  get textContent() { cost('text_content_read'); return this.text; }
  getAttribute() { cost('aria_label_read'); return null; }
  getBoundingClientRect() { cost('geometry_read'); return {width: 100, height: 20}; }
  matches() { return true; }
  querySelectorAll() { return []; }
}
class MutationObserver {
  constructor(callback) { this.callback = callback; }
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
}
globalThis.Element = Element;
globalThis.MutationObserver = MutationObserver;
globalThis.getComputedStyle = () => { cost('style_read'); return {visibility: 'visible', display: 'block'}; };
globalThis.performance = {now: () => { clockReads++; return now; }};
globalThis.window = {};
globalThis.document = {body: new Element(), createTreeWalker(root) {
  const nodes = [];
  const visit = element => {
    for (const child of element.children) { nodes.push(child); visit(child); }
  };
  visit(root);
  return {nextNode: () => nodes.shift() || null};
}};
const exec = (script, ...args) => (0, eval)('(' + script + ')')(...args);
exec(begin, true);
const state = window.__nanoBatchCurrentGenerationAttempt;
const setter = state.setObserverReadTiming;
state.setObserverReadTiming = (value) => { activeTiming = value; setter(value); };
const cards = Array.from({length: options.count}, () => new Element(document.body));
if (options.historical) for (const card of cards) card.text = exact;
exec(arm);
if (!options.historical) {
  for (const card of cards) {
    card.text = options.partial ? 'This generation might violate our policies.' : exact;
    // Pre-mounted and newly mounted cards both use the existing post-Start gate.
    state.observer.callback([options.reused
      ? {type: 'characterData', target: {parentElement: card, textContent: card.text}, oldValue: ''}
      : {type: 'childList', target: document.body, addedNodes: [card]}]);
  }
}
if (options.stage === 'retained_container_scan') {
  const scan = state.scanFailureContainers;
  state.scanFailureContainers = () => { now += 7000; scan(); };
}
charging = true; clockReads = 0;
let result = null, error = null;
try { result = exec(poll, options.enabled); }
catch (caught) { error = caught.message; }
const measuredClockReads = clockReads;
charging = false;
const callbacksBefore = state.callbackCount;
state.observer.callback([{type: 'characterData', target: {parentElement: cards[0]}}]);
console.log(JSON.stringify({result, error, calls, clockReads: measuredClockReads,
  timingCleared: activeTiming === null, callbacksBefore, callbacksAfter: state.callbackCount}));
""".replace("INPUT", json.dumps([begin, arm, poll, options, POLICY_FAILURE]))
        completed = subprocess.run([node, "-"], input=harness, text=True,
                                   encoding="utf-8", capture_output=True, timeout=10, check=True)
        return json.loads(completed.stdout)

    @staticmethod
    def raw_timing(**updates):
        raw = dict(total_browser_js_ms=7390, evaluate_enter_browser_ms=1010,
                   evaluate_exit_browser_ms=8400, containers_scanned=123,
                   retained_strong_container_count=123, mutation_record_count=1124,
                   container_sample_count=123, max_container_ms=7000,
                   p50_container_ms=2, p95_container_ms=12, slowest_container_index=7,
                   slowest_container_stage="inner_text_read",
                   stages={"inner_text_read": dict(calls=123, total_ms=7000, max_ms=7000, self_ms=7000)})
        raw.update(updates)
        return raw

    def recorder(self, raw=None, anchor=True):
        recorder = _PostStartAckTimingRecorder(lambda: 10, initial_composer_token=None, origin_source="test")
        recorder.set_counts("pre_click", dict(dom_node_count=8421, candidate_tile_count=123, history_item_count=123))
        if anchor:
            recorder.set_browser_anchor(dict(browser_now_ms=1000, python_midpoint=10, uncertainty_ms=2))
        recorder.record_observer_internal(raw or self.raw_timing(), 10.005, 17.450)
        return recorder

    def test_slow_internal_text_extraction_is_identified(self):
        result = self.browser_read(stage="inner_text_read")
        raw = result["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["stages"]["inner_text_read"]["max_ms"], 7000)
        read = self.recorder(raw).observer_read_snapshot()["slowest_reads"][0]
        self.assertEqual(read["dominant_internal_stage"], "inner_text_read")
        self.assertEqual(raw["slowest_container_stage"], "inner_text_read")

    def test_slow_geometry_is_identified(self):
        raw = self.browser_read(stage="geometry_read")["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["stages"]["geometry_read"]["calls"], 2)
        self.assertEqual(raw["total_browser_js_ms"], 14000)
        self.assertEqual(self.recorder(raw).observer_read_snapshot()["slowest_reads"][0]["dominant_internal_stage"], "geometry_read")

    def test_slow_retained_scan_self_work_is_identified(self):
        raw = self.browser_read(stage="retained_container_scan")["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["stages"]["retained_container_scan"]["self_ms"], 7000)
        self.assertEqual(self.recorder(raw).observer_read_snapshot()["slowest_reads"][0]["dominant_internal_stage"], "retained_container_scan")

    def test_text_and_style_accessors_are_separately_measured(self):
        raw = self.browser_read()["result"]["diagnostics"]["observer_read_internal_timing"]
        for name in ("aria_label_read", "inner_text_read", "text_content_read", "normalization",
                     "style_read", "geometry_read", "visibility_test", "signature_match", "failure_record"):
            self.assertIn(name, raw["stages"])

    def test_wall_browser_and_residual_gap_are_reported_separately(self):
        read = self.recorder().observer_read_snapshot()["slowest_reads"][0]
        self.assertEqual(read["wall_ms"], 7445)
        self.assertEqual(read["total_browser_js_ms"], 7390)
        self.assertEqual(read["transport_queue_serialization_gap_ms"], 55)
        self.assertEqual(read["estimated_request_to_browser_enter_ms"], 5)
        self.assertEqual(read["estimated_browser_exit_to_python_ms"], 50)
        self.assertEqual(read["clock_alignment_uncertainty_ms"], 2)
        self.assertIsNone(read["queue_start_ms"])
        self.assertIsNone(read["protocol_serialization_ms"])

    def test_queue_dominated_gap_does_not_get_attributed_to_browser_js(self):
        read = self.recorder(self.raw_timing(total_browser_js_ms=5,
            evaluate_enter_browser_ms=8400, evaluate_exit_browser_ms=8405)).observer_read_snapshot()["slowest_reads"][0]
        self.assertEqual(read["transport_queue_serialization_gap_ms"], 7440)
        self.assertEqual(read["estimated_request_to_browser_enter_ms"], 7395)
        self.assertEqual(read["estimated_browser_exit_to_python_ms"], 45)

    def test_unavailable_anchor_leaves_queue_start_null(self):
        read = self.recorder(anchor=False).observer_read_snapshot()["slowest_reads"][0]
        self.assertIsNone(read["estimated_request_to_browser_enter_ms"])
        self.assertIsNone(read["queue_start_ms"])
        self.assertEqual(read["queue_timing_unavailable_reason"], "clock_anchor_unavailable")

    def test_inconsistent_clocks_and_negative_gap_are_not_invented(self):
        read = self.recorder(self.raw_timing(total_browser_js_ms=10000,
            evaluate_enter_browser_ms=999)).observer_read_snapshot()["slowest_reads"][0]
        self.assertIsNone(read["transport_queue_serialization_gap_ms"])
        self.assertIsNone(read["estimated_request_to_browser_enter_ms"])
        self.assertEqual(read["queue_timing_unavailable_reason"], "clock_alignment_inconsistent")

    def test_container_samples_are_bounded_without_reducing_scan_limit(self):
        raw = self.browser_read(count=250)["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["containers_scanned"], 200)
        self.assertEqual(raw["retained_strong_container_count"], 200)
        self.assertEqual(raw["container_sample_count"], 128)
        self.assertIsNotNone(raw["p95_container_ms"])
        self.assertNotIn("samples", raw)
        self.assertLess(len(json.dumps(raw)), 2500)

    def test_one_container_has_no_unjustified_p95(self):
        raw = self.browser_read()["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["containers_scanned"], 1)
        self.assertIsNone(raw["p95_container_ms"])

    def test_only_five_slowest_reads_retained_with_fixed_stage_names(self):
        recorder = self.recorder()
        for index in range(500):
            recorder.record_observer_internal(self.raw_timing(), 10, 18 + index)
        summary = recorder.observer_read_snapshot()
        self.assertEqual(summary["total_reads"], 501)
        self.assertEqual(len(summary["slowest_reads"]), 5)
        self.assertLess(len(json.dumps(summary)), 12000)

    def test_private_dom_text_labels_and_prompt_are_never_emitted(self):
        private = "private prompt secret@example.com cookie=TOKEN_SENTINEL_abcdef"
        raw = self.raw_timing(prompt=private, innerText=private, aria_label=private,
                              slowest_container_stage=private, slowest_container_index=private)
        raw["stages"][private] = dict(max_ms=7000, self_ms=7000)
        recorder = self.recorder(raw)
        summary = recorder.snapshot()
        output = json.dumps(summary) + FlowSession._format_observer_read_breakdown(summary)
        for word in ("private", "example.com", "cookie", "TOKEN_SENTINEL_abcdef"):
            self.assertNotIn(word, output)
        self.assertIsNone(summary["observer_read_breakdown"]["slowest_reads"][0]["slowest_container_index"])

    def test_existing_payload_and_dom_operations_are_identical_on_off(self):
        off, on = self.browser_read(enabled=False), self.browser_read(enabled=True)
        on["result"]["diagnostics"].pop("observer_read_internal_timing")
        self.assertEqual(on["result"], off["result"])
        self.assertEqual(on["calls"], off["calls"])
        self.assertEqual(off["clockReads"], 0)
        self.assertTrue(on["timingCleared"])
        self.assertEqual(on["callbacksAfter"], on["callbacksBefore"] + 1)

    def test_historical_card_stays_ignored_and_current_reused_card_is_eligible(self):
        historical = self.browser_read(historical=True)["result"]
        self.assertEqual(historical["failures"], [])
        self.assertEqual(historical["diagnostics"]["observer_read_internal_timing"]["containers_scanned"], 0)
        current = self.browser_read(reused=True)["result"]
        self.assertEqual(len(current["failures"]), 1)
        self.assertEqual(current["diagnostics"]["last_failure_detection_reason"], "exact_policy_rejection_signature")

    def test_partial_failure_still_does_not_match(self):
        self.assertEqual(self.browser_read(partial=True)["result"]["failures"], [])

    def test_original_getter_error_unwinds_and_clears_timing_scope(self):
        result = self.browser_read(broken=True)
        self.assertEqual(result["error"], "original getter failure")
        self.assertTrue(result["timingCleared"])

    def test_python_rpc_span_is_persisted_and_classifier_unchanged(self):
        session = FlowSession("https://flow.google.com", Path("unused"), prestart_timing_debug=True)
        session.page = MagicMock()
        raw = self.browser_read()["result"]
        clock = [10.005]
        def evaluate(*_args):
            clock[0] += 7.445
            return raw
        session.page.evaluate.side_effect = evaluate
        with patch("nano_batch.adapters.flow.time.perf_counter", side_effect=lambda: clock[0]):
            session._begin_post_start_ack_timing(origin_source="test")
            with self.assertRaises(FlowPolicyRejected):
                session._raise_for_current_generation_attempt_failure()
        read = session._post_start_ack_timing_snapshot()["observer_read_breakdown"]["slowest_reads"][0]
        self.assertEqual(read["wall_ms"], 7445)
        session.page.evaluate.assert_called_once()
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()

    def test_dom_size_association_reuses_snapshots_without_new_queries(self):
        read = self.recorder().observer_read_snapshot()["slowest_reads"][0]
        self.assertEqual(read["dom_counts"], dict(dom_node_count=8421, candidate_tile_count=123, history_item_count=123))
        self.assertEqual(read["dom_count_snapshot_scope"], "pre_click_reused")
        _, _, script = _generation_failure_observer_scripts()
        for operation in ("querySelectorAll", "createTreeWalker", "getBoundingClientRect", "innerText"):
            self.assertNotIn(operation, script)

    def test_terminal_block_is_fixed_bounded_and_separate_from_ack(self):
        timing = self.recorder().snapshot()
        timing["observer_read_breakdown"]["internal_stage_max_ms"]["private selector"] = "secret"
        output = FlowSession._format_observer_read_breakdown(timing)
        self.assertIn("OBSERVER READ BREAKDOWN", output)
        self.assertIn("7445", output)
        self.assertIn("inner_text_read", output)
        self.assertIn("not separable", output)
        self.assertLess(len(output.splitlines()), 30)
        self.assertLess(len(output), 2000)
        self.assertNotIn("secret", output)
        self.assertIn("wall: -", FlowSession._format_observer_read_breakdown({}))

    def test_one_sigint_is_authoritative_and_sends_no_followup_rpc(self):
        session = FlowSession("https://flow.google.com", Path("unused"), prestart_timing_debug=True)
        session.page = MagicMock()
        def interrupted(*_args):
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        session.page.evaluate.side_effect = interrupted
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            session._wait_for_generation_start(prompt_input=MagicMock(), intended_prompt="private",
                baseline_fingerprints=set(), baseline_progress=set(), timeout_ms=5000)
        session.page.evaluate.assert_called_once()
        session.page.mouse.down.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_missing_or_disabled_metrics_are_safe_and_add_no_waits(self):
        recorder = self.recorder()
        recorder.record_observer_internal(None, 10, 11)
        self.assertEqual(recorder.observer_read_snapshot()["total_reads"], 1)
        for method in (FlowSession._current_generation_attempt_failures,
                       _PostStartAckTimingRecorder.record_observer_internal):
            source = inspect.getsource(method)
            self.assertNotIn("sleep(", source)
            self.assertNotIn("wait_for_timeout(", source)
        off = self.browser_read(enabled=False)
        self.assertNotIn("observer_read_internal_timing", off["result"]["diagnostics"])
        self.assertEqual(off["clockReads"], 0)


if __name__ == "__main__":
    unittest.main()
