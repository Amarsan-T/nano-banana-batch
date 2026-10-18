"""Offline progress observations must never become execution/recovery authority."""

import argparse
import copy
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, asdict
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import app
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.downloads import commit_staged_file
from nano_batch.errors import (
    FLOW_USAGE_LIMIT_REACHED, WAITING_FOR_FLOW_LIMIT_RESET,
    GenericGenerationFailure, PolicyGenerationRejected, TemporaryGenerationBlock,
)
from nano_batch.models import Batch, BatchItem, FlowProjectReference, GenerationSettings
from nano_batch.progress import (
    ProgressEvent, ProgressPhase as Phase, ProgressReporter, ProgressSnapshot,
    format_progress, progress_scope, record_progress, reduce_progress, terminal_progress,
)
from nano_batch.runner import execute_download_one, execute_one, execute_sequential_batch, initialize_run_state
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory
from test_app import OfflinePromptAdapter


class ProgressModelTests(unittest.TestCase):
    def test_completed_generation_is_not_completed_order(self):
        reporter = ProgressReporter((7, 27))
        reporter.seed(((7, "pending"), (27, "pending")))
        reporter.record(Phase.GENERATION_SAVED, order=27)
        self.assertEqual(reporter.snapshot.percent, 0)
        self.assertEqual(reporter.snapshot.counts["active"], 1)
        self.assertIn("position=2/2", format_progress(reporter.snapshot))
        reporter.record(Phase.COMPLETE, order=27)
        self.assertEqual(reporter.snapshot.percent, 50)

    def test_counts_are_unverified_until_seeded(self):
        reporter = ProgressReporter((1,))
        self.assertIsNone(reporter.snapshot.percent)
        self.assertIn("counts=unverified", format_progress(reporter.snapshot))

    def test_reused_reporter_does_not_claim_previous_validation_is_current(self):
        reporter = ProgressReporter((1,))
        reporter.seed(((1, "completed"),))
        reporter.record(Phase.VALIDATING)
        self.assertFalse(reporter.snapshot.verified)
        self.assertIsNone(reporter.snapshot.percent)
        self.assertIn("counts=unverified", format_progress(reporter.snapshot))

    def test_reducer_is_pure_and_events_are_immutable(self):
        before = ProgressSnapshot(ProgressEvent(0, 0, Phase.READY), ((1, "pending"),), True)
        event = ProgressEvent(1, 1, Phase.GENERATION, 1)
        after = reduce_progress(before, event)
        self.assertEqual(before.orders, ((1, "pending"),))
        self.assertEqual(after.orders, ((1, "active"),))
        with self.assertRaises(FrozenInstanceError):
            event.order = 2

    def test_mailbox_is_bounded_with_latest_snapshot_preserved(self):
        reporter = ProgressReporter((1,))
        for _ in range(1000):
            reporter.record(Phase.GENERATION, order=1)
        snapshots = reporter.drain()
        self.assertEqual(len(snapshots), 64)
        self.assertEqual(reporter.dropped_events, 936)
        self.assertEqual(snapshots[-1], reporter.snapshot)
        self.assertEqual(reporter.drain(), ())

    def test_record_never_invokes_consumer(self):
        output = []
        reporter = ProgressReporter((1,), output.append)
        reporter.record(Phase.GENERATION_SAVED, order=1)
        self.assertEqual(output, [])
        reporter.flush()
        self.assertEqual(len(output), 1)

    def test_output_failure_disables_only_output(self):
        calls = []
        def broken(snapshot):
            calls.append(snapshot)
            raise BrokenPipeError("synthetic")
        reporter = ProgressReporter((1,), broken)
        for _ in range(2):
            reporter.record(Phase.GENERATION, order=1)
            reporter.flush()
        self.assertTrue(reporter.output_failed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(reporter.snapshot.event.sequence, 2)

    def test_output_keyboard_interrupt_is_not_suppressed(self):
        def interrupted(_snapshot):
            raise KeyboardInterrupt()
        reporter = ProgressReporter((1,), interrupted)
        reporter.record(Phase.GENERATION)
        with self.assertRaises(KeyboardInterrupt):
            reporter.flush()

    def test_latched_signal_prevents_more_output(self):
        calls = []
        reporter = ProgressReporter((1,), calls.append)
        with authoritative_cancellation_scope() as cancellation:
            cancellation.request()
            reporter.record(Phase.INTERRUPTED)
            with self.assertRaises(KeyboardInterrupt):
                reporter.flush()
        self.assertEqual(calls, [])

    def test_cancellation_returning_normally_from_output_also_propagates(self):
        with authoritative_cancellation_scope() as cancellation:
            reporter = ProgressReporter((1,), lambda _s: cancellation.request())
            reporter.record(Phase.GENERATION)
            with self.assertRaises(KeyboardInterrupt):
                reporter.flush()
            self.assertEqual(cancellation.interrupt_signal_count, 1)

    def test_translated_output_signal_retains_first_latch(self):
        with authoritative_cancellation_scope() as cancellation:
            def translated(_snapshot):
                cancellation.request()
                raise RuntimeError("synthetic transport translation")
            reporter = ProgressReporter((1,), translated)
            reporter.record(Phase.GENERATION)
            with self.assertRaises(KeyboardInterrupt):
                reporter.flush()
            self.assertTrue(cancellation.requested)
            self.assertEqual(cancellation.interrupt_signal_count, 1)

    def test_100_percent_is_not_command_success(self):
        reporter = ProgressReporter((1,))
        reporter.seed(((1, "completed"),))
        with self.assertRaises(KeyboardInterrupt), progress_scope(reporter):
            raise KeyboardInterrupt()
        self.assertEqual(reporter.snapshot.percent, 100)
        self.assertEqual(reporter.snapshot.event.phase, Phase.INTERRUPTED)
        self.assertNotIn("success", format_progress(reporter.snapshot))

    def test_scope_reset_prevents_cross_invocation_contamination(self):
        reporter = ProgressReporter((1,))
        with progress_scope(reporter):
            record_progress(Phase.GENERATION, order=1)
        sequence = reporter.snapshot.event.sequence
        record_progress(Phase.GENERATION, order=1)
        self.assertEqual(reporter.snapshot.event.sequence, sequence)

    def test_privacy_unknown_code_is_not_printed(self):
        reporter = ProgressReporter((1,))
        reporter.record(Phase.FAILED, code="synthetic private prompt\nhttps://example.invalid/?secret=value")
        text = format_progress(reporter.snapshot)
        self.assertIn("code=OPERATION_FAILED", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("\n", text)

    def test_redirected_output_is_plain_ascii_and_flushed(self):
        stream = StringIO()
        reporter = ProgressReporter((1,), terminal_progress(stream))
        reporter.record(Phase.GENERATION, order=1)
        reporter.flush()
        text = stream.getvalue()
        text.encode("ascii")
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)


class ProgressRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.batch = Batch("sha256:synthetic-progress", "synthetic", tuple(
            BatchItem(index, f"synthetic_visual_{index}", f"synthetic private prompt {index}", variants=1)
            for index in (1, 2, 3)
        ))
        self.settings = GenerationSettings(variants=1)
        self.output = []
        self.reporter = ProgressReporter((1, 2, 3), self.output.append)
        self.adapter = OfflinePromptAdapter()
        self.store = RunStateStore(self.run_dir / "run_state.json")

    def execute(self, **kwargs):
        return execute_sequential_batch(self.batch, self.run_dir, self.adapter, self.settings,
                                        progress=self.reporter, **kwargs)

    def phases(self):
        return [snapshot.event.phase for snapshot in self.output]

    def test_live_phases_and_final_counts(self):
        state, outcomes = self.execute()
        self.assertEqual(self.reporter.snapshot.counts["completed"], 3)
        self.assertEqual(self.reporter.snapshot.percent, 100)
        self.assertEqual(len(outcomes), 3)
        self.assertEqual(self.phases().count(Phase.GENERATION_SAVED), 3)
        self.assertEqual(self.phases().count(Phase.CANDIDATE_SAVED), 3)
        self.assertEqual(self.phases().count(Phase.COMPLETE), 3)
        for snapshot in self.output:
            self.assertEqual(sum(snapshot.counts.values()), 3)
            self.assertLessEqual(snapshot.counts["active"], 1)
        rendered = "\n".join(map(format_progress, self.output))
        self.assertNotIn("synthetic private prompt", rendered)
        self.assertNotIn("synthetic_visual", rendered)
        self.assertNotIn(str(self.run_dir), rendered)
        self.assertNotIn(state.items[0].candidates[0].candidate_id, rendered)

    def test_generation_phase_is_visible_before_blocking_adapter_call(self):
        generate = self.adapter.generate_one
        def observe(**kwargs):
            self.assertEqual(self.output[-1].event.phase, Phase.GENERATION)
            self.assertEqual(self.output[-1].event.order, kwargs["item"].order)
            return generate(**kwargs)
        with patch.object(self.adapter, "generate_one", side_effect=observe):
            self.execute()

    def test_mixed_state_seed_counts_full_orders_not_generations(self):
        for order in (1, 2, 3):
            execute_one(self.batch, self.run_dir, order, self.adapter, self.settings)
            if order != 2:
                execute_download_one(self.batch, self.run_dir, order, self.adapter)
        state = self.store.load()
        incomplete = state.items[1]
        incomplete.download_status = "failed"
        incomplete.candidates[0].download_status = "failed"
        self.store.save(state)
        self.output.clear()
        self.execute()
        ready = next(s for s in self.output if s.event.phase == Phase.READY)
        self.assertEqual(ready.counts["completed"], 2)
        self.assertEqual(ready.counts["failed_or_blocked"], 1)
        self.assertEqual(ready.percent, 66)

    def test_ambiguous_failure_is_not_a_confirmed_failure_message_or_retry(self):
        from nano_batch.adapters.flow import FlowFailureAttributionAmbiguous
        with patch.object(self.adapter, "generate_one", side_effect=FlowFailureAttributionAmbiguous()) as generate:
            with self.assertRaises(FlowFailureAttributionAmbiguous):
                self.execute()
        self.assertEqual(generate.call_count, 1)
        rendered = "\n".join(map(format_progress, self.output))
        self.assertIn("FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS", rendered)
        self.assertNotIn("explicit_failure", rendered)
        self.assertEqual(self.store.load().items[0].attempt_count, 1)

    def test_skip_only_resume_is_read_only_and_adapter_free(self):
        self.execute()
        before = self.store.path.read_bytes()
        self.adapter.events.clear()
        self.output.clear()
        self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertNotIn(Phase.GENERATION, self.phases())
        self.assertEqual(self.phases().count(Phase.SKIP_GENERATION), 3)
        self.assertEqual(next(s for s in self.output if s.verified).percent, 100)

    def test_download_only_resume_never_regenerates(self):
        execute_one(self.batch, self.run_dir, 1, self.adapter, self.settings)
        self.adapter.events.clear()
        final, _ = self.execute()
        self.assertNotIn(("generate", 1, self.batch.items[0].prompt, 1), self.adapter.events)
        first = next(s for s in self.output if s.event.phase == Phase.SKIP_GENERATION)
        self.assertEqual(first.percent, 0)
        self.assertEqual(final.items[0].attempt_count, 1)

    def test_completed_assets_do_not_waive_ownership_validation(self):
        self.execute()
        state = self.store.load()
        state.items[1].candidates[0].candidate_id = state.items[0].candidates[0].candidate_id
        self.store.save(state)
        before = self.store.path.read_bytes()
        self.adapter.events.clear()
        self.output.clear()
        self.reporter = ProgressReporter((1, 2, 3), self.output.append)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(self.reporter.snapshot.verified)
        self.assertNotIn(Phase.COMPLETE, self.phases())

    def test_interrupted_running_state_is_blocked_not_active(self):
        state = initialize_run_state(self.batch, self.run_dir)
        state.items[0].status, state.items[0].attempt_count = "running", 1
        self.store.save(state)
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "retry required"):
            self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.reporter.snapshot.counts["active"], 0)
        self.assertEqual(self.reporter.snapshot.counts["failed_or_blocked"], 1)

    def test_usage_limit_stops_without_later_orders_or_retry(self):
        def limited(**_kwargs):
            raise TemporaryGenerationBlock("synthetic", code=FLOW_USAGE_LIMIT_REACHED,
                state_status=WAITING_FOR_FLOW_LIMIT_RESET, attempt_activated=True)
        with patch.object(self.adapter, "generate_one", side_effect=limited) as generate:
            with self.assertRaises(TemporaryGenerationBlock):
                self.execute()
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.reporter.snapshot.counts["waiting"], 1)
        self.assertEqual(self.reporter.snapshot.counts["pending"], 2)
        self.assertEqual(self.store.load().items[0].attempt_count, 1)

    def test_unknown_failure_preserves_error_and_does_not_retry(self):
        error = RuntimeError("synthetic confidential diagnostic")
        with patch.object(self.adapter, "generate_one", side_effect=error) as generate:
            with self.assertRaises(RuntimeError) as caught:
                self.execute()
        self.assertIs(caught.exception, error)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.reporter.snapshot.counts["failed_or_blocked"], 1)
        self.assertNotIn("confidential", "\n".join(map(format_progress, self.output)))

    def test_exception_metadata_failure_cannot_hide_original_error(self):
        class OriginalError(RuntimeError):
            @property
            def code(self):
                raise ValueError("synthetic metadata failure")
        error = OriginalError("synthetic original error")
        with patch.object(self.adapter, "generate_one", side_effect=error):
            with self.assertRaises(OriginalError) as caught:
                self.execute()
        self.assertIs(caught.exception, error)
        self.assertTrue(self.reporter.recording_failed)

    def test_resume_automatic_retry_reports_existing_authorization_once(self):
        for failure, budget in (("FLOW_POLICY_REJECTED", "automatic_policy_retry_count"),
                                ("FLOW_GENERIC_GENERATION_FAILED", "automatic_generic_failure_retry_count")):
            with self.subTest(failure=failure):
                run = self.root / failure
                state = initialize_run_state(self.batch, run)
                state.items[0].status = "failed"
                state.items[0].attempt_count = 1
                state.items[0].generation_error_code = failure
                RunStateStore(run / "run_state.json").save(state)
                snapshots = []
                reporter = ProgressReporter((1, 2, 3), snapshots.append)
                final, _ = execute_sequential_batch(self.batch, run, OfflinePromptAdapter(), self.settings, progress=reporter)
                self.assertEqual(sum(s.event.phase == Phase.RETRY for s in snapshots), 1)
                self.assertEqual(final.items[0].attempt_count, 2)
                self.assertEqual(getattr(final.items[0], budget), 1)

    def test_partial_x2_download_retains_success_without_complete_event(self):
        self.batch = Batch(self.batch.batch_id, "synthetic", (BatchItem(1, "synthetic", "synthetic", variants=2),))
        self.settings = GenerationSettings(variants=2)
        self.reporter = ProgressReporter((1,), self.output.append)
        download = self.adapter.download_candidate
        def fail_second(**kwargs):
            if kwargs["candidate"].variant == 2:
                raise RuntimeError("synthetic download failure")
            return download(**kwargs)
        with patch.object(self.adapter, "download_candidate", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.execute()
        state = self.store.load()
        self.assertEqual(state.items[0].candidates[0].download_status, "completed")
        self.assertEqual(self.reporter.snapshot.percent, 0)
        self.assertNotIn(Phase.COMPLETE, self.phases())
        self.output.clear()
        self.execute()
        self.assertEqual(self.reporter.snapshot.percent, 100)
        self.assertEqual(self.phases().count(Phase.CANDIDATE_SAVED), 1)

    def test_reporting_exception_does_not_change_execution(self):
        with patch.object(self.reporter, "record", side_effect=ValueError("synthetic observer bug")):
            state, _ = self.execute()
        self.assertTrue(self.reporter.recording_failed)
        self.assertTrue(all(item.download_status == "completed" for item in state.items))

    def test_broken_output_does_not_consume_extra_attempts(self):
        self.reporter = ProgressReporter((1, 2, 3), lambda _s: (_ for _ in ()).throw(OSError("synthetic")))
        state, _ = self.execute()
        self.assertTrue(self.reporter.output_failed)
        self.assertEqual([item.attempt_count for item in state.items], [1, 1, 1])

    def test_cancellation_before_dispatch_never_calls_adapter(self):
        with authoritative_cancellation_scope() as cancellation:
            cancellation.request()
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assertEqual(self.adapter.events, [])
        self.assertFalse(self.store.exists())
        self.assertEqual(self.reporter.snapshot.event.phase, Phase.INTERRUPTED)
        self.assertNotIn(Phase.RETURNED, self.phases())

    def test_checkpoint_records_no_io_and_interruption_preserves_download_only_resume(self):
        reporter, output, store = self.reporter, self.output, self.store
        class CheckpointAdapter(OfflinePromptAdapter):
            @contextmanager
            def generation_completion_checkpoint(self, callback):
                self.checkpoint = callback
                yield
            def generate_one(self, **kwargs):
                result = super().generate_one(**kwargs)
                self.checkpoint(result)
                self.saved = store.load().items[0]
                assert reporter.snapshot.event.phase == Phase.GENERATION_SAVED
                assert not any(s.event.phase == Phase.GENERATION_SAVED for s in output)
                raise cancellation.request()
        self.adapter = CheckpointAdapter()
        with authoritative_cancellation_scope() as cancellation:
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assertEqual(self.adapter.saved.status, "completed")
        self.assertEqual(self.reporter.snapshot.percent, 0)
        self.assertEqual(self.reporter.snapshot.counts["stopped"], 1)
        self.adapter = OfflinePromptAdapter()
        self.reporter = ProgressReporter((1, 2, 3), self.output.append)
        self.execute()
        self.assertFalse(any(e[0] == "generate" and e[1] == 1 for e in self.adapter.events))

    def test_failed_completion_save_never_emits_generation_saved(self):
        save = RunStateStore.save
        def fail_completion(store, state):
            if state.items[0].status == "completed":
                raise OSError("synthetic persistence failure")
            return save(store, state)
        with patch.object(RunStateStore, "save", new=fail_completion):
            with self.assertRaises(OSError):
                self.execute()
        self.assertNotIn(Phase.GENERATION_SAVED, self.phases())
        self.assertEqual(self.store.load().items[0].status, "running")
        self.assertEqual(self.reporter.snapshot.percent, 0)

    def test_cancelled_download_preserves_durable_generation_and_stops_later_orders(self):
        with authoritative_cancellation_scope() as cancellation:
            with patch.object(self.adapter, "download_candidate", side_effect=lambda **_kw: (_ for _ in ()).throw(cancellation.request())):
                with self.assertRaises(KeyboardInterrupt):
                    self.execute()
        saved = self.store.load()
        self.assertEqual(saved.items[0].status, "completed")
        self.assertEqual(saved.items[0].attempt_count, 1)
        self.assertTrue(saved.items[0].candidates[0].candidate_id)
        self.assertEqual(saved.items[1].attempt_count, 0)
        self.assertEqual(self.reporter.snapshot.percent, 0)
        self.assertEqual(self.reporter.snapshot.event.phase, Phase.INTERRUPTED)

    def test_output_interrupt_after_order_completion_preserves_saved_work(self):
        with authoritative_cancellation_scope() as cancellation:
            def interrupt(snapshot):
                if snapshot.event.phase == Phase.COMPLETE:
                    raise cancellation.request()
            self.reporter = ProgressReporter((1, 2, 3), interrupt)
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        state = self.store.load()
        self.assertEqual(state.items[0].download_status, "completed")
        self.assertEqual(state.items[1].attempt_count, 0)
        self.assertEqual(self.reporter.snapshot.counts["completed"], 1)
        self.assertEqual(self.reporter.snapshot.event.phase, Phase.INTERRUPTED)

    def test_canonical_only_receipt_recovery_without_duplicate_download(self):
        fixture = make_pending_download_fixture(self.run_dir)
        commit_staged_file(fixture.staging_path, fixture.canonical_path, overwrite=False)
        if fixture.staging_path.exists():
            fixture.staging_path.unlink()
        self.batch = fixture.batch
        self.reporter = ProgressReporter((1, 2), self.output.append)
        final, _ = self.execute()
        self.assertIn(Phase.RECONCILE, self.phases())
        self.assertFalse(any(e[0] in {"generate", "download"} and e[1] == 1 for e in self.adapter.events))
        self.assertTrue(all(item.download_status == "completed" for item in final.items))

    def test_enabled_disabled_have_identical_calls_and_saved_results(self):
        enabled, _ = self.execute()
        calls = copy.deepcopy(self.adapter.events)
        adapter = OfflinePromptAdapter()
        disabled_dir = self.root / "disabled"
        disabled, _ = execute_sequential_batch(self.batch, disabled_dir, adapter, self.settings)
        def normalized(value):
            if isinstance(value, dict):
                return {k: normalized(v) for k, v in value.items() if not k.endswith("_at")}
            if isinstance(value, list):
                return [normalized(v) for v in value]
            if isinstance(value, str):
                return value.replace(str(self.run_dir.resolve()), "RUN").replace(str(disabled_dir.resolve()), "RUN")
            return value
        self.assertEqual(calls, adapter.events)
        self.assertEqual(normalized(asdict(enabled)), normalized(asdict(disabled)))

    def test_durable_retry_events_do_not_change_existing_budgets(self):
        for failure in ("policy", "generic", "unusual"):
            with self.subTest(failure=failure):
                run = self.root / failure
                state = initialize_run_state(self.batch, run, require_flow_project=True)
                project_id = "00000000-0000-4000-8000-000000000001"
                state.flow_project = FlowProjectReference(project_id, f"https://flow.google.com/project/{project_id}")
                RunStateStore(run / "run_state.json").save(state)
                snapshots = []
                reporter = ProgressReporter((1, 2, 3), snapshots.append)
                class RetryingAdapter(OfflinePromptAdapter):
                    def generate_one_with_unusual_activity_recovery(self, *, item, settings,
                            forbidden_candidate_ids, retry_started, generic_failure_retry_started,
                            policy_failure_retry_started, **_kwargs):
                        if item.order == 1:
                            if failure == "policy":
                                assert policy_failure_retry_started(PolicyGenerationRejected("synthetic"))
                            elif failure == "generic":
                                assert generic_failure_retry_started(GenericGenerationFailure("synthetic"))
                            else:
                                retry_started()
                            saved = RunStateStore(run / "run_state.json").load().items[0]
                            assert saved.attempt_count == 2
                            assert reporter.snapshot.event.phase == Phase.RETRY
                            assert not any(s.event.phase == Phase.RETRY for s in snapshots)
                        return self.generate_one(item=item, settings=settings, forbidden_candidate_ids=forbidden_candidate_ids)
                final, _ = execute_sequential_batch(self.batch, run, RetryingAdapter(), self.settings, progress=reporter)
                self.assertEqual(final.items[0].attempt_count, 2)
                self.assertEqual(final.items[0].automatic_policy_retry_count, int(failure == "policy"))
                self.assertEqual(final.items[0].automatic_generic_failure_retry_count, int(failure == "generic"))
                self.assertEqual(sum(s.event.phase == Phase.RETRY for s in snapshots), 1)

    def test_cli_writes_progress_to_stdout_and_preview_stays_read_only(self):
        args = argparse.Namespace(run_dir=str(self.run_dir), config=str(self.root / "absent.json"),
                                  timeout=None, retry=False, preview=False, progress_format="detailed")
        for preview in (True, False):
            args.preview = preview
            with patch.object(app, "_flow_session_from_args", return_value=self.adapter) as factory, \
                 redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()) as stderr:
                self.assertEqual(app._run_sequential_command(self.batch, args, prompt_wrapper=True), 0)
            if preview:
                factory.assert_not_called()
                self.assertFalse(self.run_dir.exists())
                self.assertNotIn("PROGRESS", stderr.getvalue())
            else:
                self.assertIn("PROGRESS", stdout.getvalue())
                self.assertNotIn("PROGRESS", stderr.getvalue())

    def test_windows_powershell_strict_redirect_does_not_treat_progress_as_error(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("Windows PowerShell is required for native stream compatibility check.")
        smoke = self.root / "progress_smoke.py"
        smoke.write_text(
            "import sys\n"
            "from nano_batch.progress import ProgressReporter,ProgressPhase,terminal_progress\n"
            "reporter=ProgressReporter((1,),terminal_progress(sys.stdout))\n"
            "reporter.seed(((1,'pending'),))\n"
            "reporter.record(ProgressPhase.GENERATION,order=1)\n"
            "reporter.flush()\n", encoding="utf-8",
        )
        # Exercise the normal native-command path without changing execution
        # policy or requiring permission to execute a generated .ps1 file.
        python_arg = "'" + sys.executable.replace("'", "''") + "'"
        smoke_arg = "'" + str(smoke).replace("'", "''") + "'"
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"& {python_arg} -B {smoke_arg} 2>&1 | Out-Null; exit $LASTEXITCODE"
        )
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            env=env, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, "Native progress stream was treated as an error.")
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
