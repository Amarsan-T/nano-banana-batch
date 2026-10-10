import inspect
import json
import signal
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowSession, _AckPollProfiler, _ACTIVE_ACK_PROFILER, _ACK_CALL_BUDGETS,
    _PostStartAckTimingRecorder,
)
from nano_batch.cancellation import authoritative_cancellation_scope
from tests.test_flow_composer_generation import composer_resolution


class AckPollTimingTests(unittest.TestCase):
    class Clock:
        def __init__(self):
            self.now = 0.0
        def __call__(self):
            return self.now
        def advance(self, milliseconds):
            self.now += milliseconds / 1000

    def session(self, debug=True):
        session = FlowSession("https://flow.google.com", Path("unused"),
                              prestart_timing_debug=debug)
        session.page = MagicMock()
        return session

    def slow_poll(self, slow="candidate"):
        session, clock = self.session(), self.Clock()
        def evaluate(script, *_args, **_kwargs):
            if "scanFailureContainers" in script:
                clock.advance(8000 if slow == "observer" else 30)
                return {"failures": [], "diagnostics": {
                    "generation_dom_mutation_count": 1,
                    "first_mutation_at_ms": 1003,
                }}
            if "noteProgress" in script:
                clock.advance(20)
                return []
            if "const limit = 30" in script:
                clock.advance(7900 if slow == "composer" else 50)
                return composer_resolution(prompt_editor_count=0, composer_depth=None,
                    active_composer_generation_control_count=0,
                    actionable_owned_generation_control_count=0, ready=False)
            clock.advance(7900 if slow == "candidate" else 50)
            return []
        session.page.evaluate.side_effect = evaluate
        with patch("nano_batch.adapters.flow.time.perf_counter", side_effect=clock), \
             patch("nano_batch.adapters.flow.time.monotonic", side_effect=clock):
            session._begin_post_start_ack_timing(origin_source="mouse_down_boundary")
            session._post_start_ack_timing.set_browser_anchor(dict(
                browser_now_ms=1000, python_midpoint=0, uncertainty_ms=1))
            result = session._wait_for_generation_start(prompt_input=MagicMock(),
                intended_prompt="private prompt", baseline_fingerprints=set(),
                baseline_progress=set(), timeout_ms=5000)
        self.assertEqual(result, {"signal": "submit_button_removed"})
        session.page.wait_for_timeout.assert_not_called()
        return session._post_start_ack_timing_snapshot()

    def test_eight_second_candidate_poll_identifies_exact_nested_rpc(self):
        timing = self.slow_poll()
        breakdown = timing["ack_poll_breakdown"]
        poll = breakdown["slowest_polls"][0]
        self.assertAlmostEqual(poll["poll_start_ms"], 0)
        self.assertAlmostEqual(poll["poll_end_ms"], 8000)
        self.assertAlmostEqual(poll["poll_duration_ms"], 8000)
        self.assertEqual(poll["dominant_stage"], "candidate_observation")
        self.assertEqual(poll["dominant_nested_helper"], "candidate_snapshot_page_evaluate")
        self.assertEqual(breakdown["calls"]["candidate_snapshot_page_evaluate"]["max_ms"], 7900)
        self.assertEqual(breakdown["calls"]["candidate_snapshot_page_evaluate"]["max_poll"], 1)
        self.assertEqual(timing["ack_signal"], "submit_button_removed")
        self.assertEqual(timing["configured_timeout_ms"], 5000)

    def test_slow_composer_identifies_resolution_evaluate(self):
        timing = self.slow_poll("composer")
        poll = timing["ack_poll_breakdown"]["slowest_polls"][0]
        self.assertEqual(poll["dominant_stage"], "composer_resolution")
        self.assertEqual(poll["dominant_nested_helper"], "composer_page_evaluate")
        self.assertEqual(timing["ack_poll_breakdown"]["calls"]["composer_page_evaluate"]["max_ms"], 7900)

    def test_slow_observer_rpc_is_separate_from_other_stage_blocking(self):
        timing = self.slow_poll("observer")
        poll = timing["ack_poll_breakdown"]["slowest_polls"][0]
        self.assertEqual(poll["dominant_stage"], "failure_observation")
        self.assertEqual(poll["dominant_nested_helper"], "observer_page_evaluate")
        self.assertEqual(timing["observer_read_call_duration_ms"], 8000)
        self.assertEqual(timing["browser_first_mutation_callback_ms"], 3)
        self.assertEqual(timing["python_first_observer_read_nonzero_ms"], 8000)
        self.assertEqual(timing["callback_to_python_observation_gap_ms"], 7997)

    def test_callback_at_three_read_at_8400_reports_8397_gap(self):
        clock = self.Clock()
        recorder = _PostStartAckTimingRecorder(clock, initial_composer_token=None, origin_source="test")
        recorder.set_browser_anchor(dict(browser_now_ms=1000, python_midpoint=0, uncertainty_ms=1))
        recorder.observe_browser_metrics({"first_mutation_at_ms": 1003})
        clock.advance(8400)
        recorder.note_observer_read({"generation_dom_mutation_count": 1}, 8)
        timing = recorder.snapshot()
        self.assertEqual(timing["callback_to_python_observation_gap_ms"], 8397)
        self.assertEqual(timing["observer_read_call_duration_ms"], 8)
        self.assertIsNone(timing["browser_callback_unavailable_reason"])

    def test_null_callback_has_safe_specific_reason(self):
        for raw, anchor, reason in (
            ({"first_mutation_at_ms": None}, True, "browser_callback_not_captured"),
            ({"first_mutation_at_ms": 1003}, False, "clock_anchor_unavailable"),
            ({"first_mutation_at_ms": 999}, True, "callback_precedes_click_boundary"),
        ):
            recorder = _PostStartAckTimingRecorder(lambda: 0, initial_composer_token=None, origin_source="test")
            if anchor:
                recorder.set_browser_anchor(dict(browser_now_ms=1000, python_midpoint=0, uncertainty_ms=1))
            recorder.observe_browser_metrics(raw)
            recorder.note_observer_read({"generation_dom_mutation_count": 1}, 10)
            timing = recorder.snapshot()
            self.assertIsNone(timing["browser_first_mutation_callback_ms"])
            self.assertIsNone(timing["callback_to_python_observation_gap_ms"])
            self.assertEqual(timing["browser_callback_unavailable_reason"], reason)

    def test_stage_order_and_existing_wait_are_identical_with_debug_on_off(self):
        for debug in (False, True):
            session, clock = self.session(debug), self.Clock()
            events = []
            progress_calls = 0
            def event(name, value):
                events.append(name)
                return value
            def progress():
                nonlocal progress_calls
                progress_calls += 1
                return event("progress", () if progress_calls == 1 else ("Generating",))
            session.page.wait_for_timeout.side_effect = lambda ms: (event("wait", None), clock.advance(ms))
            with patch("nano_batch.adapters.flow.time.perf_counter", side_effect=clock), \
                 patch("nano_batch.adapters.flow.time.monotonic", side_effect=clock), \
                 patch("nano_batch.adapters.flow.cancellation_checkpoint", side_effect=lambda *_: event("cancel", None)), \
                 patch.object(session, "_raise_for_current_generation_attempt_failure", side_effect=lambda: event("failure", None)), \
                 patch.object(session, "_progress_signals", side_effect=progress), \
                 patch.object(session, "discover_candidates", side_effect=lambda *a, **k: event("candidate", ())), \
                 patch.object(session, "_current_generation_submit_buttons", side_effect=lambda: event("composer", (None,[MagicMock()], {
                     "active_composer_generation_control_count":1, "actionable_owned_generation_control_count":1}))), \
                 patch.object(session, "_live_control_facts", side_effect=lambda *_: event("start", {"enabled":True})), \
                 patch.object(session, "_prompt_control_post_start_state", side_effect=lambda *_: event("prompt", {
                     "changed":False,"cleared":False,"disabled":False})):
                result = session._wait_for_generation_start(prompt_input=MagicMock(), intended_prompt="private",
                    baseline_fingerprints=set(), baseline_progress=set(), timeout_ms=5000)
            self.assertEqual(result, {"signal":"new_generation_progress"})
            self.assertEqual(events, ["cancel","failure","progress","candidate","composer","start","prompt","wait",
                                      "cancel","failure","progress"])
            session.page.wait_for_timeout.assert_called_once_with(100)
            timing = session._post_start_ack_timing_snapshot()
            if debug:
                polls = timing["ack_poll_breakdown"]["slowest_polls"]
                self.assertEqual(polls[0]["poll_duration_ms"], 100)
                self.assertEqual(polls[0]["dominant_stage"], "poll_wait")
            else:
                self.assertIsNone(timing["ack_poll_breakdown"])

    def test_statistics_and_slowest_polls_are_bounded(self):
        clock = self.Clock()
        profiler = _AckPollProfiler(clock)
        for poll in range(300):
            profiler.begin_poll(0)
            with profiler.span("candidate_observation"):
                with profiler.span("candidate_snapshot_page_evaluate"):
                    clock.advance(poll+1)
            profiler.finish_poll()
        result = profiler.snapshot()
        stats = result["calls"]["candidate_snapshot_page_evaluate"]
        self.assertEqual(stats["total_calls"], 300)
        self.assertEqual(stats["total_elapsed_ms"], 45150)
        self.assertEqual(stats["min_ms"], 1)
        self.assertEqual(stats["max_ms"], 300)
        self.assertEqual(stats["max_poll"], 300)
        self.assertEqual(stats["last_call_duration_ms"], 300)
        self.assertEqual(stats["percentile_sample_count"], 128)
        self.assertIsNotNone(stats["p95_ms"])
        self.assertEqual(len(result["slowest_polls"]), 5)
        self.assertLess(len(json.dumps(result)), 4500)

    def test_python_helper_self_time_is_not_hidden_by_fast_nested_rpc(self):
        clock = self.Clock()
        profiler = _AckPollProfiler(clock)
        profiler.begin_poll(0)
        with profiler.span("candidate_observation"):
            with profiler.span("_asset_snapshot"):
                with profiler.span("candidate_snapshot_page_evaluate"):
                    clock.advance(5)
                clock.advance(7995)
        profiler.finish_poll()
        result = profiler.snapshot()
        self.assertEqual(result["slowest_polls"][0]["dominant_nested_helper"], "_asset_snapshot")
        self.assertEqual(result["calls"]["_asset_snapshot"]["max_self_ms"], 7995)

    def test_unknown_names_and_private_data_never_enter_profiler(self):
        profiler = _AckPollProfiler(self.Clock())
        profiler.begin_poll(0)
        with profiler.span("private prompt secret@example.com"):
            pass
        profiler.finish_poll()
        text = json.dumps(profiler.snapshot())
        self.assertNotIn("private", text)
        self.assertNotIn("example.com", text)
        self.assertEqual(profiler.snapshot()["calls"], {})

    def test_inherited_and_explicit_budgets_recorded_without_changing_calls(self):
        session, clock = self.session(), self.Clock()
        page, prompt, start, composer = session.page, MagicMock(), MagicMock(), MagicMock()
        page.get_by_role.return_value.all.return_value = [prompt]
        prompt.is_visible.return_value = prompt.is_enabled.return_value = True
        prompt.get_attribute.side_effect = ["Prompt", None, None]
        prompt.locator.return_value = composer
        composer.locator.return_value = composer
        composer.get_by_role.return_value.all.return_value = [start]
        start.evaluate.side_effect = [dict(connected=True, visible=True, enabled=True, pointer_events="auto",
            bounding_box=dict(x=0,y=0,width=30,height=30)), dict(connected=True,visible=True,enabled=False)]
        page.evaluate.side_effect = [dict(failures=[]), [], [], composer_resolution()]
        with patch("nano_batch.adapters.flow.time.perf_counter", side_effect=clock), \
             patch("nano_batch.adapters.flow.time.monotonic", side_effect=clock):
            session._begin_post_start_ack_timing(origin_source="test")
            session._wait_for_generation_start(prompt_input=prompt, intended_prompt="private",
                baseline_fingerprints=set(), baseline_progress=set(), timeout_ms=5000)
        calls = session._post_start_ack_timing_snapshot()["ack_poll_breakdown"]["calls"]
        self.assertEqual(calls["prompt_get_attribute"]["total_calls"], 3)
        self.assertEqual(calls["prompt_get_attribute"]["inherited_library_default_ms"], 30000)
        self.assertEqual(calls["live_control_evaluate"]["timeout_ms"], 500)
        for call in start.evaluate.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 500)

    def test_keyboard_interrupt_propagates_once_and_context_is_reset(self):
        session = self.session()
        signals = 0
        def interrupted(*_args, **_kwargs):
            nonlocal signals
            signals += 1
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        session.page.evaluate.side_effect = interrupted
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            session._wait_for_generation_start(prompt_input=MagicMock(), intended_prompt="private",
                baseline_fingerprints=set(), baseline_progress=set(), timeout_ms=5000)
        self.assertEqual(signals, 1)
        self.assertEqual(session.page.evaluate.call_count, 1)
        self.assertIsNone(_ACTIVE_ACK_PROFILER.get())
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()

    def test_terminal_output_is_bounded_allowlisted_and_prompt_free(self):
        timing = self.slow_poll()
        timing["prompt"] = "private prompt cookie secret@example.com"
        timing["ack_poll_breakdown"]["calls"]["unknown private"] = {"max_ms":"secret"}
        output = FlowSession._format_ack_poll_breakdown(timing)
        self.assertIn("ACK POLL BREAKDOWN", output)
        self.assertIn("candidate_snapshot_page_evaluate", output)
        self.assertLess(len(output.splitlines()), 18)
        self.assertLess(len(output), 700)
        for text in ("private", "cookie", "secret", "example.com"):
            self.assertNotIn(text, output)
        self.assertIn("observer read: -", FlowSession._format_ack_poll_breakdown({}))

    def test_observer_javascript_is_not_modified_by_timing(self):
        session = self.session()
        session._current_generation_attempt_failures()
        self.assertIn("scanFailureContainers", session.page.evaluate.call_args.args[0])
        self.assertEqual(session.page.evaluate.call_count, 1)
        self.assertIsNone(_ACTIVE_ACK_PROFILER.get())
        source = inspect.getsource(FlowSession._wait_for_generation_start)
        self.assertEqual(source.count("page.wait_for_timeout"), 1)
        self.assertNotIn("time.sleep", source)
        self.assertNotIn(".click(", source)
        self.assertNotIn(".send(", source)


if __name__ == "__main__":
    unittest.main()
