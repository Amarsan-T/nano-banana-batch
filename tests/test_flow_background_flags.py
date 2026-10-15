"""Offline Chrome command-line verification; never start or attach to Chrome."""

import json
import os
import subprocess
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import FlowAutomationError, FlowSession, _REQUIRED_BACKGROUND_FLAGS
from nano_batch.cancellation import authoritative_cancellation_scope


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"


class BackgroundFlagVerificationTests(unittest.TestCase):
    def session(self, arguments=None):
        session = FlowSession(PROJECT_URL, Path("synthetic_profile"))
        session._page_cdp_session = MagicMock()
        session._page_cdp_session.send.side_effect = RuntimeError("requires --enable-automation")
        session._browser = MagicMock()
        browser_cdp = session._browser.new_browser_cdp_session.return_value
        browser_cdp.send.return_value = {"commandLine": subprocess.list2cmdline(
            ["chrome.exe", *_REQUIRED_BACKGROUND_FLAGS] if arguments is None else arguments
        ), "gpu": {"private": "SYNTHETIC_GPU_SENTINEL"}}
        return session, browser_cdp

    def test_reproduced_normal_launcher_without_automation_flag_can_be_verified(self):
        session, browser_cdp = self.session()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            session._verify_live_background_flags()
        self.assertTrue(session._required_background_flags_present)
        self.assertEqual(session._missing_background_flags, ())
        self.assertEqual(caught, [])
        browser_cdp.send.assert_called_once_with("SystemInfo.getInfo")
        browser_cdp.detach.assert_called_once_with()

    def test_primary_command_line_success_needs_no_fallback(self):
        session, _ = self.session()
        session._page_cdp_session.send.side_effect = None
        session._page_cdp_session.send.return_value = {"arguments": ["chrome.exe", *_REQUIRED_BACKGROUND_FLAGS]}
        session._verify_live_background_flags()
        self.assertTrue(session._required_background_flags_present)
        session._browser.new_browser_cdp_session.assert_not_called()

    def test_primary_missing_flag_cannot_be_overridden_by_fallback(self):
        session, _ = self.session()
        session._page_cdp_session.send.side_effect = None
        session._page_cdp_session.send.return_value = {"arguments": ["chrome.exe"]}
        with self.assertRaisesRegex(FlowAutomationError, "missing required"):
            session._verify_live_background_flags()
        session._browser.new_browser_cdp_session.assert_not_called()

    def test_reused_browser_is_checked_against_actual_flags_not_launch_command(self):
        session, _ = self.session(["chrome.exe", *_REQUIRED_BACKGROUND_FLAGS[:-1]])
        session._debug_launch_command = MagicMock(side_effect=AssertionError("not live evidence"))
        with patch("nano_batch.adapters.flow.subprocess.Popen") as launch:
            with self.assertRaisesRegex(FlowAutomationError, "CalculateNativeWinOcclusion.*Close.*relaunch"):
                session._verify_live_background_flags()
            launch.assert_not_called()
        self.assertFalse(session._required_background_flags_present)

    def test_existing_endpoint_is_not_relaunched_or_reconfigured(self):
        session, _ = self.session()
        with patch.object(session, "_debug_endpoint_available", return_value=True), patch(
            "nano_batch.adapters.flow.subprocess.Popen"
        ) as launch, patch("builtins.print") as output:
            session.open_for_debugging()
        launch.assert_not_called()
        self.assertIn("reused", str(output.call_args_list).lower())

    def test_unavailable_both_methods_is_unknown_and_gives_accurate_guidance(self):
        session, browser_cdp = self.session()
        browser_cdp.send.side_effect = RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            session._verify_live_background_flags()
        self.assertIsNone(session._required_background_flags_present)
        self.assertEqual(session._missing_background_flags, ())
        message = str(caught[0].message)
        self.assertIn("flags are unknown", message)
        self.assertIn("chrome://version", message)
        self.assertIn("Relaunch alone", message)
        self.assertNotIn("SYNTHETIC_PRIVATE_EXCEPTION", message)
        browser_cdp.detach.assert_called_once_with()

    def test_unknown_warning_is_deduplicated_across_call_sites_by_default_filter(self):
        one, one_cdp = self.session()
        two, two_cdp = self.session()
        one_cdp.send.side_effect = two_cdp.send.side_effect = RuntimeError("unsupported")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("default")
            one._verify_live_background_flags()
            two._verify_live_background_flags()
        self.assertEqual(len(caught), 1)

    def test_malformed_or_empty_fallback_is_never_verified(self):
        for value in (None, "", [], 7, "chrome.exe\0 --disable-renderer-backgrounding"):
            with self.subTest(value=value):
                session, browser_cdp = self.session()
                browser_cdp.send.return_value = {"commandLine": value}
                with self.assertWarns(RuntimeWarning):
                    session._verify_live_background_flags()
                self.assertIsNone(session._required_background_flags_present)

    def test_unsupported_platform_does_not_guess_windows_quoting(self):
        session, _ = self.session()
        with patch("nano_batch.adapters.flow.os.name", "posix"), self.assertWarns(RuntimeWarning):
            session._verify_live_background_flags()
        self.assertIsNone(session._required_background_flags_present)
        session._browser.new_browser_cdp_session.assert_not_called()

    def test_fallback_interrupt_propagates_without_new_cleanup_rpc(self):
        session, browser_cdp = self.session()
        browser_cdp.send.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            session._verify_live_background_flags()
        browser_cdp.detach.assert_not_called()

    def test_failed_detach_does_not_invalidate_observed_flags(self):
        session, browser_cdp = self.session()
        browser_cdp.detach.side_effect = RuntimeError("detached transport")
        session._verify_live_background_flags()
        self.assertTrue(session._required_background_flags_present)

    def test_new_verification_cannot_reuse_stale_success(self):
        session, browser_cdp = self.session()
        session._verify_live_background_flags()
        browser_cdp.send.return_value = {"commandLine": ""}
        with self.assertWarns(RuntimeWarning):
            session._verify_live_background_flags()
        self.assertIsNone(session._required_background_flags_present)

    def test_each_missing_required_flag_is_rejected_by_fallback(self):
        for absent in _REQUIRED_BACKGROUND_FLAGS:
            with self.subTest(absent=absent):
                session, _ = self.session(["chrome.exe", *[f for f in _REQUIRED_BACKGROUND_FLAGS if f != absent]])
                with self.assertRaises(FlowAutomationError):
                    session._verify_live_background_flags()
                self.assertEqual(session._missing_background_flags, (absent,))

    def test_browser_target_creation_failure_leaves_unknown_status(self):
        session, _ = self.session()
        session._browser.new_browser_cdp_session.side_effect = RuntimeError("unavailable")
        with self.assertWarns(RuntimeWarning):
            session._verify_live_background_flags()
        self.assertIsNone(session._required_background_flags_present)

    def test_interrupt_during_primary_read_never_starts_fallback(self):
        session, _ = self.session()
        session._page_cdp_session.send.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            session._verify_live_background_flags()
        session._browser.new_browser_cdp_session.assert_not_called()

    def test_latched_cancel_after_fallback_reply_skips_cleanup_rpc(self):
        session, browser_cdp = self.session()
        reply = browser_cdp.send.return_value
        with authoritative_cancellation_scope() as state:
            def interrupted_reply(*_args):
                state.request()
                return reply
            browser_cdp.send.side_effect = interrupted_reply
            with self.assertRaises(KeyboardInterrupt):
                session._verify_live_background_flags()
        browser_cdp.detach.assert_not_called()

    def test_preexisting_cancel_performs_no_verification_rpc(self):
        session, _ = self.session()
        with authoritative_cancellation_scope() as state:
            state.request()
            with self.assertRaises(KeyboardInterrupt):
                session._verify_live_background_flags()
        session._page_cdp_session.send.assert_not_called()
        session._browser.new_browser_cdp_session.assert_not_called()

    def test_failure_diagnostics_reuse_only_safe_flag_booleans(self):
        session, browser_cdp = self.session([
            "chrome.exe", *_REQUIRED_BACKGROUND_FLAGS, "--user-data-dir=SYNTHETIC_PROFILE_SENTINEL",
            "https://example.test/private?token=SYNTHETIC_TOKEN_SENTINEL",
        ])
        session._verify_live_background_flags()
        session.page = MagicMock()
        session.page.is_closed.return_value = False
        session.page.evaluate.return_value = {"renderer": {}, "page": {}}
        session._page_cdp_session.send.side_effect = lambda *args: {}
        with patch.object(session, "_progress_signals", return_value=()), patch.object(
            session, "discover_candidates", return_value=()
        ):
            report = session._generation_failure_observation(
                error=FlowAutomationError("synthetic timeout"), submission={},
                baseline_fingerprints=set(), baseline_progress=set(),
                baseline_candidate_ids=set(), forbidden_candidate_ids=frozenset(),
            )
        self.assertTrue(report["browser"]["command_line_available"])
        self.assertTrue(all(report["browser"]["startup_flag_presence"].values()))
        for secret in ("SYNTHETIC_PROFILE_SENTINEL", "SYNTHETIC_TOKEN_SENTINEL", "SYNTHETIC_GPU_SENTINEL"):
            self.assertNotIn(secret, json.dumps(report))
        browser_cdp.send.assert_called_once_with("SystemInfo.getInfo")


class BackgroundFlagParsingTests(unittest.TestCase):
    def test_arguments_after_switch_terminator_are_not_flags(self):
        presence = FlowSession._background_flag_presence(["chrome.exe", "--", *_REQUIRED_BACKGROUND_FLAGS])
        self.assertFalse(any(presence.values()))

    def test_last_disable_features_switch_wins_instead_of_union(self):
        presence = FlowSession._background_flag_presence([
            "chrome.exe", *_REQUIRED_BACKGROUND_FLAGS, "--disable-features=OtherFeature",
        ])
        self.assertFalse(presence["disable_calculate_native_win_occlusion"])

    def test_feature_names_are_case_sensitive(self):
        presence = FlowSession._background_flag_presence([
            "chrome.exe", *_REQUIRED_BACKGROUND_FLAGS[:-1],
            "--disable-features=calculatenativewinocclusion",
        ])
        self.assertFalse(presence["disable_calculate_native_win_occlusion"])

    def test_bare_disable_features_overrides_previous_value(self):
        presence = FlowSession._background_flag_presence([
            "chrome.exe", *_REQUIRED_BACKGROUND_FLAGS, "--disable-features",
        ])
        self.assertFalse(presence["disable_calculate_native_win_occlusion"])

    def test_malformed_argument_list_is_unavailable_not_proven_absence(self):
        for arguments in ([], [None], ["chrome.exe", {}]):
            with self.subTest(arguments=arguments):
                self.assertIsNone(FlowSession._background_flag_presence(arguments))

    @unittest.skipUnless(os.name == "nt", "Windows native quoting check")
    def test_native_windows_parser_handles_quoted_spaces_unicode_and_backslashes(self):
        arguments = [r"C:\Program Files\Chrome\chrome.exe", *_REQUIRED_BACKGROUND_FLAGS,
                     '--user-data-dir=synthetic profile профиль', 'quoted"value', 'trailing\\']
        presence = FlowSession._windows_background_flag_presence(subprocess.list2cmdline(arguments))
        self.assertTrue(all(presence.values()))

    @unittest.skipUnless(os.name == "nt", "Windows native quoting check")
    def test_required_flag_inside_another_argument_is_not_a_real_switch(self):
        arguments = ["chrome.exe", "--user-data-dir=synthetic " + " ".join(_REQUIRED_BACKGROUND_FLAGS)]
        presence = FlowSession._windows_background_flag_presence(subprocess.list2cmdline(arguments))
        self.assertFalse(any(presence.values()))


if __name__ == "__main__":
    unittest.main()
