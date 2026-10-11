"""Pre-commit review: execute observer scripts in the actual Python call order."""

import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import FlowAutomationError, FlowGenericGenerationFailed, FlowSession
from nano_batch.models import GenerationSettings
from nano_batch.runner import execute_sequential_batch, plan_sequential_batch
from nano_batch.state import RunStateStore
from tests import test_flow_explicit_failures as explicit_failures
from tests import test_flow_production_window as window_tests
from tests.test_flow_adapter import generation_submit_snapshot
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, PROJECT_URL, _generation_failure_observer_scripts,
)
from tests.test_flow_failure_attribution import _DOM


def _execute_steps(steps, failure_on):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for offline observer tests.")
    scripts = _generation_failure_observer_scripts()
    harness = "const [begin, arm, poll, failure, steps, failureOn] = " + json.dumps(
        [*scripts, GENERIC_GENERATION_FAILURE, steps, failure_on]
    ) + ";\n" + _DOM
    harness += """
const history = body.append(new Element('section'));
let armed = true, baselineCaptures = 0;
for (const step of steps) {
  if (step === 'begin') {
    exec(begin);
    const state = window.__nanoBatchCurrentGenerationAttempt;
    const capture = state.captureFailureBaseline;
    state.captureFailureBaseline = () => { baselineCaptures++; capture(); };
  }
  if (step === 'arm') exec(arm);
  if (step === 'rearm') {
    const state = window.__nanoBatchCurrentGenerationAttempt;
    if (state) state.active = false;
    armed = (0, eval)('(' + arm + ')')(true);
  }
  if (step === failureOn) {
    const card = history.append(new Element('article', failure));
    window.__nanoBatchCurrentGenerationAttempt.observer.callback([
      {type: 'childList', target: history, addedNodes: [card], removedNodes: []},
    ]);
  }
  if (step === 'lose_observer') {
    window.__nanoBatchCurrentGenerationAttempt.observer.disconnect();
    delete window.__nanoBatchCurrentGenerationAttempt;
  }
  if (step === 'lose_provenance') delete window.__nanoBatchCurrentGenerationAttempt.failureBaseline;
}
const result = armed ? exec(poll) : {};
process.stdout.write(JSON.stringify({...result, armed, baselineCaptures}));
"""
    result = subprocess.run([node, "-"], input=harness, text=True, encoding="utf-8",
                            capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


class GenerationFailureBoundaryReviewTests(unittest.TestCase):
    def recording_session(self, failure_on):
        session = FlowSession(PROJECT_URL, Path("unused-profile"))
        session.page = MagicMock()
        steps = []
        begin, arm, poll = _generation_failure_observer_scripts()

        def evaluate(script, *args):
            if script == begin:
                steps.append("begin")
            elif script == arm:
                if args and args[0] is True:
                    steps.append("rearm")
                    return _execute_steps(steps, failure_on)["armed"]
                steps.append("arm")
            elif script == poll:
                steps.append("poll")
                return _execute_steps(steps, failure_on)
            else:
                self.fail("Unexpected browser evaluation in offline review.")

        session.page.evaluate.side_effect = evaluate
        session.page.mouse.move.side_effect = lambda *a, **k: steps.append("move")
        session.page.mouse.down.side_effect = lambda *a, **k: steps.append("down")
        session.page.mouse.up.side_effect = lambda *a, **k: steps.append("up")
        return session, steps

    def test_fast_failure_on_either_start_gesture_is_not_historical(self):
        for failure_on in ("down", "up"):
            with self.subTest(failure_on=failure_on):
                session, steps = self.recording_session(failure_on)
                prompt, button = MagicMock(), MagicMock()
                snapshot = generation_submit_snapshot()
                box = snapshot["bounding_box_samples"][-1]
                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        session, "_wait_for_owned_generation_control_structure",
                        return_value=(prompt, {"structural_generation_control_ready": True}),
                    ))
                    stack.enter_context(patch.object(
                        session, "_wait_for_generation_submit_button",
                        return_value=(prompt, button, snapshot, box),
                    ))
                    stack.enter_context(patch.object(
                        session, "_current_generation_submit_hit", return_value=box,
                    ))
                    stack.enter_context(patch.object(
                        session, "_stop_current_generation_attempt_observation",
                    ))
                    discover = stack.enter_context(patch.object(session, "discover_candidates"))
                    with self.assertRaises(FlowGenericGenerationFailed):
                        session.submit_prompt("synthetic review prompt",
                                              baseline_fingerprints=set(), baseline_progress=set())
                self.assertEqual(steps, ["begin", "arm", "move", "down", "up", "poll"])
                discover.assert_not_called()
                session.page.mouse.down.assert_called_once()
                session.page.mouse.up.assert_called_once()

    def test_missing_observer_recovery_does_not_silently_absorb_current_failure(self):
        session, steps = self.recording_session("up")
        session._begin_current_generation_attempt_observation()
        session._arm_current_generation_attempt_observation()
        session.page.mouse.down(button="left")
        session.page.mouse.up(button="left")
        steps.append("lose_observer")
        before_loss = _execute_steps(steps[:-1], "up")
        self.assertEqual(before_loss["failures"], [GENERIC_GENERATION_FAILURE])
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS") as caught:
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": False, "attempt_observer_active": False,
            })
        self.assertNotIsInstance(caught.exception, FlowGenericGenerationFailed)
        self.assertEqual(steps, ["begin", "arm", "down", "up", "lose_observer"])

    def test_first_initialization_captures_once_without_failure(self):
        result = _execute_steps(["begin", "arm"], None)
        self.assertEqual(result["baselineCaptures"], 1)
        self.assertEqual(result["failures"], [])
        self.assertFalse(result["failure_attribution_ambiguous"])

    def test_existing_observer_rearm_preserves_visible_failure_and_original_baseline(self):
        session, steps = self.recording_session("up")
        session._begin_current_generation_attempt_observation()
        session._arm_current_generation_attempt_observation()
        session.page.mouse.up(button="left")
        self.assertTrue(session._rearm_generation_observer_after_window_recovery({
            "attempt_observer_present": True, "attempt_observer_active": False,
        }))
        result = _execute_steps(steps, "up")
        self.assertEqual(result["baselineCaptures"], 1)
        self.assertEqual(result["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["failure_attribution_ambiguous"])

    def test_already_active_observer_is_left_untouched(self):
        session, steps = self.recording_session(None)
        self.assertFalse(session._rearm_generation_observer_after_window_recovery({
            "attempt_observer_present": True, "attempt_observer_active": True,
        }))
        self.assertEqual(steps, [])
        session.page.evaluate.assert_not_called()

    def test_missing_observer_without_a_visible_failure_still_fails_closed(self):
        session, steps = self.recording_session(None)
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": False, "attempt_observer_active": False,
            })
        self.assertEqual(steps, [])

    def test_replacement_without_a_start_baseline_cannot_be_armed_as_recovery(self):
        session, steps = self.recording_session(None)
        session._begin_current_generation_attempt_observation()
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": True, "attempt_observer_active": False,
            })
        self.assertEqual(_execute_steps(steps, None)["baselineCaptures"], 0)

    def test_observer_disappearing_after_snapshot_fails_closed(self):
        session, steps = self.recording_session("up")
        session._begin_current_generation_attempt_observation()
        session._arm_current_generation_attempt_observation()
        session.page.mouse.up(button="left")
        steps.append("lose_observer")
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": True, "attempt_observer_active": False,
            })
        self.assertEqual(steps.count("begin"), 1)
        self.assertEqual(steps.count("arm"), 1)

    def test_lost_baseline_in_existing_state_fails_closed(self):
        session, steps = self.recording_session("up")
        session._begin_current_generation_attempt_observation()
        session._arm_current_generation_attempt_observation()
        session.page.mouse.up(button="left")
        steps.append("lose_provenance")
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": True, "attempt_observer_active": False,
            })

    def test_active_observer_with_lost_provenance_is_not_accepted(self):
        session, steps = self.recording_session(None)
        with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._rearm_generation_observer_after_window_recovery({
                "attempt_observer_present": True, "attempt_observer_active": True,
                "attempt_observer_provenance_lost": True,
            })
        self.assertEqual(steps, [])

    def test_normal_failure_poll_reports_loss_without_window_recovery(self):
        for loss in ("lose_observer", "lose_provenance"):
            with self.subTest(loss=loss):
                result = _execute_steps(["begin", "arm", "up", loss], "up")
                self.assertEqual(result["failures"], [])
                self.assertTrue(result["failure_attribution_ambiguous"])
                session = FlowSession(PROJECT_URL, Path("unused-profile"))
                session.page = MagicMock()
                session.page.evaluate.return_value = result
                with self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
                    session._current_generation_attempt_failures()


class LostObserverRecoveryIntegrationTests(unittest.TestCase):
    @staticmethod
    def session():
        session, page, _ = window_tests.ProductionWindowTests().session("minimized")
        page.url = PROJECT_URL
        return session, page

    def test_settle_stops_on_first_missing_observer_snapshot_even_before_dom_ready(self):
        session, page = self.session()
        with patch.object(session, "_production_window_snapshot", return_value={
            "window_id": 17, "window_state": "maximized",
            "viewport": window_tests.usable_geometry(),
        }), patch.object(session, "_post_window_recovery_dom_snapshot", return_value=
                         window_tests.post_restore_dom(ready_state="loading")) as snapshot, \
             patch.object(session, "_begin_current_generation_attempt_observation") as begin, \
             self.assertRaisesRegex(FlowAutomationError, "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session._wait_for_post_window_recovery_settle(
                operation_phase="result_wait", candidate_id=None, require_semantic_surface=True,
                same_generation_attempt_active=True, start_acknowledged=True,
            )
        snapshot.assert_called_once()
        begin.assert_not_called()
        page.wait_for_timeout.assert_not_called()

    def test_window_recovery_preserves_terminal_ambiguity_and_blocks_candidate_success(self):
        session, page = self.session()
        with patch.object(session, "_post_window_recovery_dom_snapshot",
                          return_value=window_tests.post_restore_dom()) as snapshot, \
             patch.object(session, "discover_candidates") as discover, \
             patch.object(session, "_begin_current_generation_attempt_observation") as begin, \
             self.assertRaisesRegex(FlowAutomationError, "^FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
            session.wait_for_result(set(), 300, expected_count=1)
        snapshot.assert_called_once()
        discover.assert_not_called()
        begin.assert_not_called()
        page.wait_for_timeout.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_cancellation_wins_over_ambiguity_during_settle(self):
        session, page = self.session()
        with patch.object(session, "_production_window_snapshot", return_value={
            "window_id": 17, "window_state": "maximized",
            "viewport": window_tests.usable_geometry(),
        }), patch.object(session, "_post_window_recovery_dom_snapshot",
                         return_value=window_tests.post_restore_dom()), \
             patch("nano_batch.adapters.flow.cancellation_checkpoint",
                   side_effect=[None, KeyboardInterrupt]), self.assertRaises(KeyboardInterrupt):
            session._wait_for_post_window_recovery_settle(
                operation_phase="result_wait", candidate_id=None, require_semantic_surface=True,
                same_generation_attempt_active=True, start_acknowledged=True,
            )
        page.wait_for_timeout.assert_not_called()

    def test_lost_observer_ambiguity_persists_manual_retry_without_automatic_recovery(self):
        class Adapter(explicit_failures.PolicyFailureRunnerTests.Adapter):
            fail_on = True

            def generate_one(self, **kwargs):
                if not self.fail_on:
                    return super().generate_one(**kwargs)
                self.events.append(("generate", kwargs["item"].order))
                session, _ = LostObserverRecoveryIntegrationTests.session()
                with patch.object(session, "_post_window_recovery_dom_snapshot",
                                  return_value=window_tests.post_restore_dom()):
                    session.wait_for_result(set(), 300, expected_count=1)

        with tempfile.TemporaryDirectory() as tmp:
            run_dir, adapter = Path(tmp) / "run", Adapter()
            batch = explicit_failures.PolicyFailureRunnerTests.batch()
            settings = GenerationSettings(variants=1)
            with self.assertRaisesRegex(FlowAutomationError, "^FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"):
                execute_sequential_batch(batch, run_dir, adapter, settings, manage_flow_project=True)
            saved = RunStateStore(run_dir / "run_state.json").load()
            order = saved.items[0]
            self.assertEqual(order.status, "failed")
            self.assertIn("FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS", order.error)
            self.assertIsNone(order.generation_error_code)
            self.assertEqual(order.attempt_count, 1)
            self.assertEqual(order.automatic_policy_retry_count, 0)
            self.assertEqual(order.automatic_generic_failure_retry_count, 0)
            self.assertEqual(order.candidates, [])
            self.assertEqual(saved.items[1].status, "pending")
            self.assertEqual(saved.items[1].attempt_count, 0)
            self.assertEqual(adapter.events.count(("generate", 1)), 1)
            self.assertFalse(any(isinstance(event, tuple) and event[0] in
                                 {"navigate_home", "reopen", "fresh_baseline", "download"}
                                 for event in adapter.events))
            plan = plan_sequential_batch(batch, run_dir, settings)
            self.assertEqual(plan.orders[0].generation_action, "blocked_retry_required")
            adapter.fail_on = False
            adapter.generation_steps = ["success", "success"]
            execute_sequential_batch(batch, run_dir, adapter, settings, retry_failed_generation=True,
                                     manage_flow_project=True)
            retried = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(retried.flow_project, saved.flow_project)
            self.assertEqual(retried.items[0].attempt_count, 2)
            self.assertEqual(retried.items[0].automatic_policy_retry_count, 0)
            self.assertEqual(retried.items[0].automatic_generic_failure_retry_count, 0)
            self.assertTrue(all(item.status == "completed" for item in retried.items))


if __name__ == "__main__":
    unittest.main()
