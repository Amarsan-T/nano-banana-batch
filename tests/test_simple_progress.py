"""Presentation-only progress, using synthetic state and no browser connection."""

import argparse
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import app
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.downloads import commit_staged_file
from nano_batch.errors import (
    FLOW_USAGE_LIMIT_REACHED, WAITING_FOR_FLOW_LIMIT_RESET, TemporaryGenerationBlock,
)
from nano_batch.models import Batch, BatchItem, GenerationSettings
from nano_batch.progress import (
    ProgressPhase as Phase, ProgressReporter, format_progress, format_simple_progress,
    simple_terminal_progress, terminal_progress,
)
from nano_batch.runner import execute_one, execute_sequential_batch, initialize_run_state
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory
from test_app import OfflinePromptAdapter


class SimpleProgressModelTests(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        self.reporter = ProgressReporter((1, 2), simple_terminal_progress(self.stream))
        self.reporter.seed(((1, "pending"), (2, "pending")))

    def emit(self, phase, **kwargs):
        self.reporter.record(phase, **kwargs)
        self.reporter.flush()
        return self.stream.getvalue()

    def test_normal_generation_distinguishes_saved_generation_from_complete_order(self):
        self.emit(Phase.GENERATION, order=1)
        self.emit(Phase.GENERATION_SAVED, order=1)
        self.assertEqual(self.reporter.snapshot.percent, 0)
        self.assertNotIn("Complete -", self.stream.getvalue())
        self.emit(Phase.DOWNLOAD, order=1)
        self.emit(Phase.DOWNLOAD, order=1, variant=1)
        self.emit(Phase.CANDIDATE_SAVED, order=1, variant=1)
        self.emit(Phase.COMPLETE, order=1)
        self.emit(Phase.COMPLETE, order=2)
        self.assertEqual(self.stream.getvalue().splitlines(), [
            "Batch: 2 orders. Completed: 0/2 (0%).",
            "[1/2] Generating image...",
            "[1/2] Generation saved. Preparing download...",
            "[1/2] Downloading image...",
            "[1/2] Complete - 1/2 (50%)",
            "[2/2] Complete - 2/2 (100%)",
        ])

    def test_detailed_consumer_keeps_every_event(self):
        detailed = StringIO()
        reporter = ProgressReporter((1,), terminal_progress(detailed))
        reporter.seed(((1, "pending"),))
        for phase in (Phase.GENERATION_SAVED, Phase.CANDIDATE_SAVED, Phase.VERIFYING, Phase.COMPLETE):
            reporter.record(phase, order=1, variant=1)
        snapshots = reporter.drain()
        for snapshot in snapshots:
            terminal_progress(detailed)(snapshot)
        self.assertEqual(detailed.getvalue(), "".join(format_progress(s) + "\n" for s in snapshots))
        self.assertIn("phase=candidate_saved", detailed.getvalue())
        self.assertIn("phase=verifying_order", detailed.getvalue())

    def test_verification_remains_in_event_stream_but_not_simple_output(self):
        self.reporter.flush()
        before = self.stream.getvalue()
        self.emit(Phase.VERIFYING, order=1)
        self.assertEqual(self.reporter.snapshot.event.phase, Phase.VERIFYING)
        self.assertEqual(self.stream.getvalue(), before)

    def test_mixed_resume_counts_only_full_orders(self):
        self.reporter.seed(((1, "completed"), (2, "failed_or_blocked")))
        self.emit(Phase.SKIP_GENERATION, order=2)
        self.assertIn("Completed: 1/2 (50%)", self.stream.getvalue())
        self.assertIn("Generation already saved. Checking remaining downloads (no new generation)", self.stream.getvalue())
        self.assertNotIn("[2/2] Already complete", self.stream.getvalue())

    def test_unverified_counts_are_not_reported_as_zero_or_complete(self):
        self.reporter.record(Phase.VALIDATING)
        self.assertIsNone(self.reporter.snapshot.percent)
        text = format_simple_progress(self.reporter.snapshot)
        self.assertNotIn("0%", text)
        self.assertNotIn("100%", text)

    def test_noncontiguous_orders_show_position_and_numeric_order(self):
        reporter = ProgressReporter((7, 27))
        reporter.seed(((7, "completed"), (27, "pending")))
        reporter.record(Phase.GENERATION, order=27)
        self.assertEqual(format_simple_progress(reporter.snapshot), "[2/2] Order 27: Generating image...")

    def test_retry_text_requires_existing_authorization_event(self):
        self.emit(Phase.FAILED, order=1, code="FLOW_POLICY_REJECTED")
        self.assertNotIn("Automatic retry", self.stream.getvalue())
        self.emit(Phase.RETRY, order=1, attempt=2, code="FLOW_POLICY_REJECTED")
        self.assertIn("Automatic retry authorized (policy rejection). Attempt 2.", self.stream.getvalue())

    def test_usage_limit_wait_does_not_offer_retry_or_claim_completion(self):
        text = self.emit(Phase.WAITING, order=1, code=FLOW_USAGE_LIMIT_REACHED)
        self.assertIn("Resume after it resets. Completed: 0/2 (0%)", text)
        self.assertNotIn("retry", text.lower())
        self.assertEqual(self.reporter.snapshot.counts["waiting"], 1)

    def test_ambiguous_ownership_is_not_confirmed_failure_or_retry_advice(self):
        text = self.emit(Phase.BLOCKED, order=1, code="FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS")
        self.assertIn("ownership is uncertain; no automatic resubmission", text)
        self.assertNotIn("explicit failure", text)
        self.assertNotIn("--retry", text)

    def test_candidate_conflict_is_explicit(self):
        text = self.emit(Phase.BLOCKED, order=1, code="CANDIDATE_UUID_OWNERSHIP_CONFLICT")
        self.assertIn("Candidate ownership conflict", text)
        self.assertNotIn("All orders complete", text)

    def test_interruption_preserves_completed_counts_without_success_claim(self):
        self.reporter.seed(((1, "completed"), (2, "active")))
        self.emit(Phase.INTERRUPTED)
        self.assertIn("Interrupted. Saved completion: 1/2 (50%)", self.stream.getvalue())
        self.assertEqual(self.reporter.snapshot.counts["stopped"], 1)
        self.assertNotIn("success", self.stream.getvalue())

    def test_latched_cancellation_adds_no_terminal_writes(self):
        with authoritative_cancellation_scope() as cancellation:
            cancellation.request()
            with self.assertRaises(KeyboardInterrupt):
                self.reporter.flush()
        self.assertEqual(self.stream.getvalue(), "")

    def test_output_translated_interrupt_keeps_first_signal(self):
        with authoritative_cancellation_scope() as cancellation:
            class InterruptedStream(StringIO):
                def write(self, _text):
                    cancellation.request()
                    raise OSError("synthetic output translation")
            reporter = ProgressReporter((1,), simple_terminal_progress(InterruptedStream()))
            reporter.record(Phase.GENERATION, order=1)
            with self.assertRaises(KeyboardInterrupt):
                reporter.flush()
            self.assertEqual(cancellation.interrupt_signal_count, 1)

    def test_percentage_is_integer_completed_orders_not_candidate_progress(self):
        reporter = ProgressReporter(tuple(range(1, 21)))
        reporter.seed(tuple((i, "pending") for i in range(1, 21)))
        reporter.record(Phase.CANDIDATE_SAVED, order=1, variant=1)
        self.assertEqual(reporter.snapshot.percent, 0)
        reporter.record(Phase.COMPLETE, order=1)
        self.assertIn("1/20 (5%)", format_simple_progress(reporter.snapshot))

    def test_full_order_count_is_not_a_command_exit_claim(self):
        self.reporter.seed(((1, "completed"), (2, "completed")))
        self.emit(Phase.RETURNED)
        self.assertIn("All orders complete: 2/2 (100%)", self.stream.getvalue())
        self.assertNotIn("success", self.stream.getvalue())
        self.emit(Phase.INTERRUPTED)
        self.assertIn("Interrupted. Saved completion: 2/2 (100%)", self.stream.getvalue())

    def test_unknown_metadata_is_not_interpolated_and_output_is_ascii(self):
        self.emit(Phase.FAILED, order="synthetic private identifier", variant="synthetic UUID",
                  attempt="synthetic path", code="synthetic private prompt\nhttps://example.invalid/?token=value")
        text = self.stream.getvalue()
        text.encode("ascii")
        for private in ("synthetic", "https:", "token", "\x1b", "\r"):
            self.assertNotIn(private, text)
        self.assertTrue(text.endswith("\n"))

    def test_x2_candidate_save_is_not_order_completion(self):
        stream = StringIO()
        reporter = ProgressReporter((1,), simple_terminal_progress(stream, variants=2))
        reporter.seed(((1, "pending"),))
        for phase, variant in ((Phase.GENERATION, None), (Phase.DOWNLOAD, None),
                               (Phase.DOWNLOAD, 1), (Phase.CANDIDATE_SAVED, 1),
                               (Phase.DOWNLOAD, 2), (Phase.CANDIDATE_SAVED, 2)):
            reporter.record(phase, order=1, variant=variant)
        reporter.flush()
        text = stream.getvalue()
        self.assertIn("Generating 2 images", text)
        self.assertIn("Downloading candidate 1/2", text)
        self.assertIn("Downloading candidate 2/2", text)
        self.assertIn("Candidate 1/2 saved; checking Order completion", text)
        self.assertIn("Candidate 2/2 saved; checking Order completion", text)
        self.assertNotIn("Complete -", text)
        self.assertEqual(reporter.snapshot.percent, 0)


class SimpleProgressExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.batch = Batch("sha256:synthetic-simple-progress", "synthetic", tuple(
            BatchItem(i, f"synthetic_visual_{i}", f"synthetic private prompt {i}", variants=1)
            for i in (1, 2)
        ))
        self.settings = GenerationSettings(variants=1)
        self.adapter = OfflinePromptAdapter()
        self.stream = StringIO()
        self.reporter = ProgressReporter((1, 2), simple_terminal_progress(self.stream))
        self.store = RunStateStore(self.run_dir / "run_state.json")

    def execute(self, **kwargs):
        return execute_sequential_batch(self.batch, self.run_dir, self.adapter, self.settings,
                                        progress=self.reporter, **kwargs)

    def test_skip_only_resume_does_not_call_adapter_or_rewrite_state(self):
        self.execute()
        before = self.store.path.read_bytes()
        self.adapter.events.clear()
        self.stream.seek(0)
        self.stream.truncate()
        self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(self.stream.getvalue().count("Already complete; validating"), 2)
        self.assertNotIn("Generating image", self.stream.getvalue())
        self.assertNotIn("Downloading image", self.stream.getvalue())
        self.assertIn("Completed: 2/2 (100%)", self.stream.getvalue())

    def test_download_only_resume_uses_saved_generation(self):
        execute_one(self.batch, self.run_dir, 1, self.adapter, self.settings)
        self.adapter.events.clear()
        state, _ = self.execute()
        self.assertNotIn(("generate", 1, self.batch.items[0].prompt, 1), self.adapter.events)
        self.assertEqual(state.items[0].attempt_count, 1)
        text = self.stream.getvalue()
        self.assertIn("[1/2] Generation already saved", text)
        self.assertIn("[1/2] Downloading image", text)
        self.assertIn("[1/2] Complete - 1/2 (50%)", text)

    def test_completed_assets_still_require_unique_ownership(self):
        self.execute()
        state = self.store.load()
        state.items[1].candidates[0].candidate_id = state.items[0].candidates[0].candidate_id
        self.store.save(state)
        before = self.store.path.read_bytes()
        self.adapter.events.clear()
        stream = StringIO()
        self.reporter = ProgressReporter((1, 2), simple_terminal_progress(stream))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertNotIn("100%", stream.getvalue())

    def test_usage_limit_still_stops_with_one_consumed_submission(self):
        error = TemporaryGenerationBlock("synthetic private error", code=FLOW_USAGE_LIMIT_REACHED,
            state_status=WAITING_FOR_FLOW_LIMIT_RESET, attempt_activated=True)
        with patch.object(self.adapter, "generate_one", side_effect=error) as generate:
            with self.assertRaises(TemporaryGenerationBlock):
                self.execute()
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.store.load().items[0].attempt_count, 1)
        self.assertIn("Paused for Flow usage limit", self.stream.getvalue())
        self.assertNotIn("private error", self.stream.getvalue())

    def test_ambiguous_failure_still_requires_existing_retry_protection(self):
        from nano_batch.adapters.flow import FlowFailureAttributionAmbiguous
        with patch.object(self.adapter, "generate_one", side_effect=FlowFailureAttributionAmbiguous()) as generate:
            with self.assertRaises(FlowFailureAttributionAmbiguous):
                self.execute()
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.store.load().items[0].attempt_count, 1)
        self.assertIn("ownership is uncertain; no automatic resubmission", self.stream.getvalue())
        self.assertNotIn("Automatic retry authorized", self.stream.getvalue())

    def test_existing_retry_authorization_is_reported_not_created(self):
        for code, counter in (("FLOW_POLICY_REJECTED", "automatic_policy_retry_count"),
                              ("FLOW_GENERIC_GENERATION_FAILED", "automatic_generic_failure_retry_count")):
            with self.subTest(code=code):
                run = self.root / code
                state = initialize_run_state(self.batch, run)
                state.items[0].status = "failed"
                state.items[0].attempt_count = 1
                state.items[0].generation_error_code = code
                RunStateStore(run / "run_state.json").save(state)
                stream = StringIO()
                reporter = ProgressReporter((1, 2), simple_terminal_progress(stream))
                final, _ = execute_sequential_batch(self.batch, run, OfflinePromptAdapter(), self.settings, progress=reporter)
                self.assertEqual(stream.getvalue().count("Automatic retry authorized"), 1)
                self.assertEqual(final.items[0].attempt_count, 2)
                self.assertEqual(getattr(final.items[0], counter), 1)

    def test_partial_x2_preserves_first_asset_and_resumes_second_only(self):
        self.batch = Batch(self.batch.batch_id, "synthetic", (BatchItem(1, "synthetic", "synthetic", variants=2),))
        self.settings = GenerationSettings(variants=2)
        self.reporter = ProgressReporter((1,), simple_terminal_progress(self.stream, variants=2))
        download = self.adapter.download_candidate
        def fail_second(**kwargs):
            if kwargs["candidate"].variant == 2:
                raise RuntimeError("synthetic download failure")
            return download(**kwargs)
        with patch.object(self.adapter, "download_candidate", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assertNotIn("Complete -", self.stream.getvalue())
        self.assertEqual(self.store.load().items[0].candidates[0].download_status, "completed")
        self.adapter.events.clear()
        self.execute()
        self.assertEqual(self.adapter.events, [("download", 1, 2)])
        self.assertIn("[1/1] Complete - 1/1 (100%)", self.stream.getvalue())

    def test_canonical_only_reconciliation_does_not_generate_or_download_order(self):
        fixture = make_pending_download_fixture(self.run_dir)
        commit_staged_file(fixture.staging_path, fixture.canonical_path, overwrite=False)
        if fixture.staging_path.exists():
            fixture.staging_path.unlink()
        final, _ = execute_sequential_batch(fixture.batch, self.run_dir, self.adapter, self.settings,
                                           progress=self.reporter)
        self.assertFalse(any(e[1] == 1 for e in self.adapter.events))
        self.assertEqual(final.items[0].download_status, "completed")
        self.assertIn("Checking interrupted download; no new generation", self.stream.getvalue())

    def test_broken_simple_output_cannot_change_attempts_or_completion(self):
        class BrokenStream(StringIO):
            def write(self, _text):
                raise BrokenPipeError("synthetic pipe failure")
        self.reporter = ProgressReporter((1, 2), simple_terminal_progress(BrokenStream()))
        state, outcomes = self.execute()
        self.assertTrue(self.reporter.output_failed)
        self.assertEqual([item.attempt_count for item in state.items], [1, 1])
        self.assertEqual([item.download_status for item in state.items], ["completed", "completed"])
        self.assertEqual(len(outcomes), 2)

    def test_renderer_modes_preserve_adapter_sequence_and_state_facts(self):
        facts = []
        for mode in (simple_terminal_progress, terminal_progress):
            adapter = OfflinePromptAdapter()
            state, outcomes = execute_sequential_batch(self.batch, self.root / mode.__name__, adapter, self.settings,
                progress=ProgressReporter((1, 2), mode(StringIO())))
            facts.append((adapter.events, [(i.status, i.download_status, i.attempt_count,
                i.automatic_policy_retry_count, i.automatic_generic_failure_retry_count,
                [(c.candidate_id, c.download_status, c.sha256) for c in i.candidates]) for i in state.items], outcomes))
        self.assertEqual(facts[0], facts[1])

    def test_cli_defaults_to_simple_and_retains_final_summaries(self):
        args = argparse.Namespace(run_dir=str(self.run_dir), config=str(self.root / "absent.json"),
                                  timeout=None, retry=False, preview=False)
        with patch.object(app, "_flow_session_from_args", return_value=self.adapter), \
             redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            result = app._run_sequential_command(self.batch, args, prompt_wrapper=True)
        self.assertEqual(result, 0)
        self.assertIn("[1/2] Generating image...", output.getvalue())
        self.assertIn("All orders complete: 2/2 (100%)", output.getvalue())
        self.assertIn("Completed: 2\nFailed: 0\n", output.getvalue())
        self.assertNotIn("PROGRESS ", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_cli_preview_does_not_start_reporter_or_create_state(self):
        args = argparse.Namespace(run_dir=str(self.run_dir), config=str(self.root / "absent.json"),
                                  timeout=None, retry=False, preview=True, progress_format="simple")
        with patch.object(app, "_flow_session_from_args") as session, \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(app._run_sequential_command(self.batch, args, prompt_wrapper=True), 0)
        session.assert_not_called()
        self.assertFalse(self.store.exists())
        self.assertNotIn("Generating image", output.getvalue())

    def test_parser_defaults_and_detailed_option_for_all_sequential_commands(self):
        for command in (("run-batch", "synthetic.json"), ("run-prompt", "synthetic"),
                        ("run-prompts-file", "synthetic.txt", "--run-dir", "synthetic"), ("launcher",)):
            with self.subTest(command=command):
                parser = app.build_parser()
                self.assertEqual(parser.parse_args(list(command)).progress_format, "simple")
                self.assertEqual(parser.parse_args([*command, "--progress-format", "detailed"]).progress_format, "detailed")
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            app.build_parser().parse_args(["launcher", "--progress-format", "invalid"])

    def test_launcher_passes_detailed_selection_without_browser_use(self):
        prompt_path = self.root / "synthetic-prompts.txt"
        prompt_path.write_text("synthetic\n", encoding="utf-8")
        args = argparse.Namespace(config=str(self.root / "absent.json"), url=None, profile_dir=None,
                                  debug_address=None, debug_port=None, progress_format="detailed")
        def invoke(_project_dir, callback, *, launch_chrome):
            return callback(prompt_path, self.run_dir, False)
        with patch.object(app, "run_launcher", side_effect=invoke), \
             patch.object(app, "cmd_run_prompts_file", return_value=9) as command:
            self.assertEqual(app.cmd_launcher(args), 9)
        self.assertEqual(command.call_args.args[0].progress_format, "detailed")

    def test_native_powershell_strict_redirection_accepts_both_formats(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("Windows PowerShell is unavailable on this platform")
        script = self.root / "synthetic-progress.py"
        script.write_text(
            "import sys\nfrom nano_batch.progress import ProgressReporter, ProgressPhase as P, "
            "simple_terminal_progress, terminal_progress\n"
            "for render in (simple_terminal_progress, terminal_progress):\n"
            " r=ProgressReporter((1,), render(sys.stdout))\n"
            " r.seed(((1, 'pending'),))\n"
            " r.record(P.GENERATION, order=1)\n"
            " r.record(P.COMPLETE, order=1)\n"
            " r.flush()\n", encoding="utf-8")
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        command = ("$ErrorActionPreference = 'Stop'; & " + quote(sys.executable) + " -B " + quote(script)
                   + " 2>&1 | Out-Null; exit $LASTEXITCODE")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                                   capture_output=True, env=env, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
