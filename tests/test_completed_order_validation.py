"""Completed-state contradictions use only disposable synthetic runs/assets."""

import copy
import hashlib
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import app
from nano_batch.batch import load_prompts_file
from nano_batch.gui_worker import StopBridge, run_job
from nano_batch.models import Batch, BatchItem, GenerationSettings
from nano_batch.preview import PreviewError, PreviewRequest, evaluate_preview
from nano_batch.runner import (
    execute_download_one, execute_sequential_batch, plan_download_one, plan_sequential_batch,
)
from nano_batch.state import RunStateStore
from nano_batch.worker_protocol import JobMode, JobRequest
from test_flow_projects import ProjectFlowAdapter
from pending_download_fixtures import pending_download_test_directory


class CompletedOrderValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "synthetic-run"
        self.batch = Batch("sha256:completed-order-consistency", "test", (
            BatchItem(1, "synthetic_visual_1", "Synthetic still life.", variants=2),
        ))
        self.settings = GenerationSettings(variants=2)
        execute_sequential_batch(self.batch, self.run, ProjectFlowAdapter(self.run, candidate_count=2), self.settings)
        self.store = RunStateStore(self.run / "run_state.json")
        self.state = self.store.load()
        self.state.items[0].result_details = json.dumps({"configured_variants": 2, "requested_variants": 2})
        self.store.save(self.state)

    def snapshot(self):
        return {p.relative_to(self.run).as_posix():
                (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                if p.is_file() else None for p in self.run.rglob("*")}

    def reject(self, mutation):
        mutation(self.state.items[0])
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock()
        for operation in (
            lambda: plan_sequential_batch(self.batch, self.run, self.settings),
            lambda: plan_download_one(self.batch, self.run, 1),
            lambda: execute_sequential_batch(self.batch, self.run, adapter, self.settings),
            lambda: execute_download_one(self.batch, self.run, 1, adapter),
            lambda: execute_sequential_batch(self.batch, self.run, adapter, self.settings, retry_failed_generation=True),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(adapter.mock_calls, [])

    def test_x2_with_only_one_saved_candidate_is_rejected(self):
        def missing(item):
            item.candidates.pop()
            item.output_files.pop()
        self.reject(missing)

    def test_missing_generation_completion_timestamp_is_rejected(self):
        self.reject(lambda item: setattr(item, "completed_at", None))

    def test_invalid_download_completion_timestamp_is_rejected(self):
        self.reject(lambda item: setattr(item, "download_completed_at", True))

    def test_completed_order_with_download_error_is_rejected(self):
        self.reject(lambda item: setattr(item, "download_error", "synthetic stale error"))

    def test_completed_candidate_with_download_error_is_rejected(self):
        self.reject(lambda item: setattr(item.candidates[0], "download_error", "synthetic stale error"))

    def test_completed_generation_with_error_is_rejected(self):
        self.reject(lambda item: setattr(item, "error", "synthetic stale generation error"))

    def test_completed_generation_with_error_code_is_rejected(self):
        self.reject(lambda item: setattr(item, "generation_error_code", "FLOW_POLICY_REJECTED"))

    def test_completed_order_without_output_references_is_rejected(self):
        self.reject(lambda item: setattr(item, "output_files", []))

    def test_valid_completed_x2_is_read_only_and_adapter_free(self):
        before = self.snapshot()
        adapter = MagicMock()
        plan = plan_sequential_batch(self.batch, self.run, self.settings)
        self.assertEqual(plan.orders[0].generation_action, "skip_completed")
        self.assertEqual(plan.orders[0].download_action, "skip_valid")
        state, outcomes = execute_sequential_batch(self.batch, self.run, adapter, self.settings)
        self.assertEqual(state, self.state)
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        self.assertEqual(outcomes[0].downloads, "skipped_valid")
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(before, self.snapshot())

    def test_blank_or_non_string_generation_completion_proof_is_rejected(self):
        original = copy.deepcopy(self.state)
        for value in ("", " ", False, 42):
            with self.subTest(value=value):
                self.state = copy.deepcopy(original)
                self.reject(lambda item: setattr(item, "completed_at", value))

    def test_output_reference_order_duplicates_and_other_paths_are_rejected(self):
        original = copy.deepcopy(self.state)
        paths = self.state.items[0].output_files
        for value in (paths[::-1], [paths[0]] * 2, ["assets/wrong.jpeg", paths[1]], [None, paths[1]]):
            with self.subTest(outputs=value):
                self.state = copy.deepcopy(original)
                self.reject(lambda item: setattr(item, "output_files", value))

    def test_completed_order_with_incomplete_candidate_is_rejected(self):
        Path(self.state.items[0].candidates[1].local_path).unlink()
        self.reject(lambda item: setattr(item.candidates[1], "download_status", "failed"))

    def test_relative_path_conflict_on_completed_candidate_is_rejected(self):
        self.reject(lambda item: setattr(item.candidates[0], "local_relative_path", "../other.jpeg"))

    def test_batch_variant_count_overrides_dishonest_saved_count(self):
        def missing(item):
            item.candidates.pop()
            item.output_files.pop()
            item.result_details = '{"configured_variants":1,"requested_variants":1}'
        self.reject(missing)

    def test_runtime_count_is_used_when_legacy_batch_omits_variants(self):
        self.batch = replace(self.batch, items=(replace(self.batch.items[0], variants=None),))
        item = self.state.items[0]
        item.candidates.pop()
        item.output_files.pop()
        item.result_details = '{"configured_variants":1,"requested_variants":1}'
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock()
        with self.assertRaises(ValueError):
            execute_sequential_batch(self.batch, self.run, adapter, self.settings)
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_saved_counts_cannot_conflict_with_authoritative_batch(self):
        for value in (True, 1, 3, "2"):
            with self.subTest(value=value):
                self.reject(lambda item: setattr(item, "result_details", json.dumps({"configured_variants": value})))

    def test_valid_x1_and_fresh_planning(self):
        batch = replace(self.batch, items=(replace(self.batch.items[0], variants=1),))
        run = self.root / "fresh-x1"
        settings = GenerationSettings(variants=1)
        plan = plan_sequential_batch(batch, run, settings)
        self.assertEqual(plan.orders[0].generation_action, "generate")
        self.assertFalse(run.exists())
        execute_sequential_batch(batch, run, ProjectFlowAdapter(run), settings)
        plan = plan_sequential_batch(batch, run, settings)
        self.assertEqual(plan.orders[0].download_action, "skip_valid")
        adapter = MagicMock()
        execute_sequential_batch(batch, run, adapter, settings)
        self.assertEqual(adapter.mock_calls, [])

    def test_partial_x2_download_resume_preserves_first_asset_and_generation(self):
        item = self.state.items[0]
        candidate = item.candidates[1]
        Path(candidate.local_path).unlink()
        item.candidates[1] = replace(
            candidate, download_status="failed", download_error="synthetic interrupted download",
            local_path=None, local_relative_path=None, sha256=None, byte_size=None,
            image_format=None, mime_type=None, width=None, height=None, downloaded_at=None,
        )
        item.output_files.pop()
        item.download_status, item.download_error = "failed", "synthetic interrupted download"
        item.download_completed_at = None
        self.store.save(self.state)
        first = self.snapshot()[Path(item.output_files[0]).relative_to(self.run).as_posix()]
        plan = plan_sequential_batch(self.batch, self.run, self.settings)
        self.assertEqual(plan.orders[0].candidate_actions, ((1, "skip_valid"), (2, "download")))
        adapter = ProjectFlowAdapter(self.run)
        state, outcomes = execute_sequential_batch(self.batch, self.run, adapter, self.settings)
        self.assertEqual(adapter.events, [("download", 2)])
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        self.assertEqual(state.items[0].attempt_count, item.attempt_count)
        self.assertEqual(state.items[0].download_attempt_count, item.download_attempt_count + 1)
        self.assertEqual(first, self.snapshot()[Path(item.output_files[0]).relative_to(self.run).as_posix()])

    def test_missing_generation_proof_blocks_download_only_resume(self):
        item = self.state.items[0]
        item.download_status = "running"
        item.download_completed_at = None
        self.reject(lambda target: setattr(target, "completed_at", None))

    def test_valid_legacy_url_and_opaque_identities_keep_skip_compatibility(self):
        item = self.state.items[0]
        self.state.version = 1
        item.result_details = None
        for index, candidate in enumerate(item.candidates):
            candidate.candidate_id = None
            candidate.asset_url = f"https://flow-content.google/image/legacy-opaque-{index}"
            candidate.local_relative_path = candidate.downloaded_at = None
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock()
        execute_sequential_batch(self.batch, self.run, adapter, self.settings)
        execute_download_one(self.batch, self.run, 1, adapter)
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_runtime_proof_supports_legacy_missing_optional_diagnostics(self):
        self.batch = replace(self.batch, items=(replace(self.batch.items[0], variants=None),))
        self.state.items[0].result_details = "legacy free-form diagnostic"
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock()
        execute_sequential_batch(self.batch, self.run, adapter, self.settings)
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.snapshot(), before)
        # Standalone download-one cannot infer x1/x2 from surviving candidates.
        with self.assertRaises(ValueError):
            plan_download_one(self.batch, self.run, 1)

    def test_unknown_standalone_count_fails_closed_but_saved_proof_works(self):
        self.batch = replace(self.batch, items=(replace(self.batch.items[0], variants=None),))
        self.assertEqual(len(plan_download_one(self.batch, self.run, 1).candidates), 2)
        original = copy.deepcopy(self.state)
        for details in (None, "not JSON", "{}", "[]", '{"requested_variants":2}'):
            with self.subTest(details=details):
                self.state = copy.deepcopy(original)
                self.state.items[0].result_details = details
                self.store.save(self.state)
                before = self.snapshot()
                adapter = MagicMock()
                with self.assertRaises(ValueError):
                    plan_download_one(self.batch, self.run, 1)
                with self.assertRaises(ValueError):
                    execute_download_one(self.batch, self.run, 1, adapter)
                self.assertEqual(adapter.mock_calls, [])
                self.assertEqual(before, self.snapshot())

    def test_rejected_completed_state_cannot_bypass_validation_with_overwrite(self):
        self.state.items[0].error = "synthetic stale error"
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock()
        with self.assertRaises(ValueError):
            execute_download_one(self.batch, self.run, 1, adapter, overwrite=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(adapter.mock_calls, [])


class CompletedOrderFrontEndTests(unittest.TestCase):
    def test_gui_cli_and_worker_reject_the_same_saved_contradiction(self):
        with pending_download_test_directory() as temporary:
            root = Path(temporary)
            prompts, config, run = root / "synthetic.txt", root / "synthetic-config.json", root / "run"
            prompts.write_text("Synthetic still life.\n", encoding="utf-8")
            config.write_text("{}", encoding="utf-8")
            batch = load_prompts_file(prompts)
            execute_sequential_batch(batch, run, ProjectFlowAdapter(run), GenerationSettings(variants=1))
            store = RunStateStore(run / "run_state.json")
            state = store.load()
            state.items[0].error = "synthetic private error must not leak"
            store.save(state)
            before = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob("*") if p.is_file()}
            with self.assertRaises(PreviewError) as raised:
                evaluate_preview(PreviewRequest(str(prompts), str(run), True))
            self.assertEqual(raised.exception.code, "plan_invalid")
            output = io.StringIO()
            argv = ["app.py", "run-prompts-file", str(prompts), "--run-dir", str(run), "--config", str(config), "--preview"]
            with redirect_stdout(output), redirect_stderr(output), patch.object(app, "_flow_session_from_args") as browser, patch("sys.argv", argv):
                result = app.main()
            self.assertNotEqual(result, 0)
            browser.assert_not_called()
            self.assertNotIn(state.items[0].error, output.getvalue())
            factory = MagicMock(side_effect=AssertionError("adapter must not be instantiated"))
            terminal = run_job(JobRequest(JobMode.EXECUTE, str(prompts), str(run), str(config)),
                               lambda _event: None, StopBridge(), adapter_factory=factory).as_dict()
            self.assertEqual(terminal["exit_code"], 2)
            self.assertEqual(terminal["outcome"], "failed")
            factory.assert_not_called()
            after = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob("*") if p.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
