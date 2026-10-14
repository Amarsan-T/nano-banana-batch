"""Synthetic observer-to-error privacy regressions; no browser or network access."""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import (
    FlowAutomationError, FlowFailureAttributionAmbiguous, FlowGenericGenerationFailed,
    FlowHighDemand, FlowPolicyRejected, FlowSession, FlowUnusualActivity,
    FlowUsageLimitReached, _safe_current_generation_failure,
)
from nano_batch.models import GenerationSettings
from nano_batch.runner import execute_sequential_batch, plan_sequential_batch
from nano_batch.state import RunStateStore
from tests import test_flow_explicit_failures as explicit_failures
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE, PROJECT_URL,
    UNUSUAL_ACTIVITY_EXTENSION_FAILURE, UNUSUAL_ACTIVITY_FAILURE,
)
from tests.test_flow_failure_attribution import _run_case


# These UI canaries differ from the run's source prompts, which remain persisted.
# Construct private-looking paths without embedding personal home paths in source.
CANARIES = (
    "synthetic private prompt CANARY",
    "https://example.invalid/image?auth=FAKE_AUTH_CANARY&prompt=FAKE_QUERY_CANARY",
    str(PureWindowsPath("C:/") / "Users" / "synthetic-user" / "private" / "canary.txt"),
    str(PurePosixPath("/") / "home" / "synthetic-user" / "private" / "canary.txt"),
    "Bearer SYNTHETIC_CREDENTIAL_CANARY",
)


class FailurePrivacyTests(unittest.TestCase):
    @staticmethod
    def snapshot(message, *, historical=False, remount=False):
        setup = "const old = history.append(new Element('article', failure));" if historical else ""
        actions = (
            "detach(old); add(history, new Element('article', failure));" if remount else
            "state.observer.callback([{type:'attributes',target:old,attributeName:'class',oldValue:''}]);"
            if historical else "add(history, new Element('p', failure));"
        )
        return _run_case(setup, actions, failure=message)["after"]

    @staticmethod
    def session(snapshot, *, debug=False):
        session = FlowSession(PROJECT_URL, Path("unused-synthetic-profile"),
                              prestart_timing_debug=debug)
        session.page = MagicMock()
        session.page.is_closed.return_value = False
        session.page.evaluate.return_value = snapshot
        session.ensure_production_window_usable = MagicMock(return_value=None)
        session.configure_generation = MagicMock(return_value={"configured_variants": 1})
        session._asset_snapshot = MagicMock(return_value=[])
        session._progress_signals = MagicMock(return_value=())

        def submit(_prompt, **kwargs):
            # The Node fixture supplies post-Start evidence. No real input is sent.
            session._set_generation_interrupt_checkpoint(
                "start_acknowledgement_wait", start_mouse_down_sent=True,
            )
            return session._wait_for_generation_start(
                prompt_input=MagicMock(), intended_prompt=_prompt,
                baseline_fingerprints=kwargs["baseline_fingerprints"],
                baseline_progress=kwargs["baseline_progress"],
                baseline_candidate_ids=kwargs["baseline_candidate_ids"],
                forbidden_candidate_ids=kwargs["forbidden_candidate_ids"],
            )

        session.submit_prompt = submit
        return session

    @staticmethod
    def adapter(session):
        class Adapter(explicit_failures.PolicyFailureRunnerTests.Adapter):
            failure_pending = True

            def generate_one(self, **kwargs):
                if not self.failure_pending:
                    return super().generate_one(**kwargs)
                self.failure_pending = False
                item = kwargs["item"]
                self.events.append(("generate", item.order, item.prompt))
                self.generate_calls_by_order[item.order] = 1
                return session._generate_one_in_open_session(**kwargs)

        return Adapter(("success", "success"))

    def run_failure(self, snapshot, run_dir, *, debug=False):
        session = self.session(snapshot, debug=debug)
        adapter = self.adapter(session)
        batch = explicit_failures.PolicyFailureRunnerTests.batch()
        with self.assertRaises(FlowAutomationError) as raised:
            execute_sequential_batch(batch, run_dir, adapter, GenerationSettings(variants=1),
                                     manage_flow_project=True)
        session.page.mouse.down.assert_not_called()
        session.page.mouse.up.assert_not_called()
        return raised.exception, RunStateStore(run_dir / "run_state.json").load(), session, adapter

    @staticmethod
    def cli_error(error):
        def fail(_args):
            raise error

        stdout, stderr = StringIO(), StringIO()
        with patch.object(app, "build_parser") as parser, redirect_stdout(stdout), redirect_stderr(stderr):
            parser.return_value.parse_args.return_value = SimpleNamespace(func=fail)
            code = app.main()
        return code, stdout.getvalue() + stderr.getvalue()

    def test_unknown_canaries_are_absent_from_exception_state_diagnostics_and_cli(self):
        for canary in CANARIES:
            snapshot = self.snapshot("Generation failed: " + canary)
            self.assertTrue(snapshot["failures"], "The real observer must qualify this new failure")
            for debug in (False, True):
                with self.subTest(canary=canary, debug=debug), tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp) / "run"
                    error, state, session, adapter = self.run_failure(snapshot, run_dir, debug=debug)
                    self.assertIs(type(error), FlowAutomationError)
                    code, output = self.cli_error(error)
                    self.assertEqual(code, 2)
                    preview = StringIO()
                    with redirect_stdout(preview):
                        batch = explicit_failures.PolicyFailureRunnerTests.batch()
                        app._print_existing_preview_state(batch, batch.items[0], run_dir)
                    representations = (
                        str(error), repr(error), repr(vars(error)), state.items[0].error,
                        (run_dir / "run_state.json").read_text(encoding="utf-8"),
                        json.dumps(session._post_start_ack_timing_snapshot()), output, preview.getvalue(),
                        json.dumps(session._current_generation_failure_poll_observation),
                        json.dumps(session._current_generation_attempt_failures()),
                    )
                    for representation in representations:
                        for private_text in (*CANARIES, "FAKE_AUTH_CANARY", "FAKE_QUERY_CANARY",
                                             "SYNTHETIC_CREDENTIAL_CANARY", "synthetic-user"):
                            self.assertFalse(
                                private_text in representation
                                or json.dumps(private_text)[1:-1] in representation,
                                "Synthetic UI text escaped through an error/diagnostic representation",
                            )
                    self.assertIn("raw UI text", str(error))
                    self.assertIn("generation_observation=", str(error))
                    observation = json.loads(str(error).split(" [generation_observation=", 1)[1][:-1])
                    self.assertEqual(observation["phase"], "submission")
                    self.assertEqual(state.items[0].attempt_count, 1)
                    self.assertEqual(state.items[0].generation_error_code, None)
                    self.assertEqual(state.items[0].automatic_generic_failure_retry_count, 0)
                    self.assertEqual(state.items[0].automatic_policy_retry_count, 0)
                    self.assertEqual(state.items[0].candidates, [])
                    self.assertEqual(state.items[1].status, "pending")
                    self.assertFalse(any(isinstance(event, tuple) and event[0] in
                                         {"download", "navigate_home", "reopen", "fresh_baseline"}
                                         for event in adapter.events))

    def test_unknown_failure_requires_manual_retry_without_resetting_ownership(self):
        snapshot = self.snapshot("Generation failed: " + CANARIES[0])
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _, saved, _, adapter = self.run_failure(snapshot, run_dir)
            batch, settings = explicit_failures.PolicyFailureRunnerTests.batch(), GenerationSettings(variants=1)
            project = saved.flow_project
            before = (run_dir / "run_state.json").read_bytes()
            plan = plan_sequential_batch(batch, run_dir, settings)
            self.assertEqual(plan.orders[0].generation_action, "blocked_retry_required")
            with self.assertRaisesRegex(ValueError, "retry"):
                execute_sequential_batch(batch, run_dir, adapter, settings, manage_flow_project=True)
            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            final, _ = execute_sequential_batch(batch, run_dir, adapter, settings,
                                              manage_flow_project=True, retry_failed_generation=True)
            self.assertEqual(final.flow_project, project)
            self.assertEqual(adapter.create_calls, 1)
            self.assertEqual(final.items[0].attempt_count, 2)
            self.assertEqual(final.items[1].attempt_count, 1)
            self.assertEqual(final.items[0].automatic_generic_failure_retry_count, 0)
            self.assertEqual(final.items[0].automatic_policy_retry_count, 0)
            self.assertTrue(all(item.status == item.download_status == "completed" for item in final.items))
            ids = [candidate.candidate_id for item in final.items for candidate in item.candidates]
            self.assertEqual(len(ids), len(set(ids)))

    def test_known_exact_failures_keep_classification_and_fixed_exception_messages(self):
        cases = (
            (POLICY_FAILURE, FlowPolicyRejected), (UNUSUAL_ACTIVITY_FAILURE, FlowUnusualActivity),
            (UNUSUAL_ACTIVITY_EXTENSION_FAILURE, FlowUnusualActivity),
            (GENERIC_GENERATION_FAILURE, FlowGenericGenerationFailed),
            (HIGH_DEMAND_FAILURE, FlowHighDemand),
            ("You've reached your usage limit.", FlowUsageLimitReached),
        )
        for message, expected in cases:
            with self.subTest(expected=expected.__name__, message=message):
                snapshot = self.snapshot(message + " " + CANARIES[0])
                session = self.session(snapshot)
                with self.assertRaises(expected) as raised:
                    session._raise_for_current_generation_attempt_failure()
                self.assertIs(type(raised.exception), expected)
                self.assertEqual(str(raised.exception), str(expected()))
                self.assertNotIn(CANARIES[0], str(raised.exception))

    def test_known_failure_text_is_canonicalized_before_python_exposure(self):
        for message in (POLICY_FAILURE, UNUSUAL_ACTIVITY_FAILURE, GENERIC_GENERATION_FAILURE,
                        HIGH_DEMAND_FAILURE, "You've reached your usage limit."):
            with self.subTest(message=message):
                safe = _safe_current_generation_failure(message + " " + CANARIES[0])
                self.assertNotIn(CANARIES[0], safe)

    def test_unknown_similar_wording_never_becomes_a_confirmed_generic_failure(self):
        for prefix in ("Generation failed", "Could not generate", "Couldn't generate",
                       "Failed to generate", "Error generating"):
            message = prefix + ": " + CANARIES[0] + ". You have not been charged for this generation."
            with self.subTest(prefix=prefix):
                session = self.session(self.snapshot(message))
                with self.assertRaises(FlowAutomationError) as raised:
                    session._raise_for_current_generation_attempt_failure()
                self.assertIs(type(raised.exception), FlowAutomationError)
                self.assertNotIn(CANARIES[0], str(raised.exception))

    def test_historical_sensitive_failure_is_not_current_attempt_evidence(self):
        snapshot = self.snapshot("Generation failed: " + CANARIES[0], historical=True)
        self.assertEqual(snapshot["failures"], [])
        self.assertFalse(snapshot["failure_attribution_ambiguous"])
        session = self.session(snapshot)
        self.assertEqual(session._current_generation_attempt_failures(), ())
        session._raise_for_current_generation_attempt_failure()

    def test_remounted_sensitive_failure_remains_ambiguous_and_retry_protected(self):
        snapshot = self.snapshot("Generation failed: " + CANARIES[0], historical=True, remount=True)
        self.assertTrue(snapshot["failure_attribution_ambiguous"])
        with tempfile.TemporaryDirectory() as tmp:
            error, state, _, _ = self.run_failure(snapshot, Path(tmp) / "run")
            self.assertIs(type(error), FlowFailureAttributionAmbiguous)
            self.assertNotIn(CANARIES[0], str(error))
            self.assertEqual(state.items[0].attempt_count, 1)
            self.assertEqual(state.items[0].automatic_generic_failure_retry_count, 0)
            self.assertEqual(state.items[0].automatic_policy_retry_count, 0)
            self.assertEqual(state.items[1].status, "pending")

    def test_terminal_error_does_not_interpolate_untrusted_fallback_text(self):
        session = self.session({})
        with patch.object(session, "_current_generation_attempt_failures", return_value=(
            "Generation failed: " + CANARIES[-1],
        )), self.assertRaises(FlowAutomationError) as raised:
            session._raise_for_current_generation_attempt_failure()
        self.assertIs(type(raised.exception), FlowAutomationError)
        self.assertNotIn(CANARIES[-1], str(raised.exception))


if __name__ == "__main__":
    unittest.main()
