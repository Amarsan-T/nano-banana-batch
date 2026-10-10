import argparse
import json
import signal
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import FlowPolicyRejected, FlowSession
from nano_batch.batch import load_prompts_file
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.errors import (
    FLOW_GENERIC_GENERATION_FAILED, FLOW_POLICY_REJECTED, FLOW_UNUSUAL_ACTIVITY,
    FLOW_USAGE_LIMIT_REACHED,
)
from nano_batch.launcher import (
    choose_policy_recovery_action, find_matching_runs, run_launcher,
)
from nano_batch.models import FlowProjectReference, GenerationSettings
from nano_batch.runner import (
    edit_policy_rejected_prompt, execute_download_one, execute_one,
    execute_sequential_batch, initialize_run_state, plan_sequential_batch,
)
from nano_batch.state import RunStateStore, StatePersistenceError
from tests.test_app import OfflinePromptAdapter


PROJECT = FlowProjectReference(
    project_id="00000000-0000-4000-8000-000000000001",
    project_url="https://flow.google.com/project/00000000-0000-4000-8000-000000000001",
)


class OfflineSameSessionPolicyAdapter(FlowSession):
    """Exercise the production recovery loop, replacing every browser operation."""

    def __init__(self, run_dir, steps):
        super().__init__(PROJECT.project_url, Path("unused-profile"))
        self.run_dir, self.steps = run_dir, list(steps)
        self.events, self.generations, self.prepared = [], [], []
        self.enters = self.exits = 0
        self.page, self._browser, self._page_cdp_session = object(), object(), object()

    def __enter__(self):
        self.enters += 1
        return self

    def __exit__(self, *_args):
        self.exits += 1

    def create_flow_project(self):
        raise AssertionError("Policy recovery must not create or rotate projects")

    def prepare_flow_project(self, *, project, settings):
        self.prepared.append(project)

    def _generate_one_in_open_session(self, *, item, settings, **_kwargs):
        state = RunStateStore(self.run_dir / "run_state.json").load()
        current = state.items[item.order - 1]
        self.generations.append((item.order, item.prompt, current.attempt_count,
                                 current.automatic_policy_retry_count, self.enters,
                                 id(self.page), id(self._browser), id(self._page_cdp_session)))
        step = self.steps.pop(0) if item.order == 2 and self.steps else "success"
        if step == "policy":
            raise FlowPolicyRejected()
        if step == "interrupt":
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        return OfflinePromptAdapter.generate_one(
            self, item=item, settings=settings, forbidden_candidate_ids=frozenset(),
        )

    download_candidate = OfflinePromptAdapter.download_candidate


class PolicyPromptRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompts = self.root / "prompts.txt"
        self.prompts.write_text("earlier private prompt\noriginal policy prompt\nlater private prompt\n", encoding="utf-8")
        self.batch = load_prompts_file(self.prompts)
        self.run_dir = self.root / "runs" / "policy"
        self.store = RunStateStore(self.run_dir / "run_state.json")
        self.settings = GenerationSettings(variants=1)
        state = initialize_run_state(self.batch, self.run_dir)
        state.flow_project, state.flow_project_required = PROJECT, True
        self.store.save(state)
        accepted = OfflinePromptAdapter()
        execute_one(self.batch, self.run_dir, 1, accepted, self.settings)
        execute_download_one(self.batch, self.run_dir, 1, accepted)
        state = self.store.load()
        target = state.items[1]
        target.status, target.generation_error_code = "failed", FLOW_POLICY_REJECTED
        target.attempt_count, target.automatic_policy_retry_count = 2, 1
        target.error = "exact policy rejection"
        self.store.save(state)
        self.original_prompts = self.prompts.read_bytes()

    @staticmethod
    def choices(*lines):
        iterator = iter(lines)
        return lambda _prompt: next(iterator)

    def fresh_target(self):
        state = self.store.load()
        target = state.items[1]
        target.status, target.generation_error_code, target.error = "pending", None, None
        target.attempt_count = target.automatic_policy_retry_count = 0
        self.store.save(state)

    def menu(self, *lines, output=None):
        return choose_policy_recovery_action(
            self.batch, self.run_dir, 2, input_fn=self.choices(*lines),
            output=output or (lambda _line: None),
        )

    def assert_neighbors_unchanged(self, before):
        after = self.store.load()
        for index in (0, 2):
            self.assertEqual(asdict(before.items[index]), asdict(after.items[index]))
        self.assertEqual(after.flow_project, before.flow_project)
        self.assertEqual(after.batch_id, self.batch.batch_id)
        self.assertEqual(self.prompts.read_bytes(), self.original_prompts)

    def cli(self, adapter, *choices):
        args = argparse.Namespace(prompts_file=str(self.prompts), run_dir=str(self.run_dir),
            config=str(self.root / "missing-config.json"), timeout=None, retry=False, preview=False)
        with patch.object(app, "_flow_session_from_args", return_value=adapter), \
             patch.object(app, "windows_execution_state_guard", return_value=nullcontext()), \
             patch("builtins.input", side_effect=list(choices)), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return app.cmd_run_prompts_file(args)

    def test_first_rejection_retries_inside_same_open_session_and_continues(self):
        self.fresh_target()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "success"])
        events = []
        final, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
            policy_retry_event=lambda order, event: events.append((order, event)))
        calls = adapter.generations[:2]
        self.assertEqual([(c[0], c[1], c[2], c[3]) for c in calls], [
            (2, "original policy prompt", 1, 0), (2, "original policy prompt", 2, 1)])
        self.assertEqual(calls[0][4:], calls[1][4:])
        self.assertEqual(events, [(2, "automatic_retry_started"), (2, "automatic_retry_succeeded")])
        self.assertEqual(final.counts()["completed"], 3)
        self.assertEqual(outcomes[1].generation, "retried_policy_automatic")
        self.assertEqual(adapter.enters, adapter.exits)
        self.assertEqual(final.flow_project, PROJECT)

    def test_second_rejection_stops_before_third_attempt_or_next_order(self):
        self.fresh_target()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "policy", "success"])
        with self.assertRaises(FlowPolicyRejected):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(len(adapter.generations), 2)
        self.assertEqual(adapter.steps, ["success"])
        target = self.store.load().items[1]
        self.assertEqual((target.attempt_count, target.automatic_policy_retry_count), (2, 1))
        self.assertEqual(self.store.load().items[2].attempt_count, 0)
        self.assertFalse(any(event[0] == "download" for event in adapter.events))
        self.assertEqual(adapter.enters, 1)

    def test_manual_retry_after_exhaustion_uses_same_prompt_and_one_new_attempt(self):
        self.assertEqual(self.menu("1"), "retry")
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["success"])
        final, _ = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
                                            retry_failed_generation=True)
        self.assertEqual(adapter.generations[0][:4], (2, "original policy prompt", 3, 1))
        self.assertEqual(final.items[1].automatic_policy_retry_count, 1)

    def test_edit_persists_exact_unicode_multiline_only_target(self):
        before = self.store.load()
        canonical = Path(before.items[0].candidates[0].local_path)
        asset_bytes = canonical.read_bytes()
        self.assertEqual(self.menu("2", "  новая 😀  ", "", "second\tline  ", "END"), "edit_retry")
        target = self.store.load().items[1]
        self.assertEqual(target.prompt, "  новая 😀  \n\nsecond\tline  ")
        self.assertEqual(target.source_prompt, "original policy prompt")
        self.assertEqual(target.attempt_count, 2)
        self.assertEqual(target.automatic_policy_retry_count, 1)
        self.assertEqual(target.visual_id, before.items[1].visual_id)
        self.assert_neighbors_unchanged(before)
        self.assertEqual(canonical.read_bytes(), asset_bytes)

    def test_retry_after_edit_uses_stored_prompt_and_skips_prior_outputs(self):
        self.menu("2", "replacement", "END")
        before = self.store.load()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["success"])
        final, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
                                                   retry_failed_generation=True)
        self.assertEqual(adapter.generations[0][:4], (2, "replacement", 3, 1))
        self.assertEqual(outcomes[0].generation, "skipped_completed")
        self.assertEqual(outcomes[0].downloads, "skipped_valid")
        self.assertEqual(asdict(final.items[0]), asdict(before.items[0]))

    def test_resume_preserves_edited_prompt_and_consumed_budget(self):
        self.menu("2", "replacement", "END")
        plan = plan_sequential_batch(self.batch, self.run_dir, self.settings)
        self.assertEqual(plan.orders[1].generation_action, "blocked_retry_required")
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["success"])
        with self.assertRaisesRegex(ValueError, "retry required"):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(adapter.generations, [])
        incomplete, _ = find_matching_runs(self.root / "runs", load_prompts_file(self.prompts))
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0].state.items[1].prompt, "replacement")
        self.assertEqual(incomplete[0].state.items[1].automatic_policy_retry_count, 1)

    def test_empty_end_and_whitespace_only_edits_cancel_without_writes(self):
        for lines in (("2", "END", "3"), ("2", "  ", "", "END", "3")):
            before = self.store.path.read_bytes()
            self.assertEqual(self.menu(*lines), "main")
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_cancel_after_partial_edit_preserves_old_prompt_exactly(self):
        before = self.store.path.read_bytes()
        self.assertEqual(self.menu("2", "discarded", "CANCEL", "4"), "exit")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_end_and_cancel_are_exact_line_sentinels_not_substrings(self):
        self.menu("2", "END in a sentence", " CANCEL ", "END")
        self.assertEqual(self.store.load().items[1].prompt, "END in a sentence\n CANCEL ")

    def test_main_and_exit_do_not_modify_state_or_start_generation(self):
        before = self.store.path.read_bytes()
        self.assertEqual(self.menu("3"), "main")
        self.assertEqual(self.menu("4"), "exit")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_edit_again_preserves_original_binding_and_retry_budget(self):
        self.menu("2", "replacement one", "END")
        self.menu("2", "replacement two", "END")
        target = self.store.load().items[1]
        self.assertEqual(target.source_prompt, "original policy prompt")
        self.assertEqual(target.prompt, "replacement two")
        self.assertEqual(target.automatic_policy_retry_count, 1)

    def test_rejected_edited_prompt_remains_and_returns_to_menu_without_auto_loop(self):
        self.menu("2", "replacement", "END")
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "success"])
        with self.assertRaises(FlowPolicyRejected):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings,
                                     retry_failed_generation=True)
        self.assertEqual(len(adapter.generations), 1)
        self.assertEqual(adapter.steps, ["success"])
        self.assertEqual(self.store.load().items[1].prompt, "replacement")
        self.assertEqual(self.menu("3"), "main")

    def test_other_failure_types_cannot_enter_editor(self):
        for code in (FLOW_UNUSUAL_ACTIVITY, FLOW_GENERIC_GENERATION_FAILED,
                     FLOW_USAGE_LIMIT_REACHED, None):
            state = self.store.load()
            state.items[1].generation_error_code = code
            self.store.save(state)
            before = self.store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "exhausted policy"):
                edit_policy_rejected_prompt(self.batch, self.run_dir, 2, "replacement")
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_target_candidate_download_state_blocks_edit_without_discarding_it(self):
        state = self.store.load()
        state.items[1].candidates = [replace(state.items[0].candidates[0])]
        state.items[1].download_status = "failed"
        self.store.save(state)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            edit_policy_rejected_prompt(self.batch, self.run_dir, 2, "replacement")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_untracked_local_assets_fail_closed(self):
        artifact = self.run_dir / "assets" / "0002__unexpected.jpeg"
        artifact.write_bytes(b"preserve")
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "local asset"):
            edit_policy_rejected_prompt(self.batch, self.run_dir, 2, "replacement")
        self.assertEqual(artifact.read_bytes(), b"preserve")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_source_prompt_mismatch_is_not_accepted_under_same_hash(self):
        changed = replace(self.batch, items=(self.batch.items[0],
            replace(self.batch.items[1], prompt="different source"), self.batch.items[2]))
        with self.assertRaisesRegex(ValueError, "identity"):
            edit_policy_rejected_prompt(changed, self.run_dir, 2, "replacement")

    def test_source_binding_cannot_be_replaced_or_removed(self):
        self.menu("2", "replacement", "END")
        for invalid in (None, "different original"):
            state = self.store.load()
            state.items[1].source_prompt = invalid
            with self.assertRaisesRegex(ValueError, "binding"):
                self.store.save(state)

    def test_edited_prompt_with_reset_budget_fails_closed_on_load(self):
        self.menu("2", "replacement", "END")
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        raw["items"][1]["automatic_policy_retry_count"] = 0
        self.store.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "consumed automatic"):
            self.store.load()

    def test_legacy_states_without_new_fields_load_safely(self):
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        for item in raw["items"]:
            item.pop("source_prompt")
            item.pop("automatic_policy_retry_count")
        self.store.path.write_text(json.dumps(raw), encoding="utf-8")
        state = self.store.load()
        self.assertTrue(all(item.source_prompt is None for item in state.items))
        self.assertTrue(all(item.automatic_policy_retry_count == 0 for item in state.items))

    def test_atomic_edit_failure_leaves_old_state_and_neighbors_untouched(self):
        before = self.store.path.read_bytes()
        with patch.object(RunStateStore, "_replace_atomically", side_effect=StatePersistenceError("locked")), \
             self.assertRaises(StatePersistenceError):
            edit_policy_rejected_prompt(self.batch, self.run_dir, 2, "replacement")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_one_sigint_during_editor_is_authoritative_and_does_not_commit(self):
        before = self.store.path.read_bytes()
        calls = 0
        def interrupted(_prompt):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "2"
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            choose_policy_recovery_action(self.batch, self.run_dir, 2,
                input_fn=interrupted, output=lambda _line: None)
        self.assertEqual(calls, 2)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_one_sigint_after_auto_authorization_does_not_trigger_third_start(self):
        self.fresh_target()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "interrupt", "success"])
        with authoritative_cancellation_scope(), self.assertRaises(KeyboardInterrupt):
            execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(len(adapter.generations), 2)
        self.assertEqual(adapter.steps, ["success"])
        self.assertEqual(self.store.load().items[1].automatic_policy_retry_count, 1)
        self.assertEqual(self.store.load().items[2].attempt_count, 0)

    def test_menu_diagnostics_are_scalar_and_do_not_include_other_prompts(self):
        output = []
        self.menu("2", "CANCEL", "2", "replacement", "END", output=output.append)
        diagnostics = "\n".join(line for line in output if line.startswith("POLICY RECOVERY:"))
        for event in ("policy_recovery_menu_reached", "prompt_edit_started", "prompt_edit_cancelled", "edited_prompt_applied"):
            self.assertIn(event, diagnostics)
        for prompt in ("original policy prompt", "earlier private prompt", "later private prompt", "replacement"):
            self.assertNotIn(prompt, diagnostics)
        self.assertNotIn("earlier private prompt", "\n".join(output))
        self.assertNotIn("later private prompt", "\n".join(output))

    def test_direct_cli_second_rejection_offers_manual_retry(self):
        self.fresh_target()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "policy", "success"])
        self.assertEqual(self.cli(adapter, "1"), 0)
        self.assertEqual([c[:4] for c in adapter.generations[:3]], [
            (2, "original policy prompt", 1, 0), (2, "original policy prompt", 2, 1),
            (2, "original policy prompt", 3, 1)])

    def test_direct_cli_resume_edits_then_retries_preserved_order(self):
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["success"])
        self.assertEqual(self.cli(adapter, "2", "replacement", "END"), 0)
        self.assertEqual(adapter.generations[0][:4], (2, "replacement", 3, 1))
        self.assertTrue(all(project == PROJECT for project in adapter.prepared))

    def test_direct_cli_return_from_persisted_menu_never_constructs_adapter(self):
        args = argparse.Namespace(prompts_file=str(self.prompts), run_dir=str(self.run_dir),
            config=str(self.root / "none"), timeout=None, retry=False, preview=False)
        before = self.store.path.read_bytes()
        with patch.object(app, "_flow_session_from_args") as construct, \
             patch("builtins.input", return_value="3"), redirect_stdout(StringIO()):
            self.assertEqual(app.cmd_run_prompts_file(args), 2)
        construct.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_launcher_resume_policy_menu_edits_and_delegates_existing_command(self):
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["success"])
        choices = self.choices("3", "2", "replacement", "END", "6")
        def launcher(root, execute, **kwargs):
            return run_launcher(root, execute, input_fn=choices, output=lambda _line: None, **kwargs)
        args = app.build_parser().parse_args(["launcher"])
        with patch.object(app, "__file__", str(self.root / "app.py")), \
             patch.object(app, "run_launcher", side_effect=launcher), \
             patch.object(app, "_flow_session_from_args", return_value=adapter), \
             patch.object(app, "windows_execution_state_guard", return_value=nullcontext()), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(app.cmd_launcher(args), 0)
        self.assertEqual(adapter.generations[0][:4], (2, "replacement", 3, 1))

    def test_launcher_new_policy_failure_reaches_policy_menu_not_generic_menu(self):
        self.fresh_target()
        adapter = OfflineSameSessionPolicyAdapter(self.run_dir, ["policy", "policy", "success"])
        output = []
        choices = self.choices("3", "3", "6")
        def launcher(root, execute, **kwargs):
            return run_launcher(root, execute, input_fn=choices, output=output.append, **kwargs)
        with patch.object(app, "__file__", str(self.root / "app.py")), \
             patch.object(app, "run_launcher", side_effect=launcher), \
             patch.object(app, "_flow_session_from_args", return_value=adapter), \
             patch.object(app, "windows_execution_state_guard", return_value=nullcontext()), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(app.cmd_launcher(app.build_parser().parse_args(["launcher"])), 0)
        self.assertIn("Policy rejection persists for Order 2.", output)
        self.assertIn("2. Edit this Order's prompt and retry", output)
        self.assertEqual(len(adapter.generations), 2)
        self.assertEqual(self.store.load().items[2].attempt_count, 0)

    def test_launcher_policy_exit_never_delegates(self):
        execute = MagicMock()
        self.assertEqual(run_launcher(self.root, execute,
            input_fn=self.choices("3", "4"), output=lambda _line: None), 0)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
