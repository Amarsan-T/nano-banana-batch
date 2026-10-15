"""Offline diagnostic accuracy checks; no real browser, prompts, or Flow access."""

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import (
    FlowAutomationError, FlowFailureAttributionAmbiguous, FlowGenericGenerationFailed,
    FlowHighDemand, FlowPolicyRejected, FlowSession, FlowUnusualActivity, FlowUsageLimitReached,
)
from nano_batch.models import GenerationSettings
from nano_batch.runner import execute_sequential_batch, plan_sequential_batch
from tests import test_flow_explicit_failures as explicit_failures
from tests import test_flow_failure_privacy as privacy_tests
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE,
    UNUSUAL_ACTIVITY_FAILURE,
)
from pending_download_fixtures import pending_download_test_directory


class AmbiguityDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def wait(session):
        return session._wait_for_generation_start(
            prompt_input=MagicMock(), intended_prompt="synthetic prompt",
            baseline_fingerprints=set(), baseline_progress=set(), timeout_ms=500,
        )

    def assert_ambiguous_timing(self, timing):
        self.assertEqual(timing["schema"], "nano-banana-post-start-ack-timing/v1")
        self.assertEqual(timing["ack_signal"], "failure_attribution_ambiguous")
        self.assertFalse(timing["last_poll"]["exact_explicit_failure_detected"])
        self.assertNotIn("explicit_failure_ms", timing)
        for entry in timing["state_change_trace"]:
            self.assertFalse(entry["state"]["exact_explicit_failure_detected"])
        rendered = FlowSession._format_post_start_ack_timing(timing)
        self.assertIn("ACK: failure_attribution_ambiguous", rendered)
        self.assertNotIn("exact explicit failure", rendered)

    def test_remounted_failure_ack_is_ambiguous_without_an_exact_failure_mark(self):
        snapshot = privacy_tests.FailurePrivacyTests.snapshot(
            POLICY_FAILURE, historical=True, remount=True,
        )
        self.assertTrue(snapshot["failure_attribution_ambiguous"])
        for debug in (False, True):
            with self.subTest(debug=debug):
                session = privacy_tests.FailurePrivacyTests.session(snapshot, debug=debug)
                with patch.object(session, "discover_candidates") as discover:
                    with self.assertRaises(FlowFailureAttributionAmbiguous):
                        self.wait(session)
                    self.assert_ambiguous_timing(session._post_start_ack_timing_snapshot())
                    self.assertFalse(session._current_generation_failure_poll_observation["exact_explicit_failure_detected"])
                    session.page.evaluate.assert_called_once()
                    session._progress_signals.assert_not_called()
                    discover.assert_not_called()
                    session.page.wait_for_timeout.assert_not_called()
                    session.page.mouse.down.assert_not_called()

    def test_ambiguity_takes_priority_over_qualified_failure_items_in_poll_metadata(self):
        # A conservative ambiguity result can coexist with a separate qualified card.
        snapshot = {
            "failures": [HIGH_DEMAND_FAILURE], "failure_attribution_ambiguous": True,
            "diagnostics": {"generation_dom_mutation_count": 3},
        }
        session = privacy_tests.FailurePrivacyTests.session(snapshot)
        with self.assertRaises(FlowFailureAttributionAmbiguous):
            session._current_generation_attempt_failures()
        observation = session._current_generation_failure_poll_observation
        self.assertEqual(observation["generation_dom_mutation_count"], 3)
        self.assertIs(observation["exact_explicit_failure_detected"], False)
        session.page.evaluate.assert_called_once()

    def test_ambiguity_without_poll_metadata_is_still_not_an_explicit_failure(self):
        session = privacy_tests.FailurePrivacyTests.session({})
        error = FlowFailureAttributionAmbiguous("synthetic lost attempt provenance.")
        with patch.object(session, "_raise_for_current_generation_attempt_failure", side_effect=error):
            with self.assertRaises(FlowFailureAttributionAmbiguous) as raised:
                self.wait(session)
        self.assertIs(raised.exception, error)
        self.assert_ambiguous_timing(session._post_start_ack_timing_snapshot())
        session.page.evaluate.assert_not_called()

    def test_confirmed_exact_failures_keep_existing_ack_labels(self):
        for message, error_type in (
            (POLICY_FAILURE, FlowPolicyRejected), (HIGH_DEMAND_FAILURE, FlowHighDemand),
            (UNUSUAL_ACTIVITY_FAILURE, FlowUnusualActivity),
            (GENERIC_GENERATION_FAILURE, FlowGenericGenerationFailed),
            ("You've reached your usage limit.", FlowUsageLimitReached),
        ):
            with self.subTest(error_type=error_type.__name__):
                snapshot = privacy_tests.FailurePrivacyTests.snapshot(message)
                session = privacy_tests.FailurePrivacyTests.session(snapshot)
                with self.assertRaises(error_type):
                    self.wait(session)
                timing = session._post_start_ack_timing_snapshot()
                self.assertEqual(timing["ack_signal"], "explicit_failure")
                self.assertTrue(timing["last_poll"]["exact_explicit_failure_detected"])
                self.assertIn("explicit_failure_ms", timing)
                session.page.evaluate.assert_called_once()
                session.page.wait_for_timeout.assert_not_called()

    def test_qualified_unknown_failure_keeps_its_privacy_and_existing_label(self):
        message = "Generation failed: " + privacy_tests.CANARIES[-1]
        snapshot = privacy_tests.FailurePrivacyTests.snapshot(message)
        session = privacy_tests.FailurePrivacyTests.session(snapshot)
        with self.assertRaises(FlowAutomationError) as raised:
            self.wait(session)
        self.assertIs(type(raised.exception), FlowAutomationError)
        self.assertIn("raw UI text was omitted", str(raised.exception))
        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(timing["ack_signal"], "explicit_failure")
        self.assertTrue(timing["last_poll"]["exact_explicit_failure_detected"])
        for text in (str(raised.exception), json.dumps(timing), FlowSession._format_post_start_ack_timing(timing)):
            for canary in privacy_tests.CANARIES:
                self.assertNotIn(canary, text)

    def test_historical_stale_card_does_not_override_real_progress_ack(self):
        snapshot = privacy_tests.FailurePrivacyTests.snapshot(HIGH_DEMAND_FAILURE, historical=True)
        self.assertFalse(snapshot["failure_attribution_ambiguous"])
        self.assertEqual(snapshot["failures"], [])
        session = privacy_tests.FailurePrivacyTests.session(snapshot)
        session._progress_signals.return_value = ("synthetic current progress",)
        self.assertEqual(self.wait(session), {"signal": "new_generation_progress"})
        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(timing["ack_signal"], "new_generation_progress")
        self.assertFalse(timing["last_poll"]["exact_explicit_failure_detected"])
        self.assertNotIn("explicit_failure_ms", timing)
        session.page.evaluate.assert_called_once()

    def test_successful_start_ack_remains_unchanged(self):
        session = privacy_tests.FailurePrivacyTests.session({"failures": [], "diagnostics": {}})
        session._progress_signals.return_value = ("synthetic current progress",)
        with patch.object(session, "discover_candidates") as discover:
            self.assertEqual(self.wait(session), {"signal": "new_generation_progress"})
            discover.assert_not_called()
        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(timing["ack_signal"], "new_generation_progress")
        self.assertEqual(timing["poll_count"], 1)
        self.assertFalse(timing["last_poll"]["exact_explicit_failure_detected"])
        session.page.evaluate.assert_called_once()
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()

    def test_persisted_ambiguity_is_private_and_keeps_explicit_retry_accounting(self):
        helper = privacy_tests.FailurePrivacyTests()
        snapshot = helper.snapshot(
            "Generation failed: " + privacy_tests.CANARIES[0], historical=True, remount=True,
        )
        batch, settings = explicit_failures.PolicyFailureRunnerTests.batch(), GenerationSettings(variants=1)
        for debug in (False, True):
            with self.subTest(debug=debug), pending_download_test_directory() as temporary:
                run_dir = Path(temporary) / "run"
                error, state, session, adapter = helper.run_failure(snapshot, run_dir, debug=debug)
                self.assertIs(type(error), FlowFailureAttributionAmbiguous)
                persisted = json.loads(state.items[0].error.split(" [generation_observation=", 1)[1][:-1])
                self.assert_ambiguous_timing(persisted["post_start_ack_timing"])
                self.assertIsNone(persisted["start_ack_signal"])
                self.assertEqual(state.items[0].attempt_count, 1)
                self.assertEqual(state.items[0].automatic_policy_retry_count, 0)
                self.assertEqual(state.items[0].automatic_generic_failure_retry_count, 0)
                self.assertEqual(state.items[1].status, "pending")
                self.assertEqual(state.items[0].status, "failed")
                self.assertIsNone(state.items[0].generation_error_code)
                self.assertEqual(session._generation_interrupt_checkpoint["submission_state"], "ambiguous_or_sent")
                self.assertFalse(session._generation_interrupt_checkpoint["start_acknowledged"])
                before = (run_dir / "run_state.json").read_bytes()
                self.assertEqual(plan_sequential_batch(batch, run_dir, settings).orders[0].generation_action, "blocked_retry_required")
                with self.assertRaisesRegex(ValueError, "retry"):
                    execute_sequential_batch(batch, run_dir, adapter, settings, manage_flow_project=True)
                self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
                output = StringIO()
                with redirect_stdout(output):
                    app._print_existing_preview_state(batch, batch.items[0], run_dir)
                for text in (before.decode("utf-8"), str(error), output.getvalue(), json.dumps(session._post_start_ack_timing_snapshot())):
                    for canary in privacy_tests.CANARIES:
                        self.assertNotIn(canary, text)
                final, _ = execute_sequential_batch(
                    batch, run_dir, adapter, settings, manage_flow_project=True, retry_failed_generation=True,
                )
                self.assertEqual(final.flow_project, state.flow_project)
                self.assertEqual(final.items[0].attempt_count, 2)
                self.assertEqual(final.items[1].attempt_count, 1)
                self.assertEqual(final.items[0].automatic_generic_failure_retry_count, 0)
                self.assertEqual(final.items[0].automatic_policy_retry_count, 0)
                self.assertTrue(all(item.status == item.download_status == "completed" for item in final.items))


if __name__ == "__main__":
    unittest.main()
