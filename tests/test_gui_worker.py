"""Worker execution uses synthetic adapters and disposable fixtures only."""

import io
import signal
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from PIL import Image

from nano_batch.batch import load_prompts_file
from nano_batch.cancellation import (
    authoritative_cancellation_scope, cancellation_checkpoint, current_cancellation_state,
    interruptible_call,
)
from nano_batch.errors import TemporaryGenerationBlock, GenericGenerationFailure
from nano_batch.gui_worker import PipeEmitter, StopBridge, run_job, serve
from nano_batch.models import BrowserDownloadResult, DiscoveredCandidate, FlowProjectReference, GenerationResult
from nano_batch.runner import initialize_run_state, plan_sequential_batch
from nano_batch.models import GenerationSettings
from nano_batch.state import RunStateStore
from nano_batch.worker_protocol import JobMode, JobRequest, StopRequest, decode_message, encode, message
from pending_download_fixtures import pending_download_test_directory


class SyntheticAdapter:
    def __init__(self):
        self.generations = self.downloads = 0
        self.before_generation = self.after_checkpoint = self.during_download = lambda: None
        self.threads = []

    @contextmanager
    def generation_completion_checkpoint(self, callback):
        self.checkpoint = callback
        try:
            yield
        finally:
            self.checkpoint = None

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        self.threads.append(threading.current_thread())
        self.generations += 1
        self.before_generation()
        result = GenerationResult(candidates=(DiscoveredCandidate("rendered-image", candidate_id="00000000-0000-4000-8000-000000000101"),))
        self.checkpoint(result)
        self.after_checkpoint()
        return result

    def download_candidate(self, *, candidate, destination):
        self.threads.append(threading.current_thread())
        self.downloads += 1
        self.during_download()
        Image.new("RGB", (16, 9), (10, 20, 30)).save(destination, format="JPEG")
        return BrowserDownloadResult(candidate_id=candidate.candidate_id, staged_path=str(destination),
                                     browser_suggested_filename="synthetic.jpeg", browser_download_guid="synthetic-guid",
                                     mechanism="cdp_download_events")


class SyntheticInput:
    def __init__(self):
        self.frames = Queue()

    def readline(self, limit):
        return self.frames.get()[:limit]


class GuiWorkerTests(unittest.TestCase):
    def setUp(self):
        directory = pending_download_test_directory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.prompts = self.root / "synthetic \u732b prompts.txt"
        self.prompts.write_text("Synthetic still life.\n", encoding="utf-8")
        self.run = self.root / "synthetic run"
        self.config = self.root / "synthetic-config.json"
        self.config.write_text('{"generation":{"variants":2,"aspect_ratio":"16:9"}}', encoding="utf-8")
        self.request = JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config))
        self.adapter = SyntheticAdapter()
        self.stop = StopBridge()
        self.messages = []
        self.batch = load_prompts_file(self.prompts)
        self.store = RunStateStore(self.run / "run_state.json")

    def execute(self, *, request=None):
        return run_job(request or self.request, self.messages.append, self.stop,
                       adapter_factory=lambda _args, _config: self.adapter).as_dict()

    def modern_state(self):
        state = initialize_run_state(self.batch, self.run, require_flow_project=True)
        project = "00000000-0000-4000-8000-000000000999"
        state.flow_project = FlowProjectReference(project, f"https://flow.google.com/project/{project}")
        self.store.save(state)

    def test_normal_execution_reuses_runner_x1_settings_and_worker_main_thread(self):
        result = self.execute()
        self.assertEqual((result["outcome"], result["exit_code"]), ("completed", 0))
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))
        self.assertTrue(all(thread is threading.main_thread() for thread in self.adapter.threads))
        state = self.store.load()
        self.assertEqual(state.items[0].status, "completed")
        self.assertEqual(state.items[0].download_status, "completed")
        self.assertEqual(len(state.items[0].candidates), 1)
        self.assertTrue(any(value.kind == "preview" for value in self.messages))
        self.assertTrue(any(value.kind == "progress" for value in self.messages))

    def test_preview_never_calls_adapter_or_creates_run(self):
        from dataclasses import replace
        with patch("app._flow_session_from_args", side_effect=AssertionError("adapter forbidden")):
            result = run_job(replace(self.request, mode=JobMode.PREVIEW), self.messages.append, self.stop).as_dict()
        self.assertEqual(result["outcome"], "preview_complete")
        self.assertFalse(self.run.exists())

    def test_stop_before_submission_has_zero_attempts_and_no_state_creation(self):
        self.assertEqual(self.stop.request(), "pending")
        self.assertEqual(self.stop.request(), "duplicate")
        result = self.execute()
        self.assertEqual(result["exit_code"], 130)
        self.assertFalse(self.run.exists())
        self.assertEqual(self.adapter.generations, 0)

    def test_stop_during_generation_wait_blocks_resume_without_duplicate(self):
        def stop_wait():
            self.stop.request()
            cancellation_checkpoint("result_wait")
        self.adapter.before_generation = stop_wait
        self.assertEqual(self.execute()["outcome"], "interrupted")
        item = self.store.load().items[0]
        self.assertEqual((item.status, item.attempt_count, item.candidates), ("running", 1, []))
        self.stop = StopBridge()
        self.assertEqual(self.execute()["outcome"], "blocked")
        self.assertEqual(self.adapter.generations, 1)

    def test_stop_after_durable_checkpoint_preserves_download_only_resume(self):
        self.modern_state()
        def stop_after_checkpoint():
            self.stop.request()
            cancellation_checkpoint("generation_cleanup")
        self.adapter.after_checkpoint = stop_after_checkpoint
        self.assertEqual(self.execute()["exit_code"], 130)
        item = self.store.load().items[0]
        self.assertEqual((item.status, item.attempt_count, len(item.candidates)), ("completed", 1, 1))
        self.stop = StopBridge()
        self.adapter.after_checkpoint = lambda: None
        self.assertEqual(self.execute()["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_stop_during_downloading_never_marks_partial_file_complete(self):
        self.modern_state()
        def stop_download():
            self.stop.request()
            cancellation_checkpoint("download_wait")
        self.adapter.during_download = stop_download
        self.assertEqual(self.execute()["exit_code"], 130)
        item = self.store.load().items[0]
        self.assertEqual(item.status, "completed")
        self.assertNotEqual(item.download_status, "completed")
        self.assertEqual(item.attempt_count, 1)
        self.assertFalse(any((self.run / "assets").glob("*.jpeg")))

    def test_stop_after_canonical_install_preserves_pending_receipt_recovery(self):
        import nano_batch.runner as runner
        self.modern_state()
        install = runner.commit_staged_file
        def stop_after_install(*args, **kwargs):
            result = install(*args, **kwargs)
            self.stop.request()
            return result
        with patch.object(runner, "commit_staged_file", side_effect=stop_after_install):
            self.assertEqual(self.execute()["exit_code"], 130)
        item = self.store.load().items[0]
        self.assertIsNotNone(item.candidates[0].pending_download_commit)
        self.assertEqual(len(list((self.run / "assets").glob("*.jpeg"))), 1)
        self.stop = StopBridge()
        self.assertEqual(self.execute()["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_stop_during_local_cleanup_keeps_committed_asset_and_attempts(self):
        import nano_batch.runner as runner
        self.modern_state()
        cleanup = runner._cleanup_download_staging
        def stop_cleanup(*args):
            self.stop.request()
            return cleanup(*args)
        with patch.object(runner, "_cleanup_download_staging", side_effect=stop_cleanup):
            self.assertEqual(self.execute()["exit_code"], 130)
        item = self.store.load().items[0]
        self.assertEqual(item.candidates[0].download_status, "completed")
        self.stop = StopBridge()
        self.assertEqual(self.execute()["outcome"], "completed")
        self.assertEqual((self.adapter.generations, self.adapter.downloads), (1, 1))

    def test_late_stop_after_execution_is_finished_cannot_interrupt_next_operation(self):
        self.assertEqual(self.execute()["outcome"], "completed")
        self.assertEqual(self.stop.request(), "finished")
        with authoritative_cancellation_scope() as state:
            cancellation_checkpoint("unrelated_operation")
            self.assertFalse(state.requested)

    def test_unknown_private_exception_is_fixed_terminal_without_stringifying(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("private exception must not be formatted")
        self.adapter.before_generation = lambda: (_ for _ in ()).throw(PrivateError())
        result = self.execute()
        self.assertEqual((result["outcome"], result["code"]), ("failed", "OPERATION_FAILED"))
        self.assertNotIn("Synthetic still", repr(self.messages) + repr(result))
        self.assertNotIn(str(self.root), repr(self.messages) + repr(result))

    def test_usage_limit_and_retry_required_are_distinct_from_success(self):
        self.adapter.before_generation = lambda: (_ for _ in ()).throw(TemporaryGenerationBlock(
            "synthetic", code="FLOW_USAGE_LIMIT_REACHED", state_status="waiting_for_flow_limit_reset", attempt_activated=True))
        self.assertEqual(self.execute()["outcome"], "waiting")
        self.assertEqual(self.adapter.generations, 1)

    def test_existing_generic_failure_budget_is_not_reset_or_expanded(self):
        self.adapter.before_generation = lambda: (_ for _ in ()).throw(GenericGenerationFailure("synthetic"))
        self.assertEqual(self.execute()["outcome"], "failed")
        item = self.store.load().items[0]
        self.assertEqual((self.adapter.generations, item.attempt_count, item.automatic_generic_failure_retry_count), (3, 3, 2))
        self.stop = StopBridge()
        self.assertEqual(self.execute()["outcome"], "blocked")
        self.assertEqual(self.adapter.generations, 3)

    def test_stop_wins_over_failure_and_does_not_authorize_another_submission(self):
        def stopped_failure():
            self.stop.request()
            raise GenericGenerationFailure("synthetic")
        self.adapter.before_generation = stopped_failure
        self.assertEqual(self.execute()["exit_code"], 130)
        item = self.store.load().items[0]
        self.assertEqual((self.adapter.generations, item.attempt_count, item.automatic_generic_failure_retry_count), (1, 1, 0))
        self.assertEqual(item.status, "running")

    def test_worker_rejects_execution_on_control_thread(self):
        errors = []
        def wrong_thread():
            try:
                self.execute()
            except RuntimeError as exc:
                errors.append(type(exc))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(2)
        self.assertEqual(errors, [RuntimeError])
        self.assertFalse(self.run.exists())

    def test_stop_during_blocked_operation_remains_pending_until_return(self):
        entered, release = threading.Event(), threading.Event()
        facts = []
        def blocked():
            entered.set()
            self.assertTrue(release.wait(5))
        def controller():
            if entered.wait(5):
                facts.append(self.stop.request())
                facts.append(current_state.requested)
                release.set()
        with authoritative_cancellation_scope(cooperative_stop=self.stop.event) as current_state:
            thread = threading.Thread(target=controller)
            thread.start()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    interruptible_call("result_wait", "synthetic_blocked", blocked)
            finally:
                release.set()
                thread.join(5)
            self.assertEqual(facts, ["pending", False])
            self.assertEqual(current_state.interrupt_signal_count, 1)

    def test_translated_sdk_error_after_stop_remains_interruption(self):
        with authoritative_cancellation_scope(cooperative_stop=self.stop.event) as state:
            def translated():
                self.stop.request()
                raise RuntimeError("synthetic transport translation")
            with self.assertRaises(KeyboardInterrupt):
                interruptible_call("result_wait", "synthetic_sdk", translated)
            self.assertEqual(state.interrupt_signal_count, 1)

    def test_sigint_remains_immediate_and_preserves_first_signal(self):
        with authoritative_cancellation_scope(cooperative_stop=self.stop.event) as state:
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            first = state.received_phase
            self.stop.request()
            with self.assertRaises(KeyboardInterrupt):
                cancellation_checkpoint("later")
            self.assertEqual((state.interrupt_signal_count, state.received_phase), (1, first))

    def test_output_backpressure_never_blocks_execution_producer(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_write(_frame):
            entered.set()
            release.wait(5)
        emitter = PipeEmitter(blocked_write, self.stop)
        emitter.offer(message("ready"))
        self.assertTrue(entered.wait(2))
        try:
            for _ in range(100):
                emitter.offer(message("stop_ack", status="duplicate"))
            self.assertFalse(emitter.finish(timeout=0))
            self.assertFalse(self.stop.event.is_set())
        finally:
            release.set()
            emitter._thread.join(2)

    def test_pipe_failure_requests_cooperative_stop(self):
        def failed(_frame):
            raise BrokenPipeError()
        emitter = PipeEmitter(failed, self.stop)
        emitter.offer(message("ready"))
        self.assertTrue(emitter._done.wait(2))
        self.assertTrue(self.stop.event.is_set())

    def test_serve_stop_before_job_reports_readiness_pending_then_interrupted(self):
        source = SyntheticInput()
        source.frames.put(encode(StopRequest()))
        output = []
        self.assertEqual(serve(source, lambda frame: output.append(decode_message(frame))), 130)
        self.assertEqual([item.kind for item in output], ["ready", "stop_ack", "terminal"])
        self.assertEqual(dict(output[1].fields)["status"], "pending")

    def test_lost_or_malformed_control_channel_fails_without_adapter(self):
        for frame in (b"", b"invalid\n", b" " * 9000):
            source = SyntheticInput()
            source.frames.put(frame)
            output = []
            serve(source, lambda frame: output.append(decode_message(frame)),
                  adapter_factory=lambda *_args: (_ for _ in ()).throw(AssertionError("adapter forbidden")))
            self.assertNotEqual(dict(output[-1].fields)["outcome"], "completed")
            self.assertEqual(dict(output[-1].fields)["code"], "CHANNEL_LOST" if not frame else "PROTOCOL_ERROR")
