import argparse
import signal
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import (
    FlowAutomationError, FlowGenericGenerationFailed, FlowHighDemand,
    FlowPolicyRejected, FlowSession, FlowUnusualActivity, FlowUsageLimitReached,
)
from nano_batch.batch import load_prompts_file
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.errors import FLOW_HIGH_DEMAND
from nano_batch.launcher import LauncherGenerationRetryRequired, _choose_retry_required_action, retry_required_generation_orders
from nano_batch.models import GenerationSettings
from nano_batch.runner import execute_download_one, execute_one, execute_sequential_batch, initialize_run_state, plan_sequential_batch
from nano_batch.state import RunStateStore
from tests.test_app import OfflinePromptAdapter
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE,
    UNUSUAL_ACTIVITY_FAILURE, _run_generation_failure_observer_scenario,
)
from tests.test_policy_prompt_recovery import OfflineSameSessionPolicyAdapter, PROJECT


class OfflineHighDemandAdapter(OfflineSameSessionPolicyAdapter):
    """Use the real session recovery loop with every browser operation replaced."""

    def _generate_one_in_open_session(self, *, item, settings, **kwargs):
        if item.order == 2 and self.steps and self.steps[0] == "high":
            self.steps.pop(0)
            current = RunStateStore(self.run_dir / "run_state.json").load().items[1]
            self.generations.append((item.order, item.prompt, current.attempt_count,
                                     current.automatic_policy_retry_count, self.enters,
                                     id(self.page), id(self._browser), id(self._page_cdp_session)))
            raise FlowHighDemand()
        return super()._generate_one_in_open_session(item=item, settings=settings, **kwargs)


class HighDemandDetectorTests(unittest.TestCase):
    def session(self, text=HIGH_DEMAND_FAILURE):
        session = FlowSession(PROJECT.project_url, Path("unused-profile"))
        session.page = MagicMock()
        session.page.evaluate.return_value = [text]
        return session

    def test_exact_card_has_distinct_code_and_manual_retry_message(self):
        session = self.session()
        with self.assertRaises(FlowHighDemand) as caught:
            session._raise_for_current_generation_attempt_failure()
        self.assertEqual(caught.exception.code, FLOW_HIGH_DEMAND)
        self.assertIn("not charged", str(caught.exception))
        self.assertIn("Retry", str(caught.exception))

    def test_multiline_whitespace_normalizes_without_loose_keywords(self):
        session = self.session("Failed\nFlow is experiencing high demand.\nPlease try again later.\nYou have not been charged for this generation.")
        with self.assertRaises(FlowHighDemand):
            session._raise_for_current_generation_attempt_failure()

    def test_partial_and_unrelated_failed_text_is_not_high_demand(self):
        for text in ("Failed", "Flow is experiencing high demand.", "You have not been charged for this generation.",
                     "Failed. Flow is experiencing high demand. You have not been charged for this generation.",
                     "Flow is experiencing high demand. Please try again later. You have not been charged for this generation.",
                     "Failed. Flow is experiencing high demand. Please try again later.",
                     "Failed. Unrelated request failed. You have not been charged for this generation."):
            with self.subTest(text=text):
                self.assertEqual(self.session(text)._current_generation_attempt_failures(), ())

    def test_added_current_card_detected_by_real_observer(self):
        result = _run_generation_failure_observer_scenario("high_added")
        self.assertEqual(result["failures"], [HIGH_DEMAND_FAILURE])
        self.assertEqual(result["diagnostics"]["last_failure_detection_reason"], "exact_high_demand_signature")

    def test_premounted_card_changed_after_start_is_current(self):
        result = _run_generation_failure_observer_scenario("high_reused")
        self.assertEqual(result["failures"], [HIGH_DEMAND_FAILURE])

    def test_historical_matching_card_is_ignored_by_real_observer(self):
        self.assertEqual(_run_generation_failure_observer_scenario("high_historical")["failures"], [])

    def test_nested_elements_do_not_hide_exact_card(self):
        result = _run_generation_failure_observer_scenario("high_added_nested_anchor")
        self.assertEqual(result["failures"], [HIGH_DEMAND_FAILURE])

    def test_partial_current_card_is_ignored_by_real_observer(self):
        self.assertEqual(_run_generation_failure_observer_scenario("high_partial")["failures"], [])

    def test_result_wait_exits_first_poll_without_timeout_discovery_or_download(self):
        session = self.session()
        with patch("nano_batch.adapters.flow.time.monotonic", side_effect=[0, 0]), \
             patch.object(session, "discover_candidates") as discover, \
             patch.object(session, "download_candidate") as download, \
             self.assertRaises(FlowHighDemand):
            session.wait_for_result(set(), 300, expected_count=1)
        self.assertEqual(session._generation_result_observation["polls"], 1)
        discover.assert_not_called()
        download.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_other_exact_failure_classifications_are_unchanged(self):
        for text, error in ((POLICY_FAILURE, FlowPolicyRejected), (UNUSUAL_ACTIVITY_FAILURE, FlowUnusualActivity),
                            (GENERIC_GENERATION_FAILURE, FlowGenericGenerationFailed),
                            ("You've reached your usage limit.", FlowUsageLimitReached)):
            with self.assertRaises(error):
                self.session(text)._raise_for_current_generation_attempt_failure()

    def test_latched_ctrl_c_takes_precedence_over_high_demand(self):
        session = self.session()
        with authoritative_cancellation_scope() as state:
            state.request()
            with self.assertRaises(KeyboardInterrupt):
                session.wait_for_result(set(), 300, expected_count=1)
        session.page.evaluate.assert_not_called()


class HighDemandRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompts = self.root / "prompts.txt"
        self.prompts.write_text("earlier prompt\noriginal prompt\nlater prompt\n", encoding="utf-8")
        self.batch = load_prompts_file(self.prompts)
        self.run_dir = self.root / "runs" / "high-demand"
        self.store = RunStateStore(self.run_dir / "run_state.json")
        self.settings = GenerationSettings(variants=1)
        state = initialize_run_state(self.batch, self.run_dir, require_flow_project=True)
        state.flow_project = PROJECT
        self.store.save(state)
        accepted = OfflinePromptAdapter()
        execute_one(self.batch, self.run_dir, 1, accepted, self.settings)
        execute_download_one(self.batch, self.run_dir, 1, accepted)

    def fail(self):
        adapter = OfflineHighDemandAdapter(self.run_dir, ["high", "success"])
        with self.assertRaises(FlowHighDemand) as caught:
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings, manage_flow_project=True)
        self.assertEqual(caught.exception.order, 2)
        return adapter

    def test_immediate_persisted_manual_failure_no_auto_retry_or_next_order(self):
        adapter = self.fail()
        state = self.store.load()
        target = state.items[1]
        self.assertEqual((target.status, target.generation_error_code, target.attempt_count), ("failed", FLOW_HIGH_DEMAND, 1))
        self.assertEqual((target.automatic_policy_retry_count, target.automatic_generic_failure_retry_count), (0, 0))
        self.assertEqual(len(adapter.generations), 1)
        self.assertEqual(adapter.steps, ["success"])
        self.assertEqual(state.items[2].attempt_count, 0)
        self.assertEqual(retry_required_generation_orders(state), (2,))
        self.assertEqual(target.candidates, [])
        self.assertIsNone(target.download_error)
        self.assertEqual(target.output_files, [])
        self.assertFalse(any(event[0] == "download" for event in adapter.events))

    def test_existing_manual_retry_menu_is_offered_without_dispatch(self):
        adapter = self.fail()
        lines = []
        choice = _choose_retry_required_action(retry_required_generation_orders(self.store.load()),
                                               input_fn=lambda _: "1", output=lines.append)
        self.assertEqual(choice, "retry")
        self.assertIn("1. Retry failed generation", lines)
        self.assertEqual(len(adapter.generations), 1)

    def test_normal_resume_remains_blocked_until_explicit_retry(self):
        self.fail()
        plan = plan_sequential_batch(self.batch, self.run_dir, self.settings)
        self.assertEqual(plan.orders[1].generation_action, "blocked_retry_required")
        adapter = OfflineHighDemandAdapter(self.run_dir, ["success"])
        with self.assertRaisesRegex(ValueError, "retry required"):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(adapter.generations, [])

    def test_manual_retry_preserves_order_prompt_project_and_completed_neighbors(self):
        before = self.store.load()
        asset = Path(before.items[0].candidates[0].local_path)
        asset_bytes = asset.read_bytes()
        self.fail()
        adapter = OfflineHighDemandAdapter(self.run_dir, ["success"])
        final, _ = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
                                            retry_failed_generation=True, manage_flow_project=True)
        self.assertEqual(adapter.generations[0][:2], (2, "original prompt"))
        self.assertEqual(final.flow_project, PROJECT)
        self.assertTrue(all(project == PROJECT for project in adapter.prepared))
        self.assertEqual(asdict(final.items[0]), asdict(before.items[0]))
        self.assertEqual(asset.read_bytes(), asset_bytes)
        self.assertEqual(final.items[1].attempt_count, 2)
        self.assertEqual((final.items[1].automatic_policy_retry_count, final.items[1].automatic_generic_failure_retry_count), (0, 0))

    def test_effective_source_prompt_and_consumed_budgets_survive_failure_and_retry(self):
        state = self.store.load()
        target = state.items[1]
        target.attempt_count, target.automatic_policy_retry_count = 4, 1
        target.automatic_generic_failure_retry_count = 2
        target.source_prompt, target.prompt = target.prompt, "edited Unicode 😀 prompt\nsecond line"
        state.version = 5
        self.store.save(state)
        original_file = self.prompts.read_bytes()
        self.fail()
        failed = self.store.load().items[1]
        self.assertEqual((failed.prompt, failed.source_prompt), (target.prompt, target.source_prompt))
        self.assertEqual((failed.automatic_policy_retry_count, failed.automatic_generic_failure_retry_count), (1, 2))
        adapter = OfflineHighDemandAdapter(self.run_dir, ["success"])
        final, _ = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
                                            retry_failed_generation=True, manage_flow_project=True)
        self.assertEqual(adapter.generations[0][:2], (2, target.prompt))
        self.assertEqual(final.items[1].source_prompt, target.source_prompt)
        self.assertEqual((final.items[1].automatic_policy_retry_count, final.items[1].automatic_generic_failure_retry_count), (1, 2))
        self.assertEqual(self.prompts.read_bytes(), original_file)

    def test_high_demand_never_calls_automatic_recovery_callbacks(self):
        adapter = OfflineHighDemandAdapter(self.run_dir, ["high"])
        callbacks = [MagicMock(), MagicMock(), MagicMock()]
        with self.assertRaises(FlowHighDemand):
            adapter.generate_one_with_unusual_activity_recovery(
                item=self.batch.items[1], settings=self.settings, forbidden_candidate_ids=frozenset(), project=PROJECT,
                retry_started=callbacks[0], generic_failure_retry_started=callbacks[1], policy_failure_retry_started=callbacks[2])
        for callback in callbacks:
            callback.assert_not_called()
        self.assertEqual(adapter.enters, 1)
        self.assertEqual(adapter.exits, 1)

    def test_launcher_routes_persisted_high_demand_into_existing_retry_menu(self):
        def delegated(_args):
            self.fail()
            raise FlowHighDemand()
        def launcher(_project_dir, execute, **_kwargs):
            with self.assertRaises(LauncherGenerationRetryRequired) as caught:
                execute(self.prompts, self.run_dir, False)
            self.assertEqual(caught.exception.orders, (2,))
            return 0
        args = argparse.Namespace(config="unused", url=PROJECT.project_url, profile_dir="unused", debug_address="127.0.0.1", debug_port=9222)
        with patch.object(app, "cmd_run_prompts_file", side_effect=delegated), patch.object(app, "run_launcher", side_effect=launcher):
            self.assertEqual(app.cmd_launcher(args), 0)

    def test_ctrl_c_instead_of_failure_propagates_without_retry(self):
        adapter = OfflineHighDemandAdapter(self.run_dir, ["interrupt", "success"])
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings, manage_flow_project=True)
        self.assertEqual(len(adapter.generations), 1)
        self.assertEqual(adapter.steps, ["success"])


if __name__ == "__main__":
    unittest.main()
