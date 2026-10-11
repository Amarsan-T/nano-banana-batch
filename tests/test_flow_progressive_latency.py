import inspect
import json
from contextlib import redirect_stdout
from io import StringIO
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app

from nano_batch.adapters.flow import (
    FlowSession, _LATENCY_COUNT_FIELDS, _LATENCY_SAMPLE_LIMIT,
    _PostStartAckTimingRecorder, _latency_counts,
)
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.models import Batch, BatchItem


class ProgressiveLatencyDiagnosticsTests(unittest.TestCase):
    def session(self, debug=True):
        session = FlowSession("https://flow.google.com", Path("unused"),
                              prestart_timing_debug=debug)
        session.page = MagicMock()
        return session

    def run_js(self, script):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for offline embedded-JS tests")
        result = subprocess.run([node, "-"], input=script, text=True,
                                capture_output=True, timeout=10, check=True)
        return json.loads(result.stdout)

    def observer_scripts(self):
        session = self.session()
        session._begin_current_generation_attempt_observation()
        begin = session.page.evaluate.call_args.args[0]
        session._arm_current_generation_attempt_observation()
        arm = session.page.evaluate.call_args.args[0]
        session._current_generation_attempt_failures()
        poll = session.page.evaluate.call_args.args[0]
        session._stop_current_generation_attempt_observation()
        stop = session.page.evaluate.call_args.args[0]
        return dict(begin=begin, arm=arm, poll=poll, stop=stop)

    def lifecycle(self, leak=False, stop=True):
        scripts = json.dumps(self.observer_scripts())
        return self.run_js(f"""
const scripts = {scripts};
class Element {{
  constructor(parent=null) {{
    this.parentElement=parent; this.isConnected=true;
    this.innerText=''; this.textContent='';
  }}
  getAttribute() {{return null;}}
  matches() {{return false;}}
  querySelectorAll() {{return [];}}
  getBoundingClientRect() {{return {{width:100,height:20}};}}
}}
const observers = new Set();
class MutationObserver {{
  constructor(callback) {{this.callback=callback;}}
  observe() {{observers.add(this);}}
  disconnect() {{observers.delete(this);}}
  takeRecords() {{return [];}}
}}
globalThis.Element=Element;
globalThis.MutationObserver=MutationObserver;
globalThis.window={{}};
globalThis.document={{body:new Element(),
  createTreeWalker:()=>({{nextNode:()=>null}})}};
globalThis.getComputedStyle=()=>({{display:'block',visibility:'visible'}});
let now=1000; globalThis.performance={{now:()=>++now}};
const exec=(key,...args)=>(0,eval)('('+scripts[key]+')')(...args);
const samples=[];
for(let order=1;order<=15;order++) {{
  exec('begin',true); exec('arm');
  const state=window.__nanoBatchCurrentGenerationAttempt;
  const clean=exec('poll');
  state.observer.callback([{{type:'characterData',target:{{parentElement:new Element(document.body)}}}}]);
  const observed=exec('poll');
  samples.push({{clean:clean.diagnostics,observed:observed.diagnostics,
    actualActive:observers.size}});
  if ({json.dumps(leak)}) delete window.__nanoBatchCurrentGenerationAttempt;
  else if ({json.dumps(stop)}) {{exec('stop');exec('stop');}}
}}
console.log(JSON.stringify({{samples,metrics:window.__nanoBatchGenerationObserverMetricsV1}}));
""")

    def test_repeated_orders_keep_one_observer_and_reset_per_attempt(self):
        result = self.lifecycle()
        for sample in result["samples"]:
            self.assertEqual(sample["actualActive"], 1)
            self.assertEqual(sample["clean"]["generation_dom_mutation_count"], 0)
            self.assertEqual(sample["clean"]["observer_callback_count"], 0)
            self.assertIsNone(sample["clean"]["first_mutation_at_ms"])
            self.assertEqual(sample["observed"]["generation_dom_mutation_count"], 1)
            self.assertEqual(sample["observed"]["observer_callback_count"], 1)
            self.assertEqual(sample["observed"]["active_observer_count"], 1)
            self.assertGreater(sample["observed"]["observer_callback_total_ms"], 0)
        self.assertEqual(result["metrics"],
                         dict(active=0, installed=15, disconnected=15, peak=1))

    def test_deliberately_lost_cleanup_exposes_growing_observer_count(self):
        result = self.lifecycle(leak=True)
        self.assertEqual([s["observed"]["active_observer_count"] for s in result["samples"]],
                         list(range(1, 16)))
        self.assertEqual([s["actualActive"] for s in result["samples"]], list(range(1, 16)))

    def test_begin_without_stop_disconnects_previous_observer(self):
        result = self.lifecycle(stop=False)
        self.assertTrue(all(s["observed"]["active_observer_count"] == 1
                            for s in result["samples"]))
        self.assertEqual(result["metrics"],
                         dict(active=1, installed=15, disconnected=14, peak=1))
        scripts = self.observer_scripts()
        self.assertIn("prior.observer.disconnect()", scripts["begin"])
        self.assertIn("state.observer.disconnect()", scripts["stop"])
        # No registry of actual observers/DOM nodes or diagnostic observer added.
        self.assertEqual(scripts["begin"].count("new MutationObserver"), 1)
        self.assertNotIn("metrics.observers", scripts["begin"])

    def test_diagnostics_and_samples_reset_on_next_attempt(self):
        session = self.session()
        session._begin_prestart_timing()
        session._latency_pre_click_counts = {"dom_node_count": 100}
        session._begin_post_start_ack_timing(origin_source="mouse_down_boundary")
        session._post_start_ack_timing.record_latency("cheap_round_trip", 12)
        session._begin_prestart_timing()
        session._begin_post_start_ack_timing(origin_source="mouse_down_boundary")
        timing = session._post_start_ack_timing_snapshot()
        self.assertIsNone(timing["pre_click_dom_node_count"])
        self.assertEqual(timing["check_latency_ms"], {})

    def test_metrics_are_bounded_and_privacy_filtered(self):
        recorder = _PostStartAckTimingRecorder(lambda: 0, initial_composer_token=None,
                                               origin_source="test")
        for index in range(2000):
            recorder.record_latency("observer_read", index)
            recorder.record_latency("private prompt email token", index)
        recorder.set_counts("post_ack", {
            "dom_node_count": 1e100, "prompt": "secret", "email": "private@example.com",
            "body_text": "private page", "body_text_length": 50,
        })
        recorder.observe_browser_metrics({"active_observer_count": 2, "page_text": "secret"})
        result = recorder.snapshot()
        self.assertEqual(result["check_latency_ms"]["observer_read"]["samples"], _LATENCY_SAMPLE_LIMIT)
        self.assertEqual(result["post_ack_dom_node_count"], 1_000_000_000)
        self.assertEqual(len(result["dom_counts"]["post_ack"]), len(_LATENCY_COUNT_FIELDS))
        rendered = json.dumps(result)
        for text in ("secret", "private@example.com", "private page", "private prompt"):
            self.assertNotIn(text, rendered)
        self.assertLess(len(rendered), 6000)

    def test_unavailable_nonfinite_metrics_are_optional(self):
        counts = _latency_counts({"dom_node_count": float("nan"), "button_count": True,
                                  "body_text_length": "private"})
        self.assertTrue(all(v is None for v in counts.values()))
        session = self.session()
        session.page.evaluate.side_effect = RuntimeError("unavailable secret")
        session._capture_latency_counts("pre_click")
        session._begin_post_start_ack_timing(origin_source="test")
        session._capture_latency_counts("post_ack")
        self.assertIsNone(session._post_start_ack_timing_snapshot()["post_ack_dom_node_count"])

    def test_count_snapshot_returns_counts_only_even_with_private_dom_text(self):
        session = self.session()
        session._capture_latency_counts("pre_click")
        script = session.page.evaluate.call_args_list[-1].args[0]
        result = self.run_js(f"""
const snapshot={script};
const body={{innerText:'private prompt private@example.com cookie=secret'}};
globalThis.window={{}};
globalThis.NodeFilter={{SHOW_ALL:1}};
globalThis.performance={{now:()=>42}};
globalThis.document={{body,scrollingElement:{{scrollHeight:300}},
  querySelectorAll:(selector)=>Array(selector==='*'?10:0),
  createTreeWalker:()=>{{let count=0;return {{nextNode:()=>++count<=20}};}}}};
console.log(JSON.stringify(snapshot()));
""")
        self.assertEqual(result["dom_node_count"], 20)
        self.assertEqual(result["element_count"], 10)
        self.assertEqual(result["body_text_length"], 48)
        self.assertTrue(all(v is None or isinstance(v, (int, float)) for v in result.values()))
        self.assertNotIn("private", json.dumps(result))

    def test_only_two_snapshots_no_new_sleep_or_poll_when_debug_off(self):
        session = self.session(debug=False)
        session._capture_latency_counts("pre_click")
        session._capture_latency_counts("post_ack")
        session.page.evaluate.assert_not_called()
        session.prestart_timing_debug = True
        session._capture_latency_counts("pre_click")
        session._begin_post_start_ack_timing(origin_source="test")
        session._capture_latency_counts("post_ack")
        self.assertEqual(session.page.evaluate.call_count, 4)
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()
        session.page.keyboard.press.assert_not_called()
        for method in (FlowSession._capture_latency_counts,
                       _PostStartAckTimingRecorder.observe_browser_metrics):
            source = inspect.getsource(method)
            self.assertNotIn("sleep(", source)
            self.assertNotIn("wait_for_timeout(", source)

    def test_ctrl_c_preempts_diagnostic_calls_and_translated_interrupt(self):
        session = self.session()
        with authoritative_cancellation_scope() as state:
            state.request()
            with self.assertRaises(KeyboardInterrupt):
                session._capture_latency_counts("pre_click")
        session.page.evaluate.assert_not_called()
        with authoritative_cancellation_scope() as state:
            def interrupted(*_args):
                state.request()
                raise RuntimeError("transport")
            session.page.evaluate.side_effect = interrupted
            with self.assertRaises(KeyboardInterrupt):
                session._capture_latency_counts("post_ack")
        self.assertEqual(session.page.evaluate.call_count, 1)

    def test_callback_clock_mapping_and_first_reaction_timeline(self):
        recorder = _PostStartAckTimingRecorder(lambda: 10, initial_composer_token=None,
                                               origin_source="test")
        recorder.set_browser_anchor(dict(browser_now_ms=1000, python_midpoint=9.995,
                                        uncertainty_ms=10))
        recorder.observe_browser_metrics(dict(first_mutation_at_ms=1250,
                                              observer_callback_total_ms=7))
        recorder._mark_first_signals({"progress_signal_count": 1}, 300)
        recorder._mark_first_signals({"candidate_count": 1}, 400)
        recorder._mark_first_signals({"start_button_enabled": False}, 500)
        result = recorder.snapshot()
        self.assertEqual(result["first_mutation_callback_ms"], 245)
        self.assertEqual(result["first_any_browser_observed_change_ms"], 245)
        self.assertEqual(result["first_progress_change_ms"], 300)
        self.assertEqual(result["first_candidate_change_ms"], 400)
        self.assertEqual(result["first_start_state_change_ms"], 500)
        self.assertEqual(result["browser_clock_anchor_uncertainty_ms"], 10)

    def test_existing_ack_checks_and_order_are_unchanged(self):
        for debug in (False, True):
            session = self.session(debug=debug)
            with patch.object(session, "_raise_for_current_generation_attempt_failure"), \
                 patch.object(session, "_progress_signals", return_value=("Generating",)), \
                 patch.object(session, "discover_candidates") as candidates:
                result = session._wait_for_generation_start(
                    prompt_input=MagicMock(), intended_prompt="private", baseline_fingerprints=set(),
                    baseline_progress=set(), timeout_ms=5000)
            self.assertEqual(result, {"signal": "new_generation_progress"})
            candidates.assert_not_called()
            session.page.wait_for_timeout.assert_not_called()
            session.page.evaluate.assert_not_called()
            timing = session._post_start_ack_timing_snapshot()
            self.assertEqual(timing["configured_timeout_ms"], 5000)
            self.assertEqual(timing["configured_poll_interval_ms"], 100)
            self.assertEqual(set(timing["check_latency_ms"]),
                             {"explicit_failure_check", "progress_check"})

    def test_summary_is_one_bounded_prompt_free_line_with_nulls(self):
        line = FlowSession._format_order_latency_summary(14, {
            "ack_ms": 6392, "first_mutation_callback_ms": 6106,
            "post_ack_dom_node_count": 8421, "candidate_tile_count": 14,
            "history_item_count": 14, "active_observer_count": 1,
            "check_latency_ms": {"cheap_round_trip": {"p50": 8, "max": 22}},
            "prompt": "secret",
        })
        self.assertIn("order=14", line)
        self.assertIn("6106", line)
        self.assertIn("8/22", line)
        self.assertNotIn("secret", line)
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 400)
        self.assertIn("click->ack=-", FlowSession._format_order_latency_summary(1, {}))

    def test_completed_order_callback_prints_once_and_never_replays_skipped_orders(self):
        batch = Batch("offline-latency", "offline", tuple(
            BatchItem(order, f"item_{order}", "private") for order in (1, 2, 3)))
        plan = SimpleNamespace(run_dir=Path("unused-offline-run"), orders=batch.items)
        outcomes = [SimpleNamespace(order=i, generation=gen, downloads="downloaded")
                    for i, gen in ((1, "generated"), (2, "generated"), (3, "skipped_completed"))]
        state = SimpleNamespace(items=[SimpleNamespace(order=i, result_details=json.dumps({
            "post_start_ack_timing": {"ack_ms": i * 1000}, "prompt": "secret"}))
            for i in (1, 2, 3)])
        args = SimpleNamespace(run_dir="unused-offline-run", config="unused", timeout=None,
                               retry=False, preview=False)
        for enabled in (False, True):
            def execute(*_args, **kwargs):
                for outcome in outcomes:
                    kwargs["order_completed"](outcome)
                return state, outcomes
            with patch.object(app, "_load_config", return_value={"prestart_timing_debug": enabled}), \
                 patch.object(app, "plan_sequential_batch", return_value=plan), \
                 patch.object(app, "_print_sequential_batch_plan"), \
                 patch.object(app, "_flow_session_from_args", return_value=object()), \
                 patch.object(app, "execute_sequential_batch", side_effect=execute) as executor, \
                 patch.object(app, "RunStateStore") as store, \
                 patch.object(app, "summarize_state", return_value="done"), \
                 redirect_stdout(StringIO()) as output:
                store.return_value.load.return_value = state
                self.assertEqual(app._run_sequential_command(batch, args), 0)
            text = output.getvalue()
            self.assertEqual(text.count("ORDER LATENCY SUMMARY"), 2 if enabled else 0)
            self.assertEqual(text.count("ACK POLL BREAKDOWN"), 2 if enabled else 0)
            self.assertEqual(text.count("OBSERVER READ BREAKDOWN"), 2 if enabled else 0)
            self.assertEqual(text.count("EVENT LOOP VS CDP"), 2 if enabled else 0)
            self.assertEqual(text.count("GENERATION NETWORK TIMING"), 2 if enabled else 0)
            if enabled:
                self.assertLess(text.index("ACK POLL BREAKDOWN"), text.index("OBSERVER READ BREAKDOWN"))
                self.assertLess(text.index("OBSERVER READ BREAKDOWN"), text.index("EVENT LOOP VS CDP"))
                self.assertLess(text.index("EVENT LOOP VS CDP"), text.index("GENERATION NETWORK TIMING"))
            self.assertEqual(executor.call_count, 1)
            self.assertNotIn("secret", text)
            self.assertLess(text.index("Order 1: COMPLETE"),
                            text.index("ORDER LATENCY SUMMARY") if enabled else len(text))

    def test_post_ack_snapshot_sees_callback_even_when_progress_short_circuited_poll(self):
        session = self.session()
        session._begin_post_start_ack_timing(origin_source="test")
        session.page.evaluate.side_effect = [1, dict(
            active_observer_count=1, observer_callback_count=2,
            observer_callback_total_ms=12, observer_callback_max_ms=8)]
        session._capture_latency_counts("post_ack")
        result = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["active_observer_count"], 1)
        self.assertEqual(result["observer_metrics"]["observer_callback_total_ms"], 12)
        self.assertEqual(result["observer_metrics"]["observer_callback_max_ms"], 8)


if __name__ == "__main__":
    unittest.main()
