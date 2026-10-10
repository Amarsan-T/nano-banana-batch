import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowPolicyRejected,
    FlowSession,
    FlowUnusualActivity,
    FlowUsageLimitReached,
)
from nano_batch.models import BatchItem, DiscoveredCandidate, GenerationSettings


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000003"


class WindowCdp:
    def __init__(self, state="maximized", *, apply_transitions=True, window_id=17):
        self.state = state
        self.apply_transitions = apply_transitions
        self.window_id = window_id
        self.calls = []

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": self.window_id}
        if method == "Browser.getWindowBounds":
            self._assert_window(params)
            return {"bounds": {"windowState": self.state}}
        if method == "Browser.setWindowBounds":
            self._assert_window(params)
            if self.apply_transitions:
                self.state = params["bounds"]["windowState"]
            return {}
        raise AssertionError(f"unexpected CDP method: {method}")

    def _assert_window(self, params):
        if params is None or params.get("windowId") != self.window_id:
            raise AssertionError("production code targeted a different native window")


def usable_geometry():
    return {
        "inner_width": 1280,
        "inner_height": 720,
        "viewport_width": 1280,
        "viewport_height": 650,
    }


def actionable_more_options_snapshot():
    box = {"x": 100.0, "y": 80.0, "width": 28.0, "height": 28.0}
    return {
        "connected": True,
        "visible": True,
        "disabled": False,
        "aria_disabled": None,
        "pointer_events": "auto",
        "opacity": "1",
        "visibility": "visible",
        "display": "block",
        "center_top_belongs_to_control": True,
        "bounding_box_samples": [dict(box), dict(box)],
        "hit_test_probes": [],
    }


def post_restore_dom(**overrides):
    values = {
        "ready_state": "complete",
        "document_connected": True,
        "body_connected": True,
        "body_child_count": 4,
        "generic_control_count": 3,
        "prompt_surface_count": 1,
        "progress_surface_count": 0,
        "result_tile_count": 0,
        "exact_candidate_match_count": 0,
        "attempt_observer_present": False,
        "attempt_observer_active": False,
    }
    values.update(overrides)
    return values


def settle_result(**overrides):
    values = {
        "outcome": "ready",
        "operation_phase": "generation",
        "elapsed_ms": 25,
        "poll_count": 2,
        "same_generation_attempt_active": False,
        "start_acknowledged": False,
        "observer_rearmed": False,
        "exact_project": True,
        "window": {
            "window_state": "maximized",
            "viewport": {"width": 1280, "height": 650},
        },
        "dom": post_restore_dom(),
    }
    values.update(overrides)
    return values


class ProductionWindowTests(unittest.TestCase):
    def session(self, state="maximized", *, apply_transitions=True):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = usable_geometry()
        session.page = page
        cdp = WindowCdp(state, apply_transitions=apply_transitions)
        session._page_cdp_session = cdp
        return session, page, cdp

    def test_already_maximized_usable_window_is_a_noop(self):
        session, page, cdp = self.session()

        with patch.object(
            session, "_wait_for_post_window_recovery_settle"
        ) as settle:
            result = session.ensure_production_window_usable()

        self.assertFalse(result["recovery_required"])
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in cdp.calls))
        page.wait_for_timeout.assert_not_called()
        settle.assert_not_called()

    def test_normal_window_is_maximized_and_verified_on_same_window_id(self):
        session, _page, cdp = self.session("normal")

        with patch.object(
            session,
            "_wait_for_post_window_recovery_settle",
            return_value=settle_result(),
        ) as settle:
            result = session.ensure_production_window_usable()

        self.assertTrue(result["recovery_success"])
        self.assertEqual(result["classification"], "WINDOW_RECOVERY")
        self.assertEqual(result["settle"]["outcome"], "ready")
        self.assertEqual(cdp.state, "maximized")
        sets = [params for method, params in cdp.calls if method == "Browser.setWindowBounds"]
        self.assertEqual(sets, [{"windowId": 17, "bounds": {"windowState": "maximized"}}])
        self.assertTrue(all(params is None or params.get("windowId") == 17 for _, params in cdp.calls))
        settle.assert_called_once_with(
            operation_phase="generation",
            candidate_id=None,
            require_semantic_surface=True,
            same_generation_attempt_active=False,
            start_acknowledged=False,
        )

    def test_minimized_window_uses_verified_normal_then_maximized_transition(self):
        session, _page, cdp = self.session("minimized")

        with patch.object(
            session,
            "_wait_for_post_window_recovery_settle",
            return_value=settle_result(),
        ):
            result = session.ensure_production_window_usable()

        states = [
            params["bounds"]["windowState"]
            for method, params in cdp.calls
            if method == "Browser.setWindowBounds"
        ]
        self.assertEqual(states, ["normal", "maximized"])
        self.assertEqual(cdp.state, "maximized")
        self.assertEqual(result["operation_phase"], "generation")
        self.assertFalse(result["same_generation_attempt_active"])
        self.assertFalse(result["start_acknowledged"])

    def test_unverified_transition_fails_closed_with_safe_evidence(self):
        session, _page, _cdp = self.session("normal", apply_transitions=False)

        with patch(
            "nano_batch.adapters.flow._PRODUCTION_WINDOW_VERIFY_ATTEMPTS", 2
        ), self.assertRaisesRegex(
            FlowAutomationError, "PRODUCTION_WINDOW_NORMALIZATION_FAILED"
        ) as raised:
            session.ensure_production_window_usable()

        message = str(raised.exception)
        self.assertIn('"recovery_success":false', message)
        self.assertNotIn("windowId", message)

    def test_failed_post_restore_settle_reports_safe_operation_diagnostics(self):
        session, _page, _cdp = self.session("minimized")
        with patch.object(
            session,
            "_wait_for_post_window_recovery_settle",
            side_effect=FlowAutomationError("POST_WINDOW_RECOVERY_NOT_READY"),
        ), self.assertRaisesRegex(
            FlowAutomationError, "PRODUCTION_WINDOW_NORMALIZATION_FAILED"
        ) as raised:
            session.ensure_production_window_usable(
                reason="generation_wait_minimized"
            )

        message = str(raised.exception)
        self.assertIn('"classification":"WINDOW_RECOVERY"', message)
        self.assertIn('"failure_stage":"post_restore_settle"', message)
        self.assertIn('"operation_phase":"result_wait"', message)
        self.assertIn('"same_generation_attempt_active":true', message)
        self.assertIn('"start_acknowledged":true', message)
        self.assertIn('"window_state":"maximized"', message)

    def test_download_recovery_settle_keeps_exact_candidate_uuid(self):
        session, _page, _cdp = self.session("normal")
        with patch.object(
            session,
            "_wait_for_post_window_recovery_settle",
            return_value=settle_result(operation_phase="download"),
        ) as settle:
            event = session.ensure_production_window_usable(
                reason="download_control_zero_geometry",
                candidate_id=CANDIDATE_ID,
            )

        self.assertEqual(event["candidate_id"], CANDIDATE_ID)
        self.assertEqual(event["operation_phase"], "download")
        settle.assert_called_once_with(
            operation_phase="download",
            candidate_id=CANDIDATE_ID,
            require_semantic_surface=True,
            same_generation_attempt_active=False,
            start_acknowledged=False,
        )

    def test_window_control_contains_no_focus_or_front_or_os_input_path(self):
        source = "\n".join(
            (
                inspect.getsource(FlowSession.ensure_production_window_usable),
                inspect.getsource(FlowSession._wait_for_production_window_state),
            )
        ).casefold()
        for forbidden in (
            "bringtofront",
            "bring_to_front",
            "activatewindow",
            "setforegroundwindow",
            "keyboard.press",
            "mouse.",
        ):
            self.assertNotIn(forbidden, source)

    def test_production_boundaries_all_call_window_verification(self):
        for method in (
            FlowSession.create_flow_project,
            FlowSession.prepare_flow_project,
            FlowSession.download_candidate,
        ):
            with self.subTest(method=method.__name__):
                self.assertIn(
                    "ensure_production_window_usable",
                    inspect.getsource(method),
                )
        self.assertIn(
            "_generate_one_in_open_session",
            inspect.getsource(FlowSession.generate_one),
        )
        self.assertIn(
            "ensure_production_window_usable",
            inspect.getsource(FlowSession._generate_one_in_open_session),
        )

    def test_viewport_read_remount_is_retried_during_window_verification(self):
        session, page, cdp = self.session("maximized")
        page.evaluate.side_effect = [RuntimeError("document remounted"), usable_geometry()]

        snapshot = session._wait_for_production_window_state(
            cdp.window_id,
            "maximized",
            require_usable_viewport=True,
        )

        self.assertEqual(snapshot["window_state"], "maximized")
        self.assertTrue(session._production_viewport_usable(snapshot["viewport"]))
        page.wait_for_timeout.assert_called_once_with(100)

    def test_post_restore_settle_tolerates_angular_remount_then_reacquires_editor(self):
        session, page, _cdp = self.session("maximized")
        page.url = PROJECT_URL
        with patch.object(
            session,
            "_production_window_snapshot",
            return_value={
                "window_id": 17,
                "window_state": "maximized",
                "viewport": usable_geometry(),
            },
        ), patch.object(
            session,
            "_post_window_recovery_dom_snapshot",
            side_effect=[RuntimeError("Angular remount"), post_restore_dom()],
        ) as dom_probe, patch(
            "nano_batch.adapters.flow.time.monotonic",
            return_value=0.0,
        ):
            result = session._wait_for_post_window_recovery_settle(
                operation_phase="generation",
                candidate_id=None,
                require_semantic_surface=True,
                same_generation_attempt_active=False,
                start_acknowledged=False,
                timeout_seconds=1.0,
                poll_interval_ms=50,
            )

        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(result["poll_count"], 2)
        self.assertEqual(dom_probe.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(50)

    def test_result_wait_settle_rearms_only_missing_observer_for_same_attempt(self):
        session, page, _cdp = self.session("maximized")
        page.url = PROJECT_URL
        inactive = post_restore_dom(
            prompt_surface_count=0,
            progress_surface_count=1,
            attempt_observer_present=False,
            attempt_observer_active=False,
        )
        active = {
            **inactive,
            "attempt_observer_present": True,
            "attempt_observer_active": True,
        }
        with patch.object(
            session,
            "_production_window_snapshot",
            return_value={
                "window_id": 17,
                "window_state": "maximized",
                "viewport": usable_geometry(),
            },
        ), patch.object(
            session,
            "_post_window_recovery_dom_snapshot",
            side_effect=[inactive, active],
        ), patch.object(
            session, "_begin_current_generation_attempt_observation"
        ) as begin, patch.object(
            session, "_arm_current_generation_attempt_observation"
        ) as arm, patch(
            "nano_batch.adapters.flow.time.monotonic",
            return_value=0.0,
        ):
            result = session._wait_for_post_window_recovery_settle(
                operation_phase="result_wait",
                candidate_id=None,
                require_semantic_surface=True,
                same_generation_attempt_active=True,
                start_acknowledged=True,
                timeout_seconds=1.0,
                poll_interval_ms=50,
            )

        self.assertTrue(result["observer_rearmed"])
        self.assertTrue(result["same_generation_attempt_active"])
        self.assertTrue(result["start_acknowledged"])
        begin.assert_called_once_with()
        arm.assert_called_once_with()

    def test_post_restore_settle_timeout_is_safe_and_fail_closed(self):
        session, page, _cdp = self.session("maximized")
        page.url = PROJECT_URL
        remounting = post_restore_dom(
            document_connected=False,
            body_connected=False,
            body_child_count=0,
            generic_control_count=0,
            prompt_surface_count=0,
        )
        with patch.object(
            session,
            "_production_window_snapshot",
            return_value={
                "window_id": 17,
                "window_state": "maximized",
                "viewport": usable_geometry(),
            },
        ), patch.object(
            session,
            "_post_window_recovery_dom_snapshot",
            return_value=remounting,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.2],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "POST_WINDOW_RECOVERY_NOT_READY.*same_generation_attempt_active",
        ) as raised:
            session._wait_for_post_window_recovery_settle(
                operation_phase="result_wait",
                candidate_id=None,
                require_semantic_surface=True,
                same_generation_attempt_active=True,
                start_acknowledged=True,
                timeout_seconds=0.1,
                poll_interval_ms=50,
            )

        message = str(raised.exception)
        self.assertNotIn("private prompt content", message.casefold())
        self.assertNotIn("token", message.casefold())

    def test_recovery_helpers_contain_no_start_retry_project_or_focus_path(self):
        source = "\n".join(
            (
                inspect.getsource(FlowSession._wait_for_post_window_recovery_settle),
                inspect.getsource(FlowSession._rearm_generation_observer_after_window_recovery),
                inspect.getsource(FlowSession.ensure_production_window_usable),
            )
        ).casefold()
        for forbidden in (
            "submit_prompt",
            "generate_one",
            "attempt_count",
            "automatic_policy_retry_count",
            "automatic_generic_failure_retry_count",
            "create_flow_project",
            "bringtofront",
            "bring_to_front",
            "setforegroundwindow",
        ):
            self.assertNotIn(forbidden, source)


class GenerationWindowWatchdogTests(unittest.TestCase):
    def test_minimize_before_start_recovers_and_submits_exactly_once(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        recovery = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": True,
            "recovery_success": True,
        }
        no_recovery = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": False,
            "recovery_success": True,
        }
        candidate = DiscoveredCandidate(
            "rendered-image", candidate_id="same-attempt-candidate"
        )
        with patch.object(
            FlowSession, "__enter__", return_value=session
        ), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session,
            "ensure_production_window_usable",
            side_effect=[recovery, no_recovery],
        ) as normalize, patch.object(
            session, "configure_generation", return_value={"configured_variants": 1}
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session,
            "submit_prompt",
            return_value={
                "acknowledgement": {"signal": "new_generation_progress"},
                "ack_elapsed_ms": 50,
            },
        ) as submit, patch.object(
            session, "wait_for_result", return_value=(candidate,)
        ) as result_wait, patch.object(
            session, "_stop_current_generation_attempt_observation"
        ):
            result = session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )

        self.assertEqual(result.candidates, (candidate,))
        self.assertEqual(normalize.call_count, 2)
        submit.assert_called_once()
        result_wait.assert_called_once()

    def test_repeated_usable_watchdog_polls_never_issue_maximize(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = usable_geometry()
        session.page = page
        cdp = WindowCdp("maximized")
        session._page_cdp_session = cdp

        self.assertIsNone(
            session._generation_window_watchdog(recovery_count=0)
        )
        self.assertIsNone(
            session._generation_window_watchdog(recovery_count=0)
        )
        self.assertFalse(any(method == "Browser.setWindowBounds" for method, _ in cdp.calls))

    def test_wait_watchdog_recovers_once_without_touching_candidate_baseline(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session._page_cdp_session = object()
        candidate = DiscoveredCandidate("rendered-image", candidate_id="current-v01")
        recovery = {
            "recovery_required": True,
            "recovery_success": True,
            "reason": "generation_wait_minimized",
        }
        baseline = {"existing-fingerprint"}
        with patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ), patch.object(
            session,
            "_generation_window_watchdog",
            side_effect=[recovery, None, None, None],
        ) as watchdog, patch.object(
            session,
            "discover_candidates",
            side_effect=[(), (candidate,), (candidate,), (candidate,)],
        ) as discover, patch.object(
            session, "_progress_signals", return_value=()
        ), patch(
            "nano_batch.adapters.flow._GENERATION_WINDOW_WATCHDOG_INTERVAL_SECONDS",
            0.0,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0] * 5
        ):
            result = session.wait_for_result(baseline, 1, expected_count=1)

        self.assertEqual(result, (candidate,))
        self.assertEqual(watchdog.call_count, 4)
        self.assertEqual(watchdog.call_args_list[0].kwargs["recovery_count"], 0)
        self.assertEqual(watchdog.call_args_list[1].kwargs["recovery_count"], 1)
        self.assertTrue(
            all(call.args[0] is baseline for call in discover.call_args_list)
        )
        self.assertEqual(
            session._generation_result_observation["window_recovery_events"],
            [recovery],
        )
        self.assertEqual(
            session._generation_result_observation["window_recovery_count"], 1
        )

    def test_repeated_distinct_minimize_events_each_get_one_bounded_recovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        bad = {"window_state": "minimized", "viewport": usable_geometry()}
        recovery = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": True,
            "recovery_success": True,
        }
        with patch.object(
            session, "_production_window_snapshot", return_value=bad
        ), patch.object(
            session, "ensure_production_window_usable", return_value=recovery
        ) as recover:
            first = session._generation_window_watchdog(recovery_count=0)
            second = session._generation_window_watchdog(recovery_count=1)

        self.assertIs(first, recovery)
        self.assertIs(second, recovery)
        self.assertEqual(recover.call_count, 2)
        recover.assert_called_with(reason="generation_wait_minimized")

    def test_explicit_policy_failure_after_restore_still_wins_before_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session._page_cdp_session = object()
        recovery = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": True,
            "recovery_success": True,
        }
        with patch.object(
            session,
            "_raise_for_current_generation_attempt_failure",
            side_effect=FlowPolicyRejected(),
        ) as explicit_failure, patch.object(
            session, "_generation_window_watchdog", return_value=recovery
        ), patch.object(
            session, "discover_candidates"
        ) as discover, patch(
            "nano_batch.adapters.flow._GENERATION_WINDOW_WATCHDOG_INTERVAL_SECONDS",
            0.0,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaises(FlowPolicyRejected):
            session.wait_for_result(set(), 10, expected_count=1)

        explicit_failure.assert_called_once_with()
        discover.assert_not_called()

    def test_other_explicit_failures_after_restore_keep_their_classification(self):
        cases = (
            (
                "unusual_activity",
                FlowUnusualActivity(),
                FlowUnusualActivity,
            ),
            ("usage_limit", FlowUsageLimitReached(), FlowUsageLimitReached),
        )
        for name, failure, expected_type in cases:
            with self.subTest(name=name):
                session = FlowSession(PROJECT_URL, Path("profile"))
                session.page = MagicMock()
                session._page_cdp_session = object()
                recovery = {
                    "classification": "WINDOW_RECOVERY",
                    "recovery_required": True,
                    "recovery_success": True,
                }
                with patch.object(
                    session,
                    "_raise_for_current_generation_attempt_failure",
                    side_effect=failure,
                ), patch.object(
                    session, "_generation_window_watchdog", return_value=recovery
                ) as watchdog, patch.object(
                    session, "discover_candidates"
                ) as discover, patch(
                    "nano_batch.adapters.flow.time.monotonic",
                    side_effect=[0.0, 0.0],
                ), self.assertRaises(expected_type):
                    session.wait_for_result(set(), 10, expected_count=1)

                watchdog.assert_called_once_with(recovery_count=0)
                discover.assert_not_called()


class CandidateToolbarWindowRecoveryTests(unittest.TestCase):
    def test_zero_geometry_recovery_reacquires_same_uuid_and_continues(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        image, tile, stale_control, fresh_control = (
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        tile.get_by_role.return_value.all.return_value = [stale_control]
        zero = {
            "connected": True,
            "visible": False,
            "enabled": True,
            "bounding_box": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
        snapshot = actionable_more_options_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        recovery = {"recovery_required": True, "recovery_success": True}
        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ), patch.object(
            session, "_exact_candidate_image_tile", return_value=(image, tile)
        ), patch.object(
            session, "_live_control_facts", return_value=zero
        ), patch.object(
            session,
            "_recover_candidate_more_options_after_window_normalization",
            return_value=(fresh_control, {"connected": True}, recovery, "control_actionable"),
        ) as recover, patch.object(
            session, "_inspect_candidate_more_options", return_value=snapshot
        ), patch.object(
            session, "_current_candidate_more_options_hit", return_value=box
        ):
            selected, selected_snapshot, _box = session._reveal_candidate_options(
                CANDIDATE_ID, image, tile
            )

        self.assertIs(selected, fresh_control)
        recover.assert_called_once_with(
            CANDIDATE_ID,
            reason="download_control_zero_geometry",
        )
        self.assertEqual(selected_snapshot["window_layout_recovery"], recovery)

    def test_absent_control_gets_one_same_uuid_recovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        image, tile = MagicMock(), MagicMock()
        tile.get_by_role.return_value.all.return_value = []
        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ), patch.object(
            session, "_exact_candidate_image_tile", return_value=(image, tile)
        ), patch.object(
            session,
            "_recover_candidate_more_options_after_window_normalization",
            return_value=(
                None,
                {},
                {"recovery_required": True, "recovery_success": True},
                "control_not_present",
            ),
        ) as recover, self.assertRaisesRegex(
            FlowAutomationError, "CANDIDATE_MORE_OPTIONS_NOT_PRESENT"
        ):
            session._reveal_candidate_options(CANDIDATE_ID, image, tile)

        recover.assert_called_once_with(
            CANDIDATE_ID,
            reason="download_control_absent",
        )

    def test_zero_geometry_after_one_recovery_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        image, tile, control = MagicMock(), MagicMock(), MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        zero = {
            "connected": True,
            "visible": False,
            "enabled": True,
            "bounding_box": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ), patch.object(
            session, "_exact_candidate_image_tile", return_value=(image, tile)
        ), patch.object(
            session, "_live_control_facts", return_value=zero
        ), patch.object(
            session,
            "_recover_candidate_more_options_after_window_normalization",
            return_value=(
                None,
                zero,
                {"recovery_required": True, "recovery_success": True},
                "control_zero_geometry",
            ),
        ) as recover, self.assertRaisesRegex(
            FlowAutomationError, "CANDIDATE_MORE_OPTIONS_NOT_ACTIONABLE"
        ):
            session._reveal_candidate_options(CANDIDATE_ID, image, tile)
        recover.assert_called_once()

    def test_recovery_helper_uses_only_fresh_same_uuid_dom(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        image, tile, control = MagicMock(), MagicMock(), MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        facts = {
            "connected": True,
            "visible": True,
            "enabled": True,
            "bounding_box": {"x": 1, "y": 2, "width": 28, "height": 28},
        }
        recovery = {"recovery_required": True, "recovery_success": True}
        with patch.object(
            session, "ensure_production_window_usable", return_value=recovery
        ) as normalize, patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ) as reacquire, patch.object(
            session, "_exact_candidate_image_tile", return_value=(image, tile)
        ) as exact, patch.object(
            session, "_live_control_facts", return_value=facts
        ):
            selected, selected_facts, _, outcome = (
                session._recover_candidate_more_options_after_window_normalization(
                    CANDIDATE_ID,
                    reason="download_control_absent",
                )
            )

        self.assertIs(selected, control)
        self.assertEqual(selected_facts, facts)
        self.assertEqual(outcome, "control_actionable")
        normalize.assert_called_once_with(
            reason="download_control_absent", candidate_id=CANDIDATE_ID
        )
        reacquire.assert_called_once_with(CANDIDATE_ID, timeout_ms=500)
        exact.assert_called_once_with(
            CANDIDATE_ID, timeout_ms=500, poll_interval_ms=50
        )

    def test_candidate_recovery_adds_no_unsafe_or_generation_path(self):
        source = "\n".join(
            (
                inspect.getsource(
                    FlowSession._recover_candidate_more_options_after_window_normalization
                ),
                inspect.getsource(FlowSession._reveal_candidate_options),
            )
        ).casefold()
        for forbidden in (
            "force=",
            "dispatch_event",
            "bringtofront",
            "bring_to_front",
            "submit_prompt",
            "generate_one",
        ):
            self.assertNotIn(forbidden, source)


class ExactUnusualActivityFailureTests(unittest.TestCase):
    def test_exact_message_is_a_distinct_current_attempt_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = [
            "Failed. We noticed some unusual activity. "
            "Please visit the Help Center for more information. "
            "You have not been charged for this generation."
        ]
        session.page = page

        with self.assertRaisesRegex(
            FlowUnusualActivity, "FLOW_UNUSUAL_ACTIVITY"
        ):
            session._raise_for_current_generation_attempt_failure()

    def test_partial_unusual_activity_text_is_not_broadly_classified(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = ["We noticed some unusual activity"]
        session.page = page

        self.assertEqual(session._current_generation_attempt_failures(), ())

    def test_result_wait_stops_on_first_poll_without_candidate_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        exact = (
            "Failed. We noticed some unusual activity. "
            "Please visit the Help Center for more information. "
            "You have not been charged for this generation."
        )
        with patch.object(
            session, "_current_generation_attempt_failures", return_value=(exact,)
        ), patch.object(
            session, "discover_candidates"
        ) as discover, patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaisesRegex(
            FlowUnusualActivity, "FLOW_UNUSUAL_ACTIVITY"
        ):
            session.wait_for_result(set(), 10, expected_count=1)
        discover.assert_not_called()

    def test_observer_requires_all_exact_failure_clauses(self):
        source = inspect.getsource(
            FlowSession._begin_current_generation_attempt_observation
        ).casefold()
        self.assertIn("failed", source)
        self.assertIn("we noticed some unusual activity", source)
        self.assertIn("please visit the help center for more information", source)
        self.assertIn("you have not been charged for this generation", source)


if __name__ == "__main__":
    unittest.main()
