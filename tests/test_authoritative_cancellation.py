import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowGenericGenerationFailed,
    FlowSession,
    FlowUnusualActivity,
)
from nano_batch.cancellation import (
    authoritative_cancellation_scope,
    cancellation_checkpoint,
    format_cancellation_diagnostics,
    interruptible_call,
)
from nano_batch.launcher import run_launcher
from nano_batch.models import (
    BatchItem,
    CandidateRecord,
    FlowProjectReference,
    GenerationSettings,
)


PROJECT = FlowProjectReference(
    project_id="00000000-0000-4000-8000-000000000001",
    project_url=(
        "https://flow.google.com/project/"
        "00000000-0000-4000-8000-000000000001"
    ),
    created_for_batch=True,
)


class AuthoritativeCancellationTests(unittest.TestCase):
    def session(self) -> FlowSession:
        session = FlowSession(PROJECT.project_url, Path("profile"))
        page = MagicMock()
        page.url = PROJECT.project_url
        session.page = page
        return session

    def test_first_sigint_is_immediate_and_latched(self):
        with authoritative_cancellation_scope() as state:
            cancellation_checkpoint("generation_result_wait")
            handler = signal.getsignal(signal.SIGINT)
            with self.assertRaises(KeyboardInterrupt) as raised:
                handler(signal.SIGINT, None)

            self.assertTrue(state.requested)
            self.assertEqual(state.received_phase, "generation_result_wait")
            self.assertTrue(raised.exception.cancellation_requested)
            self.assertEqual(
                raised.exception.cancellation_received_phase,
                "generation_result_wait",
            )

    def test_one_translated_signal_wins_in_every_post_generation_phase(self):
        cases = (
            ("candidate_discovery", "candidate_dom_discovery"),
            ("candidate_correlation", "candidate_uuid_ownership_validation"),
            ("candidate_reacquisition", "exact_candidate_reacquisition"),
            ("candidate_history_search", "virtualized_candidate_history_search"),
            ("candidate_tile_reacquisition", "candidate_tile_locator_acquisition"),
            ("download_button_interaction", "candidate_download_menu_open"),
            ("download_submenu_wait", "download_submenu_exposure"),
            ("download_1k_option_wait", "download_1k_readiness"),
            ("download_event_wait", "download_progress_poll_wait"),
            ("download_cdp_tracking", "set_download_behavior"),
            ("download_file_appearance_poll", "staging_directory_reconciliation"),
            ("download_file_validation", "pillow_staging_validation"),
            ("download_atomic_commit", "atomic_canonical_commit"),
            ("download_cleanup", "restore_default_download_behavior"),
        )
        for phase, operation in cases:
            with self.subTest(phase=phase), authoritative_cancellation_scope() as state:
                def translated_after_signal():
                    state.request()
                    raise RuntimeError("translated synchronous transport failure")

                with self.assertRaises(KeyboardInterrupt) as raised:
                    interruptible_call(
                        phase,
                        operation,
                        translated_after_signal,
                    )

                facts = raised.exception.cancellation_diagnostics
                self.assertEqual(facts["interrupt_signal_count"], 1)
                self.assertEqual(facts["interrupt_phase"], phase)
                self.assertEqual(facts["cancellation_latched_at_phase"], phase)
                self.assertEqual(facts["blocking_operation"], operation)
                self.assertTrue(facts["blocking_operation_started"])
                self.assertTrue(facts["blocking_operation_exited"])
                self.assertTrue(facts["keyboard_interrupt_propagated"])
                self.assertFalse(facts["recovery_menu_reached"])

    def test_latched_signal_cannot_be_swallowed_by_normal_blocking_return(self):
        with authoritative_cancellation_scope() as state:
            def swallowed_by_blocking_call():
                state.request()
                return "apparently successful"

            with self.assertRaises(KeyboardInterrupt) as raised:
                interruptible_call(
                    "download_event_wait",
                    "download_progress_poll_wait",
                    swallowed_by_blocking_call,
                )

        facts = raised.exception.cancellation_diagnostics
        self.assertEqual(facts["interrupt_signal_count"], 1)
        self.assertTrue(facts["blocking_operation_exited"])
        self.assertTrue(facts["keyboard_interrupt_propagated"])

    def test_cancellation_diagnostics_are_bounded_and_prompt_free(self):
        with authoritative_cancellation_scope() as state:
            with self.assertRaises(KeyboardInterrupt) as raised:
                interruptible_call(
                    "download_submenu_wait",
                    "download_submenu_exposure",
                    lambda: (_ for _ in ()).throw(state.request()),
                )
        rendered = format_cancellation_diagnostics(raised.exception)
        self.assertIsNotNone(rendered)
        self.assertIn("signal: 1", rendered)
        self.assertIn("phase: download_submenu_wait", rendered)
        self.assertNotIn("prompt", rendered.casefold())
        self.assertNotIn("http", rendered.casefold())

    def test_generation_submission_classification_is_unchanged_at_each_boundary(self):
        cases = (
            ("composer_resolution", False, False, False, "not_submitted"),
            ("post_input_start_actionability", True, False, False, "not_submitted"),
            ("pre_start_activation", True, False, False, "not_submitted"),
            ("start_mouse_down", True, True, False, "ambiguous_or_sent"),
            ("result_wait", True, True, True, "ambiguous_or_sent"),
        )
        for phase, prompt_written, mouse_down, acknowledged, submission in cases:
            with self.subTest(phase=phase):
                session = self.session()
                with authoritative_cancellation_scope() as state:
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        with session._generation_interrupt_scope():
                            session._set_generation_interrupt_checkpoint(
                                phase,
                                prompt_written=prompt_written,
                                start_mouse_down_sent=mouse_down,
                                start_acknowledged=acknowledged,
                            )
                            raise state.request()

                self.assertEqual(raised.exception.interrupt_phase, phase)
                self.assertEqual(raised.exception.prompt_written, prompt_written)
                self.assertEqual(raised.exception.start_mouse_down_sent, mouse_down)
                self.assertEqual(raised.exception.start_acknowledged, acknowledged)
                self.assertEqual(raised.exception.submission_state, submission)
                self.assertTrue(raised.exception.cancellation_requested)

    def test_latched_cancel_stops_editor_result_candidate_menu_and_window_waits(self):
        session = self.session()
        calls = (
            (
                "editor readiness",
                lambda: session._wait_for_authenticated_image_editor(
                    PROJECT, timeout_seconds=0.01, poll_interval_ms=1
                ),
            ),
            (
                "result wait",
                lambda: session.wait_for_result(set(), 1, expected_count=1),
            ),
            (
                "candidate reacquisition",
                lambda: session._exact_candidate_image_tile(
                    "candidate-safe-id", timeout_ms=100, poll_interval_ms=50
                ),
            ),
            (
                "submenu wait",
                lambda: session._wait_for_associated_download_submenu(
                    MagicMock(), {}, timeout_ms=1
                ),
            ),
            (
                "1K readiness",
                lambda: session._wait_for_current_1k_original_size_option(
                    {}, timeout_ms=250
                ),
            ),
            (
                "window recovery",
                lambda: session._wait_for_post_window_recovery_settle(
                    operation_phase="generation",
                    candidate_id=None,
                    require_semantic_surface=True,
                    same_generation_attempt_active=True,
                    start_acknowledged=True,
                    timeout_seconds=0.1,
                    poll_interval_ms=50,
                ),
            ),
        )
        for label, operation in calls:
            with self.subTest(label=label), authoritative_cancellation_scope() as state:
                state.request()
                with self.assertRaises(KeyboardInterrupt):
                    operation()

        session.page.evaluate.assert_not_called()

    def test_cancel_during_explicit_failure_poll_prevents_candidate_discovery(self):
        session = self.session()

        def cancel_failure_poll():
            raise state.request()

        with authoritative_cancellation_scope() as state, patch.object(
            session,
            "_raise_for_current_generation_attempt_failure",
            side_effect=cancel_failure_poll,
        ) as failure_poll, patch.object(
            session, "discover_candidates"
        ) as discover, self.assertRaises(KeyboardInterrupt):
            session.wait_for_result(set(), 1, expected_count=1)

        failure_poll.assert_called_once_with()
        discover.assert_not_called()

    def test_cancel_wins_before_usage_limit_classification(self):
        session = self.session()
        with authoritative_cancellation_scope() as state, patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ) as failure_poll, self.assertRaises(KeyboardInterrupt):
            state.request()
            session.wait_for_result(set(), 1, expected_count=1)

        failure_poll.assert_not_called()

    def test_cancel_during_unusual_recovery_never_dispatches_retry_start(self):
        session = self.session()
        item = BatchItem(order=1, visual_id="visual_001", prompt="safe prompt")
        retry_started = MagicMock()

        def cancel_during_recovery(*, project):
            self.assertEqual(project, PROJECT)
            state.request()
            return MagicMock()

        with authoritative_cancellation_scope() as state, patch.object(
            session,
            "_generate_one_in_open_session",
            side_effect=FlowUnusualActivity(),
        ) as generate, patch.object(
            session,
            "_recover_unusual_activity_in_open_session",
            side_effect=cancel_during_recovery,
        ), self.assertRaises(KeyboardInterrupt):
            session.generate_one_with_unusual_activity_recovery(
                item=item,
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset(),
                project=PROJECT,
                retry_started=retry_started,
            )

        generate.assert_called_once()
        retry_started.assert_not_called()

    def test_one_cancel_stops_each_unusual_activity_recovery_phase(self):
        phases = (
            "unusual_activity_home_navigation",
            "unusual_activity_project_reopen",
            "unusual_activity_editor_readiness",
        )
        for target_phase in phases:
            with self.subTest(phase=target_phase):
                session = self.session()
                session._browser = MagicMock()
                session._page_cdp_session = MagicMock()
                navigation_count = 0

                def navigate(*_args, **_kwargs):
                    nonlocal navigation_count
                    navigation_count += 1
                    active_phase = (
                        "unusual_activity_home_navigation"
                        if navigation_count == 1
                        else "unusual_activity_project_reopen"
                    )
                    if target_phase == active_phase:
                        raise state.request()

                session.page.goto.side_effect = navigate

                def editor_wait(_project):
                    if target_phase == "unusual_activity_editor_readiness":
                        raise state.request()
                    return MagicMock()

                with authoritative_cancellation_scope() as state, patch.object(
                    session, "_wait_for_flow_home"
                ), patch.object(
                    session, "_wait_for_saved_project_reference"
                ), patch.object(
                    session, "ensure_production_window_usable"
                ), patch.object(
                    session,
                    "_wait_for_authenticated_image_editor",
                    side_effect=editor_wait,
                ), self.assertRaises(KeyboardInterrupt) as raised:
                    with session._generation_interrupt_scope():
                        session._set_generation_interrupt_checkpoint(
                            "result_wait",
                            prompt_written=True,
                            start_mouse_down_sent=True,
                            start_acknowledged=True,
                        )
                        session._recover_unusual_activity_in_open_session(
                            project=PROJECT
                        )

                self.assertEqual(raised.exception.interrupt_phase, target_phase)
                self.assertEqual(
                    raised.exception.submission_state,
                    "ambiguous_or_sent",
                )
                expected_navigations = (
                    1 if target_phase == "unusual_activity_home_navigation" else 2
                )
                self.assertEqual(session.page.goto.call_count, expected_navigations)

    def test_cancel_during_generic_retry_authorization_never_dispatches_retry_start(self):
        session = self.session()
        item = BatchItem(order=1, visual_id="visual_001", prompt="safe prompt")

        def authorize_then_cancel(_failure):
            state.request()
            return True

        with authoritative_cancellation_scope() as state, patch.object(
            session,
            "_generate_one_in_open_session",
            side_effect=FlowGenericGenerationFailed(),
        ) as generate, self.assertRaises(KeyboardInterrupt):
            session.generate_one_with_unusual_activity_recovery(
                item=item,
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset(),
                project=PROJECT,
                retry_started=MagicMock(),
                generic_failure_retry_started=authorize_then_cancel,
            )

        generate.assert_called_once()

    def test_cancel_during_cdp_progress_unwinds_without_second_activation(self):
        session = self.session()
        browser = MagicMock()
        cdp = MagicMock()
        browser.new_browser_cdp_session.return_value = cdp
        session._browser = browser
        download_action = MagicMock()

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.download"

            def activate_once(*_args, **_kwargs):
                state.request()

            with authoritative_cancellation_scope() as state, patch.object(
                session, "_exact_window_state", return_value="maximized"
            ), patch.object(
                session, "_activate_download_control", side_effect=activate_once
            ) as activate, self.assertRaises(KeyboardInterrupt):
                session._download_with_cdp(
                    candidate_id="candidate-safe-id",
                    download_action=download_action,
                    destination=destination,
                    timeout_seconds=1,
                )

        activate.assert_called_once()
        cdp.detach.assert_not_called()
        self.assertEqual(cdp.send.call_count, 1)

    def test_download_candidate_cancel_skips_all_new_playwright_cleanup(self):
        session = self.session()
        session._browser = MagicMock()
        candidate = CandidateRecord(
            variant=1,
            suggested_filename="candidate.jpeg",
            source_kind="rendered-image",
            candidate_id="candidate-safe-id",
        )

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.download"

            def translated_menu_interrupt(*_args, **_kwargs):
                state.request()
                raise RuntimeError("Playwright translated Ctrl+C")

            with authoritative_cancellation_scope() as state, patch.object(
                session, "open", return_value=session
            ), patch.object(
                session, "close"
            ) as close, patch.object(
                session, "ensure_production_window_usable"
            ), patch.object(
                session, "_assert_download_context"
            ), patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("candidate-safe-id", MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=translated_menu_interrupt,
            ), patch.object(
                session, "_restore_candidate_history_view"
            ) as restore_history, self.assertRaises(KeyboardInterrupt) as raised:
                session.download_candidate(
                    candidate=candidate,
                    destination=destination,
                )

        facts = raised.exception.cancellation_diagnostics
        self.assertEqual(facts["interrupt_signal_count"], 1)
        self.assertEqual(facts["interrupt_phase"], "download_button_interaction")
        self.assertEqual(facts["blocking_operation"], "candidate_download_menu_open")
        session.page.keyboard.press.assert_not_called()
        restore_history.assert_not_called()
        close.assert_called_once_with(intentional_cancellation=True)

    def test_cancel_during_cdp_configuration_skips_reset_and_detach(self):
        session = self.session()
        browser = MagicMock()
        cdp = MagicMock()
        browser.new_browser_cdp_session.return_value = cdp
        session._browser = browser

        def translated_configuration_interrupt(*_args, **_kwargs):
            state.request()
            raise RuntimeError("CDP translated Ctrl+C")

        cdp.send.side_effect = translated_configuration_interrupt
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.download"
            with authoritative_cancellation_scope() as state, patch.object(
                session, "_exact_window_state", return_value="maximized"
            ), self.assertRaises(KeyboardInterrupt) as raised:
                session._download_with_cdp(
                    candidate_id="candidate-safe-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

        facts = raised.exception.cancellation_diagnostics
        self.assertEqual(facts["interrupt_signal_count"], 1)
        self.assertEqual(facts["interrupt_phase"], "download_cdp_tracking")
        self.assertEqual(facts["blocking_operation"], "set_download_behavior")
        cdp.send.assert_called_once()
        cdp.detach.assert_not_called()

    def test_translated_cancel_during_cdp_attach_uses_intentional_cleanup(self):
        session = FlowSession(PROJECT.project_url, Path("profile"))
        manager = MagicMock()
        playwright = MagicMock()
        manager.start.return_value = playwright

        def translated_interrupt(*_args, **_kwargs):
            state.request()
            raise RuntimeError("driver interrupted")

        playwright.chromium.connect_over_cdp.side_effect = translated_interrupt
        with authoritative_cancellation_scope() as state, patch.object(
            FlowSession,
            "_playwright_api",
            return_value=MagicMock(return_value=manager),
        ), self.assertRaises(KeyboardInterrupt):
            session.open()

        playwright.stop.assert_called_once_with()

    def test_intentional_cleanup_stops_driver_without_new_cdp_or_browser_rpcs(self):
        session = self.session()
        cdp = MagicMock()
        browser = MagicMock()
        playwright = MagicMock()
        session._page_cdp_session = cdp
        session._focus_emulation_enabled = True
        session._browser = browser
        session._playwright = playwright

        with authoritative_cancellation_scope() as state:
            state.request()
            session.close(intentional_cancellation=True)
            self.assertTrue(state.cleanup_started)
            self.assertTrue(state.cleanup_completed)
            self.assertIsNone(state.cleanup_error_type)

        cdp.send.assert_not_called()
        cdp.detach.assert_not_called()
        browser.close.assert_not_called()
        playwright.stop.assert_called_once_with()

    def test_session_exit_converts_a_translated_post_signal_error_back_to_interrupt(self):
        session = self.session()
        browser = MagicMock()
        playwright = MagicMock()
        session._browser = browser
        session._playwright = playwright

        with authoritative_cancellation_scope() as state, self.assertRaises(
            KeyboardInterrupt
        ):
            with session:
                state.request()
                raise RuntimeError("transport translated the signal")

        browser.close.assert_not_called()
        playwright.stop.assert_called_once_with()

    def test_launcher_marks_existing_recovery_menu_reached_after_one_interrupt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "prompts.txt").write_text("safe prompt\n", encoding="utf-8")
            captured = {}

            def execute(_prompts_path, _run_dir, _retry):
                with authoritative_cancellation_scope() as state:
                    captured["state"] = state
                    cancellation_checkpoint("generation_result_wait")
                    raise state.request()

            choices = iter(("2", "", "2", "6"))
            output_lines = []
            result = run_launcher(
                root,
                execute,
                input_fn=lambda _prompt: next(choices),
                output=output_lines.append,
            )

        self.assertEqual(result, 0)
        self.assertTrue(captured["state"].recovery_menu_reached)
        self.assertIn("  signal: 1", output_lines)
        self.assertIn("  recovery menu reached: yes", output_lines)

    def test_production_source_has_checkpoints_for_all_requested_active_phases(self):
        root = Path(__file__).resolve().parents[1]
        flow_source = (root / "nano_batch" / "adapters" / "flow.py").read_text(
            encoding="utf-8"
        )
        runner_source = (root / "nano_batch" / "runner.py").read_text(
            encoding="utf-8"
        )
        required_flow_phases = (
            "session_playwright_start",
            "session_cdp_attach",
            "composer_resolution",
            "prompt_entry",
            "post_input_start_actionability",
            "pre_start_activation",
            "start_mouse_down",
            "generation_acknowledgement_wait",
            "generation_result_wait",
            "unusual_activity_home_navigation",
            "unusual_activity_project_reopen",
            "unusual_activity_editor_readiness",
            "generic_failure_retry_preparation",
            "candidate_reacquisition_wait",
            "download_submenu_wait",
            "download_1k_readiness_wait",
            "download_progress_wait",
            "window_recovery_settle",
        )
        for phase in required_flow_phases:
            with self.subTest(phase=phase):
                self.assertIn(f'"{phase}"', flow_source)
        self.assertIn('cancellation_checkpoint("generic_retry_loop")', runner_source)
        self.assertIn('cancellation_checkpoint("sequential_order_dispatch")', runner_source)


if __name__ == "__main__":
    unittest.main()
