"""Synthetic, read-only preview and bounded-thread regressions. Never use Flow."""

import hashlib
import io
import os
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from PIL import Image

from nano_batch.batch import load_prompts_file
from nano_batch.downloads import validate_image_file
from nano_batch.errors import WAITING_FOR_FLOW_LIMIT_RESET
from nano_batch.models import CandidateRecord, GenerationSettings
from nano_batch.preview import (
    ERROR_MESSAGES, PreviewController, PreviewError, PreviewMessage, PreviewRequest,
    PreviewResult, PreviewState, PreviewView, evaluate_preview, present_plan, preview_fingerprint,
)
from nano_batch.runner import initialize_run_state, output_basename, plan_sequential_batch
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


def wait_message(controller, kind=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        message = controller.drain()
        if message is not None and (kind is None or message.kind == kind):
            return message
        threading.Event().wait(0.005)
    raise AssertionError("Synthetic preview message did not arrive")


def tree_snapshot(root):
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest(),
        ) if path.is_file() else None
        for path in root.rglob("*")
    }


class GuiPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompts = self.root / "synthetic-input.txt"
        self.prompts.write_text("# synthetic comment\nSynthetic private prompt one.\n\nSynthetic private prompt two.\n", encoding="utf-8")
        self.run = self.root / "run"
        self.batch = load_prompts_file(self.prompts)
        self.store = RunStateStore(self.run / "run_state.json")

    def request(self, existing=False):
        return PreviewRequest(str(self.prompts), str(self.run), existing)

    def saved(self):
        return initialize_run_state(self.batch, self.run)

    def complete_generation(self, state, order=1, *, variants=1, downloaded=()):
        item = state.items[order - 1]
        item.status, item.attempt_count = "completed", 1
        item.completed_at = "synthetic completion"
        item.result_details = f'{{"configured_variants":{variants},"requested_variants":{variants}}}'
        item.candidates = []
        for variant in range(1, variants + 1):
            candidate = CandidateRecord(variant, output_basename(order, item.visual_id, variant, "jpeg"),
                                        "rendered-image", candidate_id=str(UUID(int=100 * order + variant)))
            if variant in downloaded:
                path = self.run / "assets" / candidate.suggested_filename
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (16, 9), (20 * variant, 30, 40)).save(path, format="JPEG")
                for name, value in asdict(validate_image_file(path)).items():
                    setattr(candidate, name, value)
                candidate.download_status, candidate.local_path = "completed", str(path)
                candidate.local_relative_path = path.relative_to(self.run).as_posix()
                candidate.downloaded_at = "synthetic download"
                item.output_files.append(str(path))
            item.candidates.append(candidate)
        if len(downloaded) == variants:
            item.download_status = "completed"
            item.download_completed_at = "synthetic download"
        self.store.save(state)
        return item

    def test_fresh_preview_uses_production_parser_and_does_not_create_run(self):
        before = tree_snapshot(self.root)
        result = evaluate_preview(self.request())
        self.assertEqual(len(result.view.rows), 2)
        self.assertEqual(result.view.count(PreviewState.PENDING), 2)
        self.assertFalse(self.run.exists())
        self.assertEqual(before, tree_snapshot(self.root))
        self.assertEqual(result.view.rows[0].generation, "Generate (planned)")

    def test_complete_existing_run_is_validated_without_writes(self):
        state = self.saved()
        self.complete_generation(state, 1, downloaded=(1,))
        self.complete_generation(state, 2, downloaded=(1,))
        before = tree_snapshot(self.root)
        view = evaluate_preview(self.request(True)).view
        self.assertEqual(view.count(PreviewState.COMPLETED), 2)
        self.assertEqual(before, tree_snapshot(self.root))

    def test_download_only_resume_does_not_mark_generation_pending(self):
        state = self.saved()
        self.complete_generation(state)
        before = tree_snapshot(self.root)
        row = evaluate_preview(self.request(True)).view.rows[0]
        self.assertEqual(row.state, PreviewState.DOWNLOAD_ONLY)
        self.assertEqual(row.generation, "Skip saved generation")
        self.assertEqual(before, tree_snapshot(self.root))

    def test_stale_order_finalization_is_not_silently_repaired_or_counted_complete(self):
        state = self.saved()
        item = self.complete_generation(state, downloaded=(1,))
        item.download_status = "running"
        self.store.save(state)
        before = tree_snapshot(self.root)
        view = evaluate_preview(self.request(True)).view
        self.assertEqual(view.rows[0].state, PreviewState.FINALIZE)
        self.assertEqual(view.count(PreviewState.COMPLETED), 0)
        self.assertEqual(before, tree_snapshot(self.root))

    def test_acknowledged_without_ownership_remains_blocked(self):
        state = self.saved()
        state.items[0].status, state.items[0].attempt_count = "running", 1
        self.store.save(state)
        before = self.store.path.read_bytes()
        row = evaluate_preview(self.request(True)).view.rows[0]
        self.assertEqual(row.state, PreviewState.BLOCKED)
        self.assertIn("no automatic submission", row.generation)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_ambiguous_generation_failure_is_not_automatically_retried(self):
        state = self.saved()
        state.items[0].status = "failed"
        state.items[0].generation_error_code = "FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS"
        self.store.save(state)
        self.assertEqual(evaluate_preview(self.request(True)).view.rows[0].state, PreviewState.BLOCKED)

    def test_conflicting_uuid_ownership_blocks_entire_preview_safely(self):
        state = self.saved()
        first = self.complete_generation(state, 1)
        second = self.complete_generation(state, 2)
        second.candidates[0].candidate_id = first.candidates[0].candidate_id
        self.store.save(state)
        before = tree_snapshot(self.root)
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request(True))
        self.assertEqual(caught.exception.code, "plan_invalid")
        self.assertNotIn(first.candidates[0].candidate_id, str(caught.exception))
        self.assertEqual(before, tree_snapshot(self.root))

    def test_waiting_state_is_separate_from_blocked_or_pending(self):
        state = self.saved()
        state.items[0].status = WAITING_FOR_FLOW_LIMIT_RESET
        state.items[0].generation_error_code = "FLOW_USAGE_LIMIT_REACHED"
        self.store.save(state)
        view = evaluate_preview(self.request(True)).view
        self.assertEqual(view.count(PreviewState.WAITING), 1)
        self.assertEqual(view.count(PreviewState.PENDING), 1)

    def test_existing_automatic_retry_eligibility_does_not_consume_budget(self):
        state = self.saved()
        state.items[0].status = "failed"
        state.items[0].generation_error_code = "FLOW_POLICY_REJECTED"
        self.store.save(state)
        before = self.store.path.read_bytes()
        self.assertEqual(evaluate_preview(self.request(True)).view.rows[0].state, PreviewState.RETRY)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_partial_x2_planner_projection_counts_orders_and_preserves_assets(self):
        self.batch = replace(self.batch, items=tuple(replace(item, variants=2) for item in self.batch.items))
        state = self.saved()
        self.complete_generation(state, variants=2, downloaded=(1,))
        before = tree_snapshot(self.root)
        view = present_plan(plan_sequential_batch(self.batch, self.run, GenerationSettings(variants=2)))
        self.assertEqual(view.rows[0].state, PreviewState.DOWNLOAD_ONLY)
        self.assertIn("v01: keep validated asset", view.rows[0].candidates)
        self.assertIn("v02: download", view.rows[0].candidates)
        self.assertEqual(view.count(PreviewState.COMPLETED), 0)
        self.assertEqual(before, tree_snapshot(self.root))

    def test_text_gui_does_not_guess_external_x2_batch_identity(self):
        state = self.saved()
        state.batch_id = "sha256:synthetic-external-x2"
        self.store.save(state)
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request(True))
        self.assertEqual(caught.exception.code, "plan_invalid")

    def test_pending_receipt_plan_is_not_reconciled_or_cleaned(self):
        fixture = make_pending_download_fixture(self.root / "pending")
        before = tree_snapshot(self.root)
        with patch("nano_batch.preview.load_prompts_file", return_value=fixture.batch):
            result = evaluate_preview(PreviewRequest(str(self.prompts), str(fixture.run_dir), True))
        self.assertIn("reconcile interrupted download", result.view.rows[0].candidates)
        self.assertEqual(before, tree_snapshot(self.root))

    def test_invalid_utf8_is_safely_reported(self):
        self.prompts.write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request())
        self.assertEqual(caught.exception.code, "prompt_encoding")

    def test_empty_or_comments_only_prompt_file_is_rejected(self):
        self.prompts.write_text("\n# synthetic comment\n", encoding="utf-8")
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request())
        self.assertEqual(caught.exception.code, "prompt_content")

    def test_missing_or_directory_prompt_path_is_rejected(self):
        for path in (self.root / "missing.txt", self.root):
            with self.subTest(path=path.name), self.assertRaises(PreviewError) as caught:
                evaluate_preview(replace(self.request(), prompt_file=str(path)))
            self.assertEqual(caught.exception.code, "prompt_path")

    def test_invalid_run_paths_do_not_create_parents(self):
        for path in ("", str(self.root / "missing-parent" / "run"), str(self.root / "bad?name"), "\0"):
            with self.subTest(), self.assertRaises(PreviewError) as caught:
                evaluate_preview(replace(self.request(), run_dir=path))
            self.assertEqual(caught.exception.code, "run_path")
        self.assertFalse((self.root / "missing-parent").exists())

    def test_existing_run_mode_requires_folder_and_state(self):
        self.run.mkdir()
        for path in (self.run, self.prompts, self.root / "absent"):
            with self.subTest(path=path.name), self.assertRaises(PreviewError) as caught:
                evaluate_preview(replace(self.request(True), run_dir=str(path)))
            self.assertEqual(caught.exception.code, "run_missing")

    def test_new_mode_rejects_existing_folder_without_modifying_it(self):
        self.run.mkdir()
        before = tree_snapshot(self.root)
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request())
        self.assertEqual(caught.exception.code, "run_exists")
        self.assertEqual(before, tree_snapshot(self.root))

    def test_malformed_state_is_never_repaired(self):
        self.run.mkdir()
        self.store.path.write_text('{"synthetic":', encoding="utf-8")
        before = tree_snapshot(self.root)
        with self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request(True))
        self.assertEqual(caught.exception.code, "plan_invalid")
        self.assertEqual(before, tree_snapshot(self.root))

    def test_unicode_and_space_containing_paths(self):
        directory = self.root / "synthetic spaces \u0442\u0435\u0441\u0442 \u6d4b\u8bd5"
        directory.mkdir()
        prompts = directory / "synthetic \u732b.txt"
        prompts.write_text("Synthetic \u732b prompt.\n", encoding="utf-8")
        request = PreviewRequest(str(prompts), str(directory / "new \u043f\u043b\u0430\u043d"))
        self.assertEqual(len(evaluate_preview(request).view.rows), 1)
        self.assertFalse(Path(request.run_dir).exists())

    def test_prompt_change_during_planning_invalidates_result(self):
        planner = plan_sequential_batch
        def change(*args, **kwargs):
            result = planner(*args, **kwargs)
            self.prompts.write_text("Synthetic changed input.\n", encoding="utf-8")
            return result
        with patch("nano_batch.preview.plan_sequential_batch", side_effect=change), self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request())
        self.assertEqual(caught.exception.code, "changed")

    def test_state_change_during_planning_invalidates_result(self):
        self.saved()
        planner = plan_sequential_batch
        def change(*args, **kwargs):
            result = planner(*args, **kwargs)
            self.store.path.write_bytes(self.store.path.read_bytes() + b"\n")
            return result
        with patch("nano_batch.preview.plan_sequential_batch", side_effect=change), self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request(True))
        self.assertEqual(caught.exception.code, "changed")

    def test_hash_detects_content_changes_with_preserved_size_and_mtime(self):
        before = preview_fingerprint(self.request())
        stamp = self.prompts.stat()
        self.prompts.write_bytes(self.prompts.read_bytes().replace(b"one", b"six"))
        os.utime(self.prompts, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertNotEqual(before, preview_fingerprint(self.request()))

    def test_safe_results_contain_no_prompt_uuid_path_or_visual_id(self):
        state = self.saved()
        self.complete_generation(state)
        result = evaluate_preview(self.request(True))
        message = PreviewMessage(1, "ready", result.view)
        rendered = repr(asdict(message)) + repr(result)
        for private in ("Synthetic private", "prompt_0001_visual", str(self.root), state.items[0].candidates[0].candidate_id):
            self.assertNotIn(private, rendered)

    def test_unknown_exception_text_is_not_exposed(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("must not stringify private error")
        with patch("nano_batch.preview.plan_sequential_batch", side_effect=PrivateError()), self.assertRaises(PreviewError) as caught:
            evaluate_preview(self.request())
        self.assertEqual(caught.exception.code, "unexpected")

    def test_malicious_planner_labels_are_not_rendered(self):
        plan = plan_sequential_batch(self.batch, self.run, GenerationSettings(variants=1))
        bad = replace(plan.orders[0], generation_action="synthetic private\nhttps://example.invalid/?token=private")
        with self.assertRaises(PreviewError):
            present_plan(replace(plan, orders=(bad,)))

    def test_read_only_audit_blocks_writes_network_browser_and_subprocess(self):
        state = self.saved()
        self.complete_generation(state, downloaded=(1,))
        before = tree_snapshot(self.root)
        script = (
            "import os,sys\nfrom nano_batch.preview import evaluate_preview,PreviewRequest\n"
            "def guard(event,args):\n"
            " if event in {'socket.connect','socket.bind','subprocess.Popen','os.mkdir','os.remove','os.rmdir','os.rename','os.chmod'}: raise AssertionError('forbidden side effect')\n"
            " if event=='open' and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND): raise AssertionError('forbidden write')\n"
            "sys.addaudithook(guard)\n"
            "r=evaluate_preview(PreviewRequest(sys.argv[1],sys.argv[2],True))\n"
            "assert len(r.view.rows)==2\n"
            "assert not any(n.startswith('playwright') or n=='nano_batch.adapters.flow' for n in sys.modules)\n"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", script, str(self.prompts), str(self.run)],
                                   capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(before, tree_snapshot(self.root))

    def test_in_process_write_guards_cover_preview(self):
        self.saved()
        actual_open = io.open
        def readonly_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("Preview attempted a write")
            return actual_open(file, mode, *args, **kwargs)
        with patch("io.open", side_effect=readonly_open), \
             patch.object(RunStateStore, "save", side_effect=AssertionError("save forbidden")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("mkdir forbidden")):
            self.assertEqual(len(evaluate_preview(self.request(True)).view.rows), 2)


class PreviewControllerTests(unittest.TestCase):
    def controller(self, **kwargs):
        controller = PreviewController(**kwargs)
        self.addCleanup(controller._thread.join, 2)
        self.addCleanup(controller.close)
        return controller

    def test_slow_preview_returns_immediately_and_result_arrives_via_queue(self):
        entered, release = threading.Event(), threading.Event()
        def slow(request):
            entered.set()
            release.wait(5)
            return PreviewResult(PreviewView(()), ())
        controller = self.controller(evaluator=slow, fingerprint=lambda _r: ())
        self.addCleanup(release.set)
        controller.request(PreviewRequest("synthetic", "synthetic"))
        self.assertTrue(entered.wait(2))
        self.assertIsNone(controller.drain())
        self.assertTrue(controller._thread.is_alive())
        release.set()
        self.assertEqual(wait_message(controller).kind, "ready")

    def test_stale_response_from_old_selection_is_discarded(self):
        entered, release = threading.Event(), threading.Event()
        def slow(request):
            entered.set()
            release.wait(5)
            return PreviewResult(PreviewView(()), ())
        controller = self.controller(evaluator=slow, fingerprint=lambda _r: ())
        self.addCleanup(release.set)
        controller.request(PreviewRequest("synthetic", "synthetic"))
        self.assertTrue(entered.wait(2))
        new_revision = controller.invalidate()
        release.set()
        controller._publish(PreviewMessage(new_revision - 1, "ready", PreviewView(())))
        self.assertIsNone(controller.drain())

    def test_requests_and_results_are_bounded_under_repeated_clicks(self):
        entered, release = threading.Event(), threading.Event()
        def slow(request):
            entered.set()
            release.wait(5)
            return PreviewResult(PreviewView(()), ())
        controller = self.controller(evaluator=slow, fingerprint=lambda _r: ())
        self.addCleanup(release.set)
        controller.request(PreviewRequest("synthetic", "synthetic"))
        self.assertTrue(entered.wait(2))
        for _ in range(1000):
            revision = controller.request(PreviewRequest("synthetic", "synthetic"))
        self.assertLessEqual(controller._requests.qsize(), 1)
        self.assertLessEqual(controller._results.qsize(), 1)
        release.set()
        self.assertEqual(wait_message(controller).revision, revision)

    def test_monitor_invalidates_changed_inputs_without_replanning(self):
        token = [1]
        controller = self.controller(evaluator=lambda _r: PreviewResult(PreviewView(()), (1,)),
            fingerprint=lambda _r: tuple(token), check_interval=0.02)
        controller.request(PreviewRequest("synthetic", "synthetic"))
        wait_message(controller, "ready")
        token[0] = 2
        message = wait_message(controller, "stale")
        self.assertEqual(message.code, "changed")
        self.assertIsNone(message.view)

    def test_real_prompt_and_state_changes_invalidate_completed_preview(self):
        with pending_download_test_directory() as directory:
            root = Path(directory)
            prompts = root / "synthetic.txt"
            prompts.write_text("Synthetic prompt.\n", encoding="utf-8")
            run = root / "run"
            initialize_run_state(load_prompts_file(prompts), run)
            controller = self.controller(check_interval=0.02)
            request = PreviewRequest(str(prompts), str(run), True)
            for path in (prompts, run / "run_state.json"):
                controller.request(request)
                wait_message(controller, "ready")
                path.write_bytes(path.read_bytes() + b"\n")
                self.assertEqual(wait_message(controller, "stale").code, "changed")
            controller.close()
            controller._thread.join(2)

    def test_new_run_appearing_invalidates_prospective_preview(self):
        with pending_download_test_directory() as directory:
            root = Path(directory)
            prompts = root / "synthetic.txt"
            prompts.write_text("Synthetic prompt.\n", encoding="utf-8")
            run = root / "new"
            controller = self.controller(check_interval=0.02)
            controller.request(PreviewRequest(str(prompts), str(run)))
            wait_message(controller, "ready")
            run.mkdir()
            self.assertEqual(wait_message(controller, "stale").code, "changed")
            controller.close()
            controller._thread.join(2)

    def test_worker_exceptions_are_fixed_code_messages_only(self):
        def broken(_request):
            raise RuntimeError("synthetic private prompt or authentication detail")
        controller = self.controller(evaluator=broken)
        controller.request(PreviewRequest("synthetic private path", "synthetic"))
        message = wait_message(controller)
        self.assertEqual(message.code, "unexpected")
        self.assertNotIn("private", repr(message))

    def test_close_is_nonblocking_and_prevents_late_publication(self):
        entered, release = threading.Event(), threading.Event()
        def slow(_request):
            entered.set()
            release.wait(5)
            return PreviewResult(PreviewView(()), ())
        controller = self.controller(evaluator=slow)
        self.addCleanup(release.set)
        controller.request(PreviewRequest("synthetic", "synthetic"))
        self.assertTrue(entered.wait(2))
        controller.close()
        self.assertTrue(controller._thread.is_alive())
        release.set()
        controller._thread.join(2)
        self.assertIsNone(controller.drain())


if __name__ == "__main__":
    unittest.main()
