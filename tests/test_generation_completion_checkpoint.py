"""Persist proven generation completion before cleanup; never adopt later history."""

import copy
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from nano_batch.adapters.flow import (
    FlowSession, FlowGenericGenerationFailed, FlowPolicyRejected, FlowUnusualActivity,
)
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.models import (
    Batch, BatchItem, BrowserDownloadResult, CandidateRecord, DiscoveredCandidate,
    FlowProjectReference, GenerationSettings,
)
from nano_batch.runner import execute_one, execute_sequential_batch, initialize_run_state, plan_sequential_batch
from nano_batch.state import RunStateStore
from pending_download_fixtures import pending_download_test_directory


UUID = "00000000-0000-4000-8000-000000000101"
PROJECT = "00000000-0000-4000-8000-000000000999"


class GenerationCompletionCheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"
        self.batch = Batch("sha256:completion-checkpoint", "synthetic", (
            BatchItem(1, "synthetic_visual", "synthetic still life", variants=1),
        ))
        self.settings = GenerationSettings(variants=1)
        self.store = RunStateStore(self.run_dir / "run_state.json")
        state = initialize_run_state(self.batch, self.run_dir, require_flow_project=True)
        state.flow_project = FlowProjectReference(PROJECT, f"https://flow.google.com/project/{PROJECT}")
        self.store.save(state)
        self.session = FlowSession(state.flow_project.project_url, None)
        self.submissions = 0

    @contextmanager
    def offline_session(self, *, cleanup=None, wait=None, submit=None):
        self.session.page = MagicMock()
        def acknowledge(*args, **kwargs):
            self.submissions += 1
            self.session._set_generation_interrupt_checkpoint(
                "start_acknowledged", start_mouse_down_sent=True, start_acknowledged=True,
            )
            return {}
        with ExitStack() as stack:
            for name, value in {
                "open": self.session,
                "ensure_production_window_usable": None,
                "configure_generation": {"configured_variants": 1, "requested_variants": 1},
                "_asset_snapshot": [], "_progress_signals": [],
                "wait_for_result": (DiscoveredCandidate("rendered-image", candidate_id=UUID),),
                "_stop_current_generation_attempt_observation": None,
            }.items():
                mocked = stack.enter_context(patch.object(self.session, name, return_value=value))
                if name == "wait_for_result" and wait is not None:
                    mocked.side_effect = wait
                if name == "_stop_current_generation_attempt_observation" and cleanup is not None:
                    mocked.side_effect = cleanup
            stack.enter_context(patch.object(self.session, "submit_prompt", side_effect=submit or acknowledge))
            stack.enter_context(patch.object(
                self.session, "_recover_unusual_activity_in_open_session",
                side_effect=AssertionError("completed generation must not navigate/retry"),
            ))
            yield

    def execute(self, **kwargs):
        return execute_one(self.batch, self.run_dir, 1, self.session, self.settings, **kwargs)

    def assert_completed(self):
        state = self.store.load()
        item = state.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.attempt_count, 1)
        self.assertEqual(item.candidates[0].candidate_id, UUID)
        self.assertEqual(item.automatic_policy_retry_count, 0)
        self.assertEqual(item.automatic_generic_failure_retry_count, 0)
        self.assertEqual(state.flow_project.project_id, PROJECT)
        self.assertEqual(item.download_status, "not_started")
        self.assertEqual(self.submissions, 1)
        self.assertIsNone(getattr(self.session, "_generation_result_checkpoint", None))
        return state

    def test_observer_cleanup_interrupt_preserves_proven_completed_generation(self):
        with authoritative_cancellation_scope() as cancellation:
            with self.offline_session(cleanup=lambda: (_ for _ in ()).throw(cancellation.request())):
                with self.assertRaises(KeyboardInterrupt):
                    self.execute()
        self.assert_completed()

    def test_driver_stop_interrupt_preserves_proven_completed_generation(self):
        with authoritative_cancellation_scope() as cancellation:
            def stop():
                raise cancellation.request()
            self.session._playwright = SimpleNamespace(stop=stop)
            with self.offline_session(), self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assert_completed()

    def test_translated_stop_interrupt_preserves_completion_and_cancellation(self):
        with authoritative_cancellation_scope() as cancellation:
            def stop():
                cancellation.request()
                raise RuntimeError("synthetic transport translation")
            self.session._playwright = SimpleNamespace(stop=stop)
            with self.offline_session(), self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assert_completed()

    def test_completion_is_saved_once_before_observer_cleanup(self):
        seen, completed_saves = [], []
        save = RunStateStore.save
        def record_save(store, state):
            if state.items[0].status == "completed":
                completed_saves.append(copy.deepcopy(state))
            return save(store, state)
        def inspect_cleanup():
            seen.append(self.store.load().items[0].status)
        with patch.object(RunStateStore, "save", new=record_save), self.offline_session(cleanup=inspect_cleanup):
            self.execute()
        self.assertEqual(seen, ["completed"])
        self.assertEqual(len(completed_saves), 1)
        self.assert_completed()

    def test_interruption_after_ack_without_uuid_remains_blocked_even_if_flow_finishes(self):
        with authoritative_cancellation_scope() as cancellation:
            def interrupted(*args, **kwargs):
                raise cancellation.request()
            with self.offline_session(wait=interrupted), self.assertRaises(KeyboardInterrupt):
                self.execute()
        state = self.store.load()
        self.assertEqual(state.items[0].status, "running")
        self.assertEqual(state.items[0].candidates, [])
        self.assertEqual(plan_sequential_batch(self.batch, self.run_dir, self.settings).orders[0].generation_action, "blocked_retry_required")
        # A fresh adapter could see the later image. Resume must not consult it.
        later = MagicMock(spec=["generate_one", "download_candidate"])
        before = self.store.path.read_bytes()
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "Generation retry required"):
                execute_sequential_batch(self.batch, self.run_dir, later, self.settings)
        self.assertEqual(later.mock_calls, [])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_ambiguous_acknowledgement_remains_blocked(self):
        with authoritative_cancellation_scope() as cancellation:
            def ambiguous(*args, **kwargs):
                self.submissions += 1
                self.session._set_generation_interrupt_checkpoint("start_mouse_down", start_mouse_down_sent=True)
                raise cancellation.request()
            with self.offline_session(submit=ambiguous), self.assertRaises(KeyboardInterrupt):
                self.execute()
        state = self.store.load()
        self.assertEqual(state.items[0].status, "running")
        self.assertEqual(state.items[0].candidates, [])
        self.assertEqual(self.submissions, 1)

    def test_pre_start_interrupt_stays_pending_without_consuming_attempt(self):
        with authoritative_cancellation_scope() as cancellation:
            with self.offline_session(), patch.object(
                self.session, "_asset_snapshot", side_effect=lambda: (_ for _ in ()).throw(cancellation.request()),
            ), self.assertRaises(KeyboardInterrupt):
                self.execute()
        item = self.store.load().items[0]
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.attempt_count, 0)
        self.assertEqual(self.submissions, 0)

    def test_interrupt_before_completion_replace_keeps_last_durable_running_state(self):
        save = RunStateStore.save
        with authoritative_cancellation_scope() as cancellation:
            def interrupted_save(store, state):
                if state.items[0].status == "completed":
                    raise cancellation.request()
                return save(store, state)
            with patch.object(RunStateStore, "save", new=interrupted_save), self.offline_session(), self.assertRaises(KeyboardInterrupt):
                self.execute()
        item = self.store.load().items[0]
        self.assertEqual(item.status, "running")
        self.assertEqual(item.candidates, [])

    def test_interrupt_after_completion_replace_preserves_durable_uuid(self):
        save = RunStateStore.save
        with authoritative_cancellation_scope() as cancellation:
            def interrupted_save(store, state):
                result = save(store, state)
                if state.items[0].status == "completed":
                    raise cancellation.request()
                return result
            with patch.object(RunStateStore, "save", new=interrupted_save), self.offline_session(), self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assert_completed()

    def test_historical_candidate_conflict_is_not_checkpointed(self):
        state = self.store.load()
        neighbor = copy.deepcopy(state.items[0])
        neighbor.order, neighbor.visual_id = 2, "synthetic_history"
        neighbor.status, neighbor.attempt_count = "completed", 1
        neighbor.candidates = [CandidateRecord(
            1, "0002__synthetic_history__v01.jpeg", "rendered-image", candidate_id=UUID,
        )]
        state.items.append(neighbor)
        self.store.save(state)
        with self.offline_session(), self.assertRaisesRegex(RuntimeError, "CANDIDATE_CORRELATION_UNPROVEN"):
            self.execute()
        saved = self.store.load()
        self.assertEqual(saved.items[0].candidates, [])
        self.assertEqual(saved.items[1], neighbor)

    def test_completion_save_error_preserves_the_last_atomic_state(self):
        save = RunStateStore.save
        for after_replace in (False, True):
            with self.subTest(after_replace=after_replace):
                fixture = type(self)(methodName="runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                def failed_save(store, state):
                    if state.items[0].status == "completed":
                        if after_replace:
                            save(store, state)
                        raise RuntimeError("synthetic completion persistence failure")
                    return save(store, state)
                with patch.object(RunStateStore, "save", new=failed_save), fixture.offline_session(), patch.object(
                    fixture.session, "_generation_failure_observation", return_value={},
                ) as failure_read:
                    with self.assertRaisesRegex(RuntimeError, "synthetic completion persistence failure"):
                        fixture.execute()
                failure_read.assert_not_called()
                if after_replace:
                    fixture.assert_completed()
                else:
                    item = fixture.store.load().items[0]
                    self.assertEqual(item.status, "running")
                    self.assertEqual(item.candidates, [])

    def test_post_completion_exact_failures_do_not_start_automatic_retry(self):
        # One subcase per fixture keeps every persisted attempt independent.
        for failure in (FlowPolicyRejected, FlowGenericGenerationFailed, FlowUnusualActivity):
            with self.subTest(failure=failure.__name__):
                fixture = type(self)(methodName="runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                def fail_cleanup():
                    raise failure()
                with fixture.offline_session(cleanup=fail_cleanup), self.assertRaises(failure):
                    fixture.execute(allow_automatic_policy_retry=True)
                fixture.assert_completed()

    def test_restart_download_only_and_repeated_resume_never_resubmit(self):
        with authoritative_cancellation_scope() as cancellation:
            with self.offline_session(cleanup=lambda: (_ for _ in ()).throw(cancellation.request())), self.assertRaises(KeyboardInterrupt):
                self.execute()
        before = self.assert_completed()
        adapter = MagicMock(spec=["generate_one", "download_candidate"])
        adapter.generate_one.side_effect = AssertionError("must not submit again")
        def download(*, candidate, destination):
            Image.new("RGB", (16, 9)).save(destination, format="JPEG")
            return BrowserDownloadResult(candidate.candidate_id, str(destination), mechanism="synthetic")
        adapter.download_candidate.side_effect = download
        recovered, _ = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(recovered.items[0].attempt_count, before.items[0].attempt_count)
        self.assertEqual(recovered.items[0].candidates[0].candidate_id, UUID)
        state_bytes = self.store.path.read_bytes()
        again, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(again, recovered)
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        self.assertEqual(outcomes[0].downloads, "skipped_valid")
        self.assertEqual(self.store.path.read_bytes(), state_bytes)
        adapter.generate_one.assert_not_called()
        adapter.download_candidate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
