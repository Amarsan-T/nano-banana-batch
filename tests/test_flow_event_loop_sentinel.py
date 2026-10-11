import inspect
import json
import shutil
import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import FlowAutomationError, FlowSession
from nano_batch.cancellation import authoritative_cancellation_scope
from tests.test_flow_explicit_failures import _generation_failure_observer_scripts


class EventLoopSentinelTests(unittest.TestCase):
    def session(self, enabled=True):
        session = FlowSession("https://flow.google.com", Path("unused"), prestart_timing_debug=enabled)
        session.page = MagicMock()
        return session

    def browser(self, *, enabled=True, supported=True, delayed=False, cleanup=False,
                replacement=False, entries=1, gesture=True, incomplete=False, expire=False, observe_fails=False, task_mix=False):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for offline sentinel tests")
        begin, arm, _ = _generation_failure_observer_scripts()
        session = self.session()
        session._capture_event_loop_sentinel()
        read = session.page.evaluate.call_args.args[0]
        session._stop_current_generation_attempt_observation()
        stop = session.page.evaluate.call_args.args[0]
        options = dict(enabled=enabled, supported=supported, delayed=delayed, cleanup=cleanup,
                       replacement=replacement, entries=entries, gesture=gesture, incomplete=incomplete,
                       expire=expire, observe_fails=observe_fails, task_mix=task_mix)
        harness = r"""
const [begin, arm, read, stop, options] = INPUT;
(async () => {
  let now = 100, timerCalls = 0, zeroTimers = 0, expiryTimers = 0, rafCalls = 0, prevented = 0;
  const listeners = new Map(), timers = new Map(), frames = new Map(), micros = [];
  const longObservers = [];
  class Element { querySelectorAll() { return []; } }
  class MutationObserver {
    constructor(callback) { this.callback = callback; }
    observe() {}
    disconnect() {}
    takeRecords() { return []; }
  }
  class PerformanceObserver {
    static supportedEntryTypes = options.supported ? ['longtask'] : [];
    constructor(callback) { this.callback = callback; this.disconnected = false; longObservers.push(this); }
    observe() { if (options.observe_fails) throw new Error('PRIVATE_OBSERVER_ERROR'); }
    takeRecords() { return []; }
    disconnect() { this.disconnected = true; }
  }
  globalThis.window = {};
  globalThis.Element = Element;
  globalThis.MutationObserver = MutationObserver;
  globalThis.PerformanceObserver = PerformanceObserver;
  globalThis.performance = {now: () => now};
  globalThis.document = {body: new Element(),
    createTreeWalker() { return {nextNode: () => null}; },
    addEventListener(type, handler, settings) {
      if (!settings.capture || !settings.passive) throw new Error('nonpassive listener');
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
  };
  globalThis.queueMicrotask = (callback) => micros.push(callback);
  globalThis.setTimeout = (callback, delay) => {
    if (delay === 0) zeroTimers++;
    else if (delay === 30000) expiryTimers++;
    else throw new Error('unexpected timeout');
    timers.set(++timerCalls, {callback, delay}); return timerCalls;
  };
  globalThis.clearTimeout = (id) => timers.delete(id);
  globalThis.requestAnimationFrame = (callback) => { frames.set(++rafCalls, callback); return rafCalls; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  const exec = (script, ...args) => (0, eval)('(' + script + ')')(...args);
  const event = (type, trusted = true) => {
    for (const handler of [...(listeners.get(type) || [])]) handler({isTrusted: trusted, button: 0,
      preventDefault() { prevented++; }, stopPropagation() { prevented++; }, target: {secret: 'PRIVATE_PROMPT'}});
  };
  exec(begin, options.enabled);
  const state = window.__nanoBatchCurrentGenerationAttempt;
  event('pointerdown'); // Before arm: ignored.
  exec(arm);
  now = 900; event('pointerdown', false); // Synthetic event: ignored.
  now = 1000;
  if (options.gesture) { event('pointerdown'); event('mousedown'); }
  now = 1001;
  if (!options.incomplete) {
    for (const callback of micros.splice(0)) callback();
    await Promise.resolve();
    if (options.gesture) event('click');
    now = options.delayed ? 8010 : 1012;
    for (const [id, task] of [...timers]) if (task.delay === 0) { timers.delete(id); task.callback(); }
    now = options.delayed ? 8020 : 1017;
    for (const [id, callback] of [...frames]) { frames.delete(id); callback(); }
    now = options.delayed ? 8035 : 1034;
    for (const [id, callback] of [...frames]) { frames.delete(id); callback(); }
  }
  state.firstMutationAtMs = 1003;
  if (longObservers[0]) longObservers[0].callback({getEntries: () =>
    options.task_mix ? [{startTime: 1060, duration: 60}, {startTime: 1110, duration: 6800}] :
    Array.from({length: options.entries}, () => ({startTime: 995, duration: 80,
      name: 'PRIVATE_URL', attribution: [{secret: 'PRIVATE_ACCOUNT'}]}))});
  now = 8410;
  if (options.expire) {
    now = 30100;
    for (const [id, task] of [...timers]) if (task.delay === 30000) { timers.delete(id); task.callback(); }
  }
  let raw = null;
  if (options.replacement) exec(begin, options.enabled);
  else if (options.cleanup) exec(stop);
  else raw = exec(read);
  const pending = timers.size + frames.size;
  const listenerCount = [...listeners.values()].reduce((sum, set) => sum + set.size, 0);
  await Promise.resolve();
  console.log(JSON.stringify({raw, timerCalls, zeroTimers, expiryTimers, rafCalls, prevented, pending, listenerCount,
    supportedObservers: longObservers.length,
    firstDisconnected: longObservers[0]?.disconnected ?? null,
    activeObservers: window.__nanoBatchGenerationObserverMetricsV1?.active ?? null,
    hasSentinel: Boolean(window.__nanoBatchCurrentGenerationAttempt?.eventLoopSentinel)}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
""".replace("INPUT", json.dumps([begin, arm, read, stop, options]))
        result = subprocess.run([node, "-"], input=harness, text=True, capture_output=True,
                                encoding="utf-8", timeout=10, check=True)
        return json.loads(result.stdout)

    @staticmethod
    def observer(**updates):
        read = dict(wall_ms=7400, total_browser_js_ms=.4, evaluate_enter_browser_ms=8388,
                    estimated_request_to_browser_enter_ms=7387,
                    estimated_browser_exit_to_python_ms=12, clock_alignment_uncertainty_ms=2)
        read.update(updates)
        return read

    def summary(self, raw=None, **read_updates):
        return FlowSession._event_loop_sentinel_summary(
            raw if raw is not None else self.browser()["raw"], [self.observer(**read_updates)], 3)

    def test_fast_callbacks_delayed_evaluate_is_responsive_evidence(self):
        result = self.summary()
        self.assertEqual(result["observer_evaluate_start_ms"], 7388)
        self.assertEqual(result["conclusion_hint"], "renderer_responsive_external_evaluate_delay_evidence")

    def test_delayed_callbacks_are_blockage_or_throttling_evidence(self):
        result = self.summary(self.browser(delayed=True)["raw"])
        self.assertEqual(result["timeout_0_ms"], 7010)
        self.assertEqual(result["conclusion_hint"], "delayed_event_loop_callbacks_blockage_or_throttling_evidence")

    def test_microtasks_timeout_and_two_frames_are_recorded(self):
        result = self.summary()
        self.assertEqual([result[key] for key in ("pointerdown_ms", "mousedown_ms", "click_ms",
            "microtask_ms", "promise_microtask_ms", "timeout_0_ms", "raf_1_ms", "raf_2_ms")],
            [0, 0, 1, 1, 1, 12, 17, 34])
        self.assertEqual(result["installation_ms"], -900)

    def test_existing_mutation_timestamp_preserved(self):
        self.assertEqual(self.summary()["mutation_callback_ms"], 3)

    def test_longest_task_retains_its_own_start_not_first_task_start(self):
        result = self.summary(self.browser(task_mix=True)["raw"])
        self.assertEqual(result["first_longtask_start_ms"], 60)
        self.assertEqual(result["longest_longtask_start_ms"], 110)
        self.assertEqual(result["longest_longtask_ms"], 6800)

    def test_longtasks_bounded_numeric_and_private_fields_filtered(self):
        raw = self.browser(entries=1000)["raw"]
        result = self.summary(raw)
        self.assertEqual(result["longtask_count"], 128)
        self.assertTrue(result["longtask_truncated"])
        self.assertEqual(result["first_longtask_start_ms"], -5)
        self.assertTrue(result["longtask_overlaps_ack_window"])
        self.assertEqual(result["longest_longtask_ms"], 80)
        self.assertEqual(result["total_longtask_ms"], 128 * 80)
        self.assertLess(len(json.dumps(result)), 2300)
        self.assertNotIn("PRIVATE", json.dumps(raw) + json.dumps(result))

    def test_unsupported_longtask_is_safe(self):
        result = self.summary(self.browser(supported=False)["raw"])
        self.assertFalse(result["longtask_supported"])
        for key in ("longtask_count", "first_longtask_start_ms", "longest_longtask_ms",
                    "total_longtask_ms", "longtask_overlaps_ack_window"):
            self.assertIsNone(result[key])

    def test_one_shot_passive_capture_and_no_intervals(self):
        result = self.browser()
        self.assertEqual((result["zeroTimers"], result["expiryTimers"], result["rafCalls"], result["prevented"]), (1, 1, 2, 0))
        self.assertEqual((result["pending"], result["listenerCount"]), (0, 0))
        begin, _, _ = _generation_failure_observer_scripts()
        self.assertNotIn("setInterval", begin)

    def test_longtask_observe_error_is_nonfatal_and_disconnects(self):
        browser = self.browser(observe_fails=True)
        result = self.summary(browser["raw"])
        self.assertFalse(result["longtask_supported"])
        self.assertTrue(browser["firstDisconnected"])
        self.assertEqual(result["timeout_0_ms"], 12)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_normal_attempt_cleanup_disconnects_observer_and_cancels_pending_callbacks(self):
        result = self.browser(cleanup=True, incomplete=True)
        self.assertTrue(result["firstDisconnected"])
        self.assertEqual((result["pending"], result["listenerCount"]), (0, 0))
        self.assertEqual(result["activeObservers"], 0)

    def test_replaced_attempt_disconnects_prior_sentinel(self):
        result = self.browser(replacement=True, incomplete=True)
        self.assertTrue(result["firstDisconnected"])
        self.assertEqual(result["pending"], 1)  # Only the new attempt's one-shot expiry.
        self.assertEqual(result["activeObservers"], 1)

    def test_read_stops_longtask_observer_without_stopping_generation_observer(self):
        result = self.browser()
        self.assertTrue(result["firstDisconnected"])
        self.assertEqual(result["activeObservers"], 1)

    def test_disabled_installs_no_sentinel_observers_or_callbacks(self):
        result = self.browser(enabled=False)
        self.assertFalse(result["hasSentinel"])
        self.assertEqual((result["supportedObservers"], result["timerCalls"], result["rafCalls"]), (0, 0, 0))

    def test_missing_anchor_and_incomplete_callbacks_stay_null(self):
        result = self.summary(self.browser(gesture=False)["raw"])
        self.assertEqual(result["anchor"], "unavailable")
        self.assertIsNone(result["timeout_0_ms"])
        self.assertEqual(result["conclusion_hint"], "insufficient_evidence")
        result = self.summary(self.browser(incomplete=True)["raw"])
        self.assertIsNone(result["raf_2_ms"])

    def test_mousedown_fallback_and_high_alignment_uncertainty(self):
        raw = self.browser()["raw"]
        raw.pop("pointerdown_at_ms")
        result = self.summary(raw, clock_alignment_uncertainty_ms=3000)
        self.assertEqual(result["anchor"], "mousedown")
        self.assertIsNone(result["estimated_request_to_js_ms"])
        self.assertIsNone(result["estimated_js_to_python_ms"])
        self.assertEqual(result["observer_evaluate_start_ms"], 7388)

    def test_read_is_once_and_disabled_is_zero_rpc(self):
        session = self.session()
        session.page.evaluate.return_value = self.browser()["raw"]
        session._capture_event_loop_sentinel()
        session._capture_event_loop_sentinel()
        session.page.evaluate.assert_called_once()
        disabled = self.session(enabled=False)
        disabled._capture_event_loop_sentinel()
        disabled.page.evaluate.assert_not_called()

    def test_read_not_present_in_ack_or_result_poll(self):
        self.assertNotIn("event_loop_sentinel", inspect.getsource(FlowSession._wait_for_generation_start))
        self.assertNotIn("event_loop_sentinel", inspect.getsource(FlowSession._current_generation_attempt_failures))
        self.assertNotIn("event_loop_sentinel", inspect.getsource(FlowSession.wait_for_result))

    def test_diagnostic_error_is_nonfatal_and_privacy_safe(self):
        session = self.session()
        session.page.evaluate.side_effect = RuntimeError("PRIVATE_PROMPT PRIVATE_TOKEN")
        session._capture_event_loop_sentinel()
        self.assertEqual(session._event_loop_sentinel["conclusion_hint"], "insufficient_evidence")
        self.assertNotIn("PRIVATE", json.dumps(session._event_loop_sentinel))

    def test_one_sigint_during_read_remains_authoritative(self):
        session = self.session()
        def interrupt(*_args):
            signal.raise_signal(signal.SIGINT)
        session.page.evaluate.side_effect = interrupt
        with authoritative_cancellation_scope():
            with self.assertRaises(KeyboardInterrupt):
                session._capture_event_loop_sentinel()
            session._stop_current_generation_attempt_observation()
        session.page.evaluate.assert_called_once()

    def test_latched_cancellation_starts_no_read(self):
        session = self.session()
        with patch("nano_batch.adapters.flow.cancellation_requested", return_value=True):
            session._capture_event_loop_sentinel()
        session.page.evaluate.assert_not_called()

    def test_abandoned_sentinel_has_bounded_one_shot_expiry(self):
        browser = self.browser(expire=True, incomplete=True)
        self.assertTrue(browser["firstDisconnected"])
        self.assertEqual((browser["pending"], browser["listenerCount"]), (0, 0))
        result = self.summary(browser["raw"])
        self.assertTrue(result["capture_expired"])
        self.assertEqual(result["conclusion_hint"], "insufficient_evidence")

    def test_cancellation_translated_into_transport_or_normal_return_still_unwinds(self):
        for translated in (False, True):
            session = self.session()
            with authoritative_cancellation_scope() as state:
                def evaluate(*_args):
                    state.request()
                    if translated:
                        raise RuntimeError("transport interrupted")
                    return {}
                session.page.evaluate.side_effect = evaluate
                with self.assertRaises(KeyboardInterrupt):
                    session._capture_event_loop_sentinel()
            session.page.evaluate.assert_called_once()

    def test_early_frames_do_not_rule_out_later_longtask_blockage(self):
        raw = self.browser()["raw"]
        raw["longest_longtask_ms"] = 7000
        result = self.summary(raw)
        self.assertEqual(result["conclusion_hint"], "mixed_event_loop_and_evaluate_delay_evidence")

    def test_formatter_is_bounded_and_filters_unknown_fields(self):
        raw = self.summary()
        raw["conclusion_hint"] = "PRIVATE_PROMPT"
        raw["pointerdown_ms"] = "PRIVATE_ACCOUNT"
        output = FlowSession._format_event_loop_vs_cdp({"browser_event_loop_sentinel": raw})
        self.assertIn("EVENT LOOP VS CDP", output)
        self.assertNotIn("PRIVATE", output)
        self.assertLess(len(output), 1800)
        self.assertIn("insufficient evidence", output)

    def test_capture_after_ack_success_or_error_without_another_start(self):
        for failure in (None, FlowAutomationError("existing terminal failure")):
            session = self.session()
            session._begin_post_start_ack_timing(origin_source="test")
            events = []
            prompt, button = MagicMock(), MagicMock()
            def wait(**_kwargs):
                events.append("ack")
                self.assertFalse(session._event_loop_sentinel_read)
                if failure:
                    raise failure
                return {"signal": "new_generation_progress"}
            def read(*_args):
                events.append("read")
                return {}
            def activate_once(*_args):
                session._begin_post_start_ack_timing(origin_source="test")
                return {}
            session.page.evaluate.side_effect = read
            with patch.object(session, "_wait_for_owned_generation_control_structure", return_value=(prompt, {})), \
                 patch.object(session, "_wait_for_generation_submit_button", return_value=(prompt, button, {}, {})), \
                 patch.object(session, "_begin_current_generation_attempt_observation"), \
                 patch.object(session, "_arm_current_generation_attempt_observation"), \
                 patch.object(session, "_activate_generation_submit_button", side_effect=activate_once) as activate, \
                 patch.object(session, "_wait_for_generation_start", side_effect=wait), \
                 patch.object(session, "_capture_latency_counts"), \
                 patch.object(session, "_stop_current_generation_attempt_observation"), \
                 patch("builtins.print"):
                if failure:
                    with self.assertRaises(FlowAutomationError) as caught:
                        session.submit_prompt("offline", baseline_fingerprints=set(), baseline_progress=set())
                    self.assertIs(caught.exception, failure)
                else:
                    result = session.submit_prompt("offline", baseline_fingerprints=set(), baseline_progress=set())
                    self.assertIn("browser_event_loop_sentinel", result["post_start_ack_timing"])
            self.assertEqual(events, ["ack", "read"])
            activate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
