"""Synthetic window restoration and persisted no-resubmission regressions."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowFailureAttributionAmbiguous,
    FlowSession,
)
from nano_batch.models import Batch, BatchItem, GenerationSettings
from nano_batch.runner import (
    execute_one,
    execute_sequential_batch,
    plan_sequential_batch,
)
from nano_batch.state import RunStateStore
from tests.test_flow_projects import NEW_PROJECT, ProjectFlowAdapter
from tests.test_flow_production_window import (
    PROJECT_URL,
    WindowCdp,
    post_restore_dom,
    usable_geometry,
)


class RestoreMaximizedCdp(WindowCdp):
    """A minimized, previously maximized window restores straight to maximized."""

    def send(self, method, params=None):
        was_minimized = self.state == "minimized"
        result = super().send(method, params)
        if (
            method == "Browser.setWindowBounds"
            and params["bounds"]["windowState"] == "normal"
            and was_minimized
        ):
            self.state = "maximized"
        return result


class MinimizedRestoreTests(unittest.TestCase):
    def session(self):
        session = FlowSession(PROJECT_URL, Path("offline-profile-placeholder"))
        session.page = MagicMock()
        session.page.url = PROJECT_URL
        session.page.evaluate.return_value = usable_geometry()
        session._page_cdp_session = RestoreMaximizedCdp("minimized")
        return session

    def test_direct_restore_to_maximized_continues_same_acknowledged_attempt(self):
        session = self.session()
        with patch.object(
            session,
            "_post_window_recovery_dom_snapshot",
            return_value=post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
            ),
        ), patch.object(session, "submit_prompt") as submit, patch.object(
            session, "_begin_current_generation_attempt_observation"
        ) as begin, patch(
            "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
        ):
            event = session.ensure_production_window_usable(
                reason="generation_wait_minimized"
            )

        self.assertTrue(event["recovery_success"])
        self.assertEqual(event["after"]["window_state"], "maximized")
        self.assertTrue(event["start_acknowledged"])
        self.assertTrue(event["same_generation_attempt_active"])
        self.assertTrue(event["settle"]["exact_project"])
        self.assertFalse(event["settle"]["observer_rearmed"])
        states = [
            params["bounds"]["windowState"]
            for method, params in session._page_cdp_session.calls
            if method == "Browser.setWindowBounds"
        ]
        self.assertEqual(states, ["normal"])
        session.page.wait_for_timeout.assert_not_called()
        submit.assert_not_called()
        begin.assert_not_called()

    def test_direct_restore_still_waits_for_positive_final_viewport(self):
        session = self.session()
        empty = dict.fromkeys(usable_geometry(), 0)
        session.page.evaluate.side_effect = [
            usable_geometry(), empty, empty, usable_geometry(), usable_geometry(),
        ]
        with patch.object(
            session, "_post_window_recovery_dom_snapshot",
            return_value=post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
            ),
        ):
            event = session.ensure_production_window_usable(
                reason="generation_wait_minimized"
            )
        self.assertTrue(event["recovery_success"])
        session.page.wait_for_timeout.assert_called_once_with(100)

    def test_normal_transition_remains_strict_unless_restore_explicitly_allows_maximized(self):
        session = self.session()
        session._page_cdp_session.state = "maximized"
        with patch(
            "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
        ), self.assertRaisesRegex(FlowAutomationError, "did not verify as normal"):
            session._wait_for_production_window_state(
                17, "normal", require_usable_viewport=False,
            )

    def test_final_maximized_check_never_accepts_normal_state(self):
        session = self.session()
        session._page_cdp_session.state = "normal"
        with patch(
            "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
        ), self.assertRaisesRegex(FlowAutomationError, "did not verify as maximized"):
            session._wait_for_production_window_state(
                17, "maximized", require_usable_viewport=True,
                allow_maximized_restore=True,
            )

    def test_unrestored_and_ambiguous_native_states_fail_closed(self):
        for state in ("minimized", "fullscreen", "unknown"):
            with self.subTest(state=state):
                session = self.session()
                session._page_cdp_session = WindowCdp(state, apply_transitions=False)
                with patch(
                    "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
                ), patch.object(
                    session, "_wait_for_post_window_recovery_settle"
                ) as settle, self.assertRaisesRegex(
                    FlowAutomationError, "PRODUCTION_WINDOW_NORMALIZATION_FAILED"
                ) as raised:
                    session.ensure_production_window_usable(
                        reason="generation_wait_minimized"
                    )
                settle.assert_not_called()
                self.assertIn('"recovery_success":false', str(raised.exception))
                self.assertIn("Retry would submit a new generation", str(raised.exception))
                self.assertNotIn("then Resume current prompts", str(raised.exception))

    def test_maximized_with_unusable_viewport_is_not_success(self):
        session = self.session()
        session.page.evaluate.return_value = dict.fromkeys(usable_geometry(), 0)
        with patch(
            "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
        ), patch.object(
            session, "_wait_for_post_window_recovery_settle"
        ) as settle, self.assertRaisesRegex(
            FlowAutomationError, "PRODUCTION_WINDOW_NORMALIZATION_FAILED"
        ) as raised:
            session.ensure_production_window_usable(reason="generation_wait_minimized")
        settle.assert_not_called()
        self.assertIn('"failure_stage":"window_normalization"', str(raised.exception))
        self.assertIn('"window_state":"maximized"', str(raised.exception))

    def test_delayed_restore_uses_existing_bounded_poll(self):
        session = self.session()
        send = session._page_cdp_session.send
        reads_after_restore = 0

        def delayed_send(method, params=None):
            nonlocal reads_after_restore
            result = send(method, params)
            if method == "Browser.getWindowBounds" and session._page_cdp_session.state == "maximized":
                reads_after_restore += 1
                if reads_after_restore == 1:
                    return {"bounds": {"windowState": "minimized"}}
            return result

        with patch.object(
            session._page_cdp_session, "send", side_effect=delayed_send
        ), patch.object(
            session, "_post_window_recovery_dom_snapshot",
            return_value=post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
            ),
        ):
            event = session.ensure_production_window_usable(reason="generation_wait_minimized")
        self.assertTrue(event["recovery_success"])
        session.page.wait_for_timeout.assert_called_once_with(100)

    def test_direct_restore_rearms_only_existing_observer_with_preserved_provenance(self):
        session = self.session()
        inactive = post_restore_dom(attempt_observer_present=True)
        active = post_restore_dom(attempt_observer_present=True, attempt_observer_active=True)
        with patch.object(
            session, "_post_window_recovery_dom_snapshot", side_effect=[inactive, active]
        ), patch.object(
            session, "_arm_current_generation_attempt_observation"
        ) as arm, patch.object(
            session, "_begin_current_generation_attempt_observation"
        ) as begin:
            event = session.ensure_production_window_usable(reason="generation_wait_minimized")
        self.assertTrue(event["settle"]["observer_rearmed"])
        arm.assert_called_once_with(preserve_attempt=True)
        begin.assert_not_called()

    def test_direct_restore_does_not_replace_lost_or_disputed_observer(self):
        for evidence in (
            post_restore_dom(),
            post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
                attempt_observer_provenance_lost=True,
            ),
        ):
            with self.subTest(evidence=evidence):
                session = self.session()
                with patch.object(
                    session, "_post_window_recovery_dom_snapshot", return_value=evidence
                ), patch.object(
                    session, "_begin_current_generation_attempt_observation"
                ) as begin, patch.object(
                    session, "submit_prompt"
                ) as submit, self.assertRaises(FlowFailureAttributionAmbiguous):
                    session.ensure_production_window_usable(reason="generation_wait_minimized")
                begin.assert_not_called()
                submit.assert_not_called()

    def test_direct_restore_to_wrong_project_fails_semantic_settle(self):
        session = self.session()
        session.page.url = "https://flow.google.com/project/00000000-0000-4000-8000-000000000009"
        with patch.object(
            session, "_post_window_recovery_dom_snapshot",
            return_value=post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
            ),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0, 6]
        ), self.assertRaisesRegex(
            FlowAutomationError, "PRODUCTION_WINDOW_NORMALIZATION_FAILED"
        ) as raised:
            session.ensure_production_window_usable(reason="generation_wait_minimized")
        self.assertIn('"failure_stage":"post_restore_settle"', str(raised.exception))

    def test_generation_completes_after_direct_restore_without_another_start_or_baseline(self):
        session = self.session()
        session._page_cdp_session.state = "maximized"
        historical_id = "00000000-0000-4000-8000-000000000004"
        owned_id = "00000000-0000-4000-8000-000000000005"
        current_id = "00000000-0000-4000-8000-000000000006"
        before = [{"fingerprint": "old", "kind": "rendered-image", "mediaId": historical_id}]
        after = [
            {"fingerprint": "remounted-old", "kind": "rendered-image", "mediaId": historical_id},
            {"fingerprint": "owned", "kind": "rendered-image", "mediaId": owned_id},
            {"fingerprint": "current", "kind": "rendered-image", "mediaId": current_id},
        ]
        for asset in before + after:
            asset.update(width=16, height=9)

        def acknowledged_submit(*_args, **_kwargs):
            session._page_cdp_session.state = "minimized"
            return {"acknowledgement": {"signal": "new_generation_progress"}, "ack_elapsed_ms": 10}

        with patch.object(
            session, "configure_generation", return_value={"configured_variants": 1}
        ), patch.object(
            session, "_asset_snapshot", side_effect=[before, after, after, after]
        ) as assets, patch.object(
            session, "submit_prompt", side_effect=acknowledged_submit
        ) as submit, patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ), patch.object(
            session, "_post_window_recovery_dom_snapshot",
            return_value=post_restore_dom(
                attempt_observer_present=True, attempt_observer_active=True,
            ),
        ), patch.object(
            session, "_stop_current_generation_attempt_observation"
        ), patch.object(
            session, "_begin_current_generation_attempt_observation"
        ) as begin:
            result = session._generate_one_in_open_session(
                item=BatchItem(order=1, visual_id="visual_1", prompt="synthetic still life"),
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset({owned_id}),
            )
        self.assertEqual([candidate.candidate_id for candidate in result.candidates], [current_id])
        submit.assert_called_once()
        self.assertEqual(submit.call_args.kwargs["baseline_candidate_ids"], {historical_id})
        self.assertEqual(assets.call_count, 4)  # One baseline, then three stable result polls.
        begin.assert_not_called()
        events = json.loads(result.details)["window_recovery_events"]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["recovery_success"])


class InterruptedGenerationProtectionTests(unittest.TestCase):
    @staticmethod
    def batch(count=1):
        return Batch(
            batch_id="sha256:offline-minimized-recovery", source_schema="test",
            items=tuple(
                BatchItem(order=i, visual_id=f"visual_{i}", prompt=f"synthetic still life {i}")
                for i in range(1, count + 1)
            ),
        )

    def test_acknowledged_window_failure_is_persisted_and_resume_never_resubmits(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "synthetic-run"
            batch = self.batch()
            adapter = ProjectFlowAdapter(
                run_dir, generation_error=FlowAutomationError(
                    'PRODUCTION_WINDOW_NORMALIZATION_FAILED: recovery={"start_acknowledged":true}'
                ),
            )
            with self.assertRaises(FlowAutomationError):
                execute_sequential_batch(
                    batch, run_dir, adapter, GenerationSettings(variants=1), manage_flow_project=True,
                )
            store = RunStateStore(run_dir / "run_state.json")
            state = store.load()
            item = state.items[0]
            self.assertEqual(item.status, "failed")
            self.assertEqual(item.attempt_count, 1)
            self.assertIn('"start_acknowledged":true', item.error)
            self.assertEqual(item.candidates, [])
            self.assertEqual(item.output_files, [])
            self.assertEqual(item.automatic_policy_retry_count, 0)
            self.assertEqual(item.automatic_generic_failure_retry_count, 0)
            self.assertIsNone(item.generation_error_code)
            self.assertEqual(state.flow_project, NEW_PROJECT)
            for status in ("failed", "running"):
                with self.subTest(status=status):
                    item.status = status
                    store.save(state)
                    saved = store.path.read_bytes()
                    plan = plan_sequential_batch(batch, run_dir, GenerationSettings(variants=1))
                    self.assertEqual(plan.orders[0].generation_action, "blocked_retry_required")
                    self.assertEqual(plan.orders[0].candidate_actions, ())
                    resume = ProjectFlowAdapter(run_dir)
                    with self.assertRaises(ValueError):
                        execute_sequential_batch(
                            batch, run_dir, resume, GenerationSettings(variants=1), manage_flow_project=True,
                        )
                    with self.assertRaises(ValueError):
                        execute_one(batch, run_dir, 1, resume, GenerationSettings(variants=1))
                    self.assertEqual(resume.events, [])
                    self.assertEqual(store.path.read_bytes(), saved)
                    self.assertEqual(store.load().items[0].attempt_count, 1)

    def test_blocked_interrupted_order_preserves_prior_completed_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "synthetic-run"
            batch = self.batch(2)
            first = ProjectFlowAdapter(run_dir)
            generate = first.generate_one

            def generation(*, item, **kwargs):
                if item.order == 2:
                    raise FlowAutomationError("PRODUCTION_WINDOW_NORMALIZATION_FAILED: acknowledged attempt")
                return generate(item=item, **kwargs)

            first.generate_one = generation
            with self.assertRaises(FlowAutomationError):
                execute_sequential_batch(
                    batch, run_dir, first, GenerationSettings(variants=1), manage_flow_project=True,
                )
            before = {str(p.relative_to(run_dir)): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
            resume = ProjectFlowAdapter(run_dir)
            with self.assertRaises(ValueError):
                execute_sequential_batch(
                    batch, run_dir, resume, GenerationSettings(variants=1), manage_flow_project=True,
                )
            self.assertEqual(resume.events, [])
            self.assertEqual(before, {
                str(p.relative_to(run_dir)): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()
            })
            saved = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(saved.items[0].download_status, "completed")
            self.assertEqual(saved.items[1].attempt_count, 1)

    def test_persisted_completed_uuid_allows_download_only_resume_without_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "synthetic-run"
            batch = self.batch()
            first = ProjectFlowAdapter(run_dir, download_error=RuntimeError("synthetic interruption"))
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                execute_sequential_batch(
                    batch, run_dir, first, GenerationSettings(variants=1), manage_flow_project=True,
                )
            original = RunStateStore(run_dir / "run_state.json").load().items[0]
            self.assertEqual(original.status, "completed")
            identity = original.candidates[0].candidate_id
            resume = ProjectFlowAdapter(run_dir)
            state, _ = execute_sequential_batch(
                batch, run_dir, resume, GenerationSettings(variants=1), manage_flow_project=True,
            )
            self.assertNotIn(("generate", 1), resume.events)
            self.assertIn(("download", 1), resume.events)
            self.assertEqual(state.items[0].candidates[0].candidate_id, identity)
            self.assertEqual(state.items[0].attempt_count, original.attempt_count)
            self.assertEqual(state.items[0].download_status, "completed")
            self.assertEqual(state.flow_project, NEW_PROJECT)
