import inspect
import json
import logging
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowGenerationPreflightError,
    FlowInspection,
    FlowSession,
    _normalize_composer_generation_resolution,
)
from nano_batch.models import (
    BatchItem,
    DiscoveredCandidate,
    FlowProjectReference,
    GenerationSettings,
)


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"


def composer_resolution(**overrides):
    values = {
        "prompt_editor_count": 1,
        "global_generation_control_count": 1,
        "global_visible_generation_control_count": 1,
        "active_composer_count": 1,
        "active_composer_generation_control_count": 1,
        "structurally_owned_generation_control_count": 1,
        "actionable_owned_generation_control_count": 1,
        "ignored_generation_control_count": 0,
        "composer_depth": 3,
        "composer_token": 10,
        "composer_semantic": True,
        "scan_truncated": False,
        "structural_generation_control_ready": True,
        "generation_control_actionable": True,
        "ready": True,
        "failure_reason": "ready",
        "owned_control_label": "Start generation",
        "prompt_populated": False,
        "candidates": [],
    }
    values.update(overrides)
    if "structurally_owned_generation_control_count" not in overrides:
        values["structurally_owned_generation_control_count"] = values[
            "active_composer_generation_control_count"
        ]
    if "structural_generation_control_ready" not in overrides:
        values["structural_generation_control_ready"] = values["ready"]
    if "generation_control_actionable" not in overrides:
        values["generation_control_actionable"] = bool(
            values["structural_generation_control_ready"]
            and values["actionable_owned_generation_control_count"] == 1
        )
    return _normalize_composer_generation_resolution(values)


def inspection_with_resolution(resolution, *, model="Nano Banana 2"):
    prompt = {
        "tag": "div",
        "role": "textbox",
        "label": "Prompt",
        "placeholder": "What do you want to create?",
        "name": None,
        "contenteditable": "true",
    }
    return FlowInspection(
        current_url=PROJECT_URL,
        title="Flow project",
        document_ready_state="complete",
        visible_body_text_length=400,
        authentication_state="editor_visible",
        visible_buttons=("Start generation",),
        editable_controls=(prompt,),
        prompt_control_candidates=(prompt,),
        comboboxes=(),
        tabs=(),
        menus=(),
        selected_controls=(),
        generation_controls=(model, "Start generation"),
        generation_action_controls=("Start generation",),
        model_labels=(f"{model} crop_16_9 x1",),
        model_related_text=(f"{model} crop_16_9 x1",),
        aspect_ratio_controls=(f"{model} crop_16_9 x1",),
        download_controls=(),
        rendered_assets=(),
        media=(),
        iframes=(),
        loading_or_error_states=(),
        generation_control_resolution=resolution,
    )


def generation_snapshot():
    box = {"x": 100.0, "y": 80.0, "width": 32.0, "height": 32.0}
    return {
        "connected": True,
        "visible": True,
        "disabled": False,
        "aria_disabled": None,
        "pointer_events": "auto",
        "center_top_belongs_to_control": True,
        "bounding_box_samples": [dict(box), dict(box)],
    }


class ComposerOwnedGenerationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.session = FlowSession(PROJECT_URL, Path("profile"))
        self.settings = GenerationSettings(variants=1)

    def test_one_prompt_and_one_owned_generation_control_is_unchanged_success(self):
        result = self.session.detect_image_mode(
            self.settings,
            inspection=inspection_with_resolution(composer_resolution()),
        )
        self.assertEqual(result.mode, "image")
        self.assertEqual(result.evidence["generation_control"], "Start generation")

    def test_empty_editor_with_one_disabled_owned_start_is_preinput_ready(self):
        resolution = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            candidates=[
                {
                    "candidate_index": 0,
                    "tag": "button",
                    "role": "button",
                    "relationship": "descendant",
                    "connected": True,
                    "visible": True,
                    "enabled": False,
                    "positive_geometry": True,
                    "pointer_events": "auto",
                    "inactive_reasons": ["disabled_attribute"],
                    "bounding_box": {"x": 10, "y": 10, "width": 30, "height": 30},
                }
            ],
        )

        result = self.session.detect_image_mode(
            self.settings,
            inspection=inspection_with_resolution(resolution),
        )

        self.assertEqual(result.mode, "image")
        self.assertTrue(resolution["structural_generation_control_ready"])
        self.assertFalse(resolution["generation_control_actionable"])
        self.assertFalse(resolution["prompt_populated"])

    def test_pointer_events_none_on_disabled_owned_start_is_preinput_ready(self):
        resolution = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            candidates=[
                {
                    "candidate_index": 0,
                    "tag": "button",
                    "role": "button",
                    "relationship": "descendant",
                    "connected": True,
                    "visible": True,
                    "enabled": False,
                    "positive_geometry": True,
                    "pointer_events": "none",
                    "inactive_reasons": [
                        "disabled_attribute",
                        "pointer_events_none",
                    ],
                    "bounding_box": {"x": 10, "y": 10, "width": 30, "height": 30},
                }
            ],
        )

        self.session.detect_image_mode(
            self.settings,
            inspection=inspection_with_resolution(resolution),
        )

        self.assertTrue(resolution["structural_generation_control_ready"])
        self.assertEqual(
            resolution["candidates"][0]["inactive_reasons"],
            ["disabled_attribute", "pointer_events_none"],
        )

    def test_two_global_controls_with_one_owned_control_succeeds(self):
        resolution = composer_resolution(
            global_generation_control_count=2,
            global_visible_generation_control_count=2,
            ignored_generation_control_count=1,
            candidates=[
                {
                    "candidate_index": 0,
                    "tag": "button",
                    "role": "button",
                    "relationship": "descendant",
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                    "positive_geometry": True,
                    "pointer_events": "auto",
                    "bounding_box": {"x": 10, "y": 10, "width": 30, "height": 30},
                },
                {
                    "candidate_index": 1,
                    "tag": "button",
                    "role": "button",
                    "relationship": "outside",
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                    "positive_geometry": True,
                    "pointer_events": "auto",
                    "bounding_box": {"x": 200, "y": 10, "width": 30, "height": 30},
                },
            ],
        )
        result = self.session.detect_image_mode(
            self.settings,
            inspection=inspection_with_resolution(resolution),
        )
        self.assertEqual(result.mode, "image")
        self.assertEqual(resolution["ignored_generation_control_count"], 1)
        self.assertEqual(resolution["candidates"][1]["relationship"], "outside")

    def test_hidden_or_disabled_duplicate_inside_composer_is_ignored(self):
        for state in ("hidden", "disabled"):
            with self.subTest(state=state):
                duplicate = {
                    "candidate_index": 1,
                    "tag": "button",
                    "role": "button",
                    "relationship": "descendant",
                    "connected": True,
                    "visible": state != "hidden",
                    "enabled": state != "disabled",
                    "positive_geometry": state != "hidden",
                    "pointer_events": "auto",
                    "bounding_box": None,
                }
                resolution = composer_resolution(
                    global_generation_control_count=2,
                    active_composer_generation_control_count=2,
                    structurally_owned_generation_control_count=(
                        1 if state == "hidden" else 2
                    ),
                    actionable_owned_generation_control_count=1,
                    ignored_generation_control_count=1,
                    candidates=[duplicate],
                )
                result = self.session.detect_image_mode(
                    self.settings,
                    inspection=inspection_with_resolution(resolution),
                )
                self.assertEqual(result.mode, "image")

    def test_zero_or_two_structurally_owned_controls_fail_closed(self):
        cases = (
            (0, "owned_generation_control_missing"),
            (2, "owned_generation_control_ambiguous"),
        )
        for count, reason in cases:
            with self.subTest(count=count):
                resolution = composer_resolution(
                    active_composer_generation_control_count=count,
                    structurally_owned_generation_control_count=count,
                    actionable_owned_generation_control_count=count,
                    structural_generation_control_ready=False,
                    generation_control_actionable=False,
                    ready=False,
                    failure_reason=reason,
                    owned_control_label=None,
                )
                with self.assertRaisesRegex(
                    FlowGenerationPreflightError, "No generation was submitted"
                ):
                    self.session.detect_image_mode(
                        self.settings,
                        inspection=inspection_with_resolution(resolution),
                    )

    def test_one_global_start_without_structural_relationship_fails_closed(self):
        resolution = composer_resolution(
            active_composer_count=0,
            active_composer_generation_control_count=0,
            structurally_owned_generation_control_count=0,
            actionable_owned_generation_control_count=0,
            structural_generation_control_ready=False,
            generation_control_actionable=False,
            ready=False,
            failure_reason="active_composer_unproven",
            owned_control_label=None,
            candidates=[
                {
                    "candidate_index": 0,
                    "tag": "button",
                    "role": "button",
                    "relationship": "outside",
                    "connected": True,
                    "visible": True,
                    "enabled": False,
                    "positive_geometry": True,
                    "pointer_events": "none",
                    "inactive_reasons": [
                        "disabled_attribute",
                        "pointer_events_none",
                    ],
                    "bounding_box": {"x": 200, "y": 10, "width": 30, "height": 30},
                }
            ],
        )

        with self.assertRaisesRegex(
            FlowGenerationPreflightError, "No generation was submitted"
        ):
            self.session.detect_image_mode(
                self.settings,
                inspection=inspection_with_resolution(resolution),
            )

    def test_prompt_editor_ambiguity_fails_closed(self):
        resolution = composer_resolution(
            prompt_editor_count=2,
            active_composer_count=0,
            ready=False,
            failure_reason="prompt_editor_ambiguous",
            owned_control_label=None,
        )
        current = inspection_with_resolution(resolution)
        duplicate_prompt = dict(current.prompt_control_candidates[0])
        current = FlowInspection(
            **{
                **current.to_dict(),
                "prompt_control_candidates": (
                    current.prompt_control_candidates[0],
                    duplicate_prompt,
                ),
            }
        )
        with self.assertRaisesRegex(
            FlowGenerationPreflightError, "exactly one active prompt editor"
        ):
            self.session.detect_image_mode(self.settings, inspection=current)

    def test_model_names_remain_informational(self):
        for model in ("Nano Banana Pro", "Nano Banana Lite"):
            with self.subTest(model=model):
                result = self.session.detect_image_mode(
                    self.settings,
                    inspection=inspection_with_resolution(
                        composer_resolution(), model=model
                    ),
                )
                self.assertEqual(result.evidence["model"], model)


class ComposerGenerationSettleTests(unittest.TestCase):
    def session(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        return session, page

    @staticmethod
    def runtime_facts(resolution):
        return {
            **resolution,
            "semantic_match_count": resolution["global_generation_control_count"],
            "connected_match_count": resolution["global_generation_control_count"],
            "visible_match_count": resolution[
                "global_visible_generation_control_count"
            ],
            "fresh_owned_locator_count": resolution[
                "active_composer_generation_control_count"
            ],
            "fresh_structurally_owned_locator_count": resolution[
                "structurally_owned_generation_control_count"
            ],
            "fresh_actionable_owned_locator_count": resolution[
                "actionable_owned_generation_control_count"
            ],
            "dom_changed_during_reacquisition": False,
        }

    def test_preinput_structural_wait_accepts_disabled_start_without_input(self):
        session, page = self.session()
        prompt = MagicMock()
        disabled = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
        )
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(prompt, [], self.runtime_facts(disabled)),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ):
            resolved_prompt, facts = (
                session._wait_for_owned_generation_control_structure(
                    timeout_ms=500
                )
            )

        self.assertIs(resolved_prompt, prompt)
        self.assertTrue(facts["structural_generation_control_ready"])
        self.assertFalse(facts["generation_control_actionable"])
        prompt.fill.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_post_input_disabled_start_becomes_actionable(self):
        session, page = self.session()
        prompt = MagicMock()
        button = MagicMock()
        disabled = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            prompt_populated=True,
        )
        ready = composer_resolution(
            composer_token=11,
            prompt_populated=True,
        )
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=[
                (prompt, [], self.runtime_facts(disabled)),
                (prompt, [button], self.runtime_facts(ready)),
            ],
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            resolved_prompt, resolved_button, _, _ = (
                session._wait_for_generation_submit_button(
                    intended_prompt="safe prompt", timeout_ms=500
                )
            )

        self.assertIs(resolved_prompt, prompt)
        self.assertIs(resolved_button, button)
        page.wait_for_timeout.assert_called_once_with(150)
        self.assertIn(
            "post_input_actionability_wait_elapsed_ms",
            session._composer_generation_observation["resolution"],
        )
        page.mouse.down.assert_not_called()

    def test_post_input_start_never_enables_is_not_submitted(self):
        session, page = self.session()
        prompt = MagicMock()
        disabled = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            prompt_populated=True,
        )
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(prompt, [], self.runtime_facts(disabled)),
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 1.0],
        ), self.assertRaisesRegex(
            FlowGenerationPreflightError, "No generation was submitted"
        ):
            session._wait_for_generation_submit_button(
                intended_prompt="safe prompt", timeout_ms=500
            )

        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        self.assertEqual(
            session._composer_generation_observation["resolution"][
                "post_input_actionability_wait_elapsed_ms"
            ],
            session._composer_generation_observation["resolution"][
                "settle_elapsed_ms"
            ],
        )

    def test_post_input_remount_reacquires_fresh_control_before_one_activation(self):
        session, page = self.session()
        old_prompt = MagicMock()
        new_prompt = MagicMock()
        new_button = MagicMock()
        disabled = composer_resolution(
            composer_token=70,
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            prompt_populated=True,
        )
        ready = composer_resolution(composer_token=71, prompt_populated=True)
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=[
                (old_prompt, [], self.runtime_facts(disabled)),
                (new_prompt, [new_button], self.runtime_facts(ready)),
            ],
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            resolved_prompt, resolved_button, _, stable = (
                session._wait_for_generation_submit_button(
                    intended_prompt="safe prompt", timeout_ms=500
                )
            )
        with patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ):
            session._activate_generation_submit_button(resolved_button, stable)

        self.assertIs(resolved_prompt, new_prompt)
        self.assertIs(resolved_button, new_button)
        self.assertTrue(
            session._composer_generation_observation["resolution"][
                "remount_observed"
            ]
        )
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        new_button.click.assert_not_called()

    def test_submit_verifies_exact_prompt_before_exactly_one_activation(self):
        session, page = self.session()
        prompt = MagicMock()
        button = MagicMock()
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        events = []
        prompt.fill.side_effect = lambda value: events.append(("fill", value))

        def read_prompt(_control):
            events.append(("verify", "safe prompt"))
            return "safe prompt"

        def wait_actionable(*, intended_prompt):
            self.assertEqual(intended_prompt, "safe prompt")
            self.assertEqual(events[-1][0], "fill")
            self.assertEqual(read_prompt(prompt), intended_prompt)
            events.append(("actionable", intended_prompt))
            return prompt, button, snapshot, box

        def activate(_button, _box):
            self.assertEqual(events[-1][0], "observer")
            events.append(("activate", None))
            return {"activation_count": 1}

        with patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(prompt, composer_resolution(
                actionable_owned_generation_control_count=0,
                generation_control_actionable=False,
            )),
        ), patch.object(
            session, "_prompt_control_text", side_effect=read_prompt
        ), patch.object(
            session,
            "_wait_for_generation_submit_button",
            side_effect=wait_actionable,
        ), patch.object(
            session,
            "_begin_current_generation_attempt_observation",
            side_effect=lambda: events.append(("observer", None)),
        ), patch.object(
            session, "_activate_generation_submit_button", side_effect=activate
        ) as activate_start, patch.object(
            session, "_arm_current_generation_attempt_observation"
        ), patch.object(
            session,
            "_wait_for_generation_start",
            return_value={"signal": "new_generation_progress"},
        ), patch.object(
            session, "_stop_current_generation_attempt_observation"
        ):
            result = session.submit_prompt(
                "safe prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
            )

        self.assertEqual(
            [event[0] for event in events],
            ["fill", "verify", "actionable", "observer", "activate"],
        )
        self.assertEqual(result["activation"]["activation_count"], 1)
        activate_start.assert_called_once_with(button, box)
        page.mouse.down.assert_not_called()

    def test_duplicate_disappears_after_remount_and_no_start_occurs_during_settle(self):
        session, page = self.session()
        prompt = MagicMock()
        button = MagicMock()
        ambiguous = composer_resolution(
            global_generation_control_count=2,
            global_visible_generation_control_count=2,
            active_composer_generation_control_count=2,
            structurally_owned_generation_control_count=2,
            actionable_owned_generation_control_count=2,
            ignored_generation_control_count=0,
            composer_token=20,
            ready=False,
            failure_reason="owned_generation_control_ambiguous",
            owned_control_label=None,
        )
        ready = composer_resolution(composer_token=21)
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=[
                (prompt, [], self.runtime_facts(ambiguous)),
                (prompt, [button], self.runtime_facts(ready)),
            ],
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            resolved_prompt, resolved_button, _, _ = (
                session._wait_for_generation_submit_button(
                    intended_prompt="safe prompt", timeout_ms=500
                )
            )

        self.assertIs(resolved_prompt, prompt)
        self.assertIs(resolved_button, button)
        self.assertTrue(
            session._composer_generation_observation["resolution"][
                "remount_observed"
            ]
        )
        page.wait_for_timeout.assert_called_once_with(150)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        button.click.assert_not_called()

    def test_editor_readiness_accepts_two_global_controls_when_one_is_owned(self):
        session, page = self.session()
        ambiguous = inspection_with_resolution(
            composer_resolution(
                global_generation_control_count=2,
                global_visible_generation_control_count=2,
                active_composer_generation_control_count=2,
                structurally_owned_generation_control_count=2,
                actionable_owned_generation_control_count=2,
                composer_token=40,
                ready=False,
                failure_reason="owned_generation_control_ambiguous",
                owned_control_label=None,
            )
        )
        ready = inspection_with_resolution(
            composer_resolution(
                global_generation_control_count=2,
                global_visible_generation_control_count=2,
                active_composer_generation_control_count=1,
                structurally_owned_generation_control_count=1,
                actionable_owned_generation_control_count=1,
                ignored_generation_control_count=1,
                composer_token=41,
            )
        )
        session.inspect_environment = MagicMock(side_effect=[ambiguous, ready])
        expected = FlowProjectReference(
            project_id=PROJECT_URL.rsplit("/", 1)[-1],
            project_url=PROJECT_URL,
        )

        observed = session._wait_for_authenticated_image_editor(
            expected,
            timeout_seconds=1,
            poll_interval_ms=350,
        )

        self.assertIs(observed, ready)
        page.wait_for_timeout.assert_called_once_with(150)
        diagnostic = session._editor_readiness_observation
        self.assertEqual(diagnostic["generation_control_count"], 2)
        self.assertEqual(
            diagnostic["actionable_owned_generation_control_count"], 1
        )
        self.assertEqual(diagnostic["ignored_generation_control_count"], 1)
        self.assertTrue(diagnostic["generation_control_ready"])
        self.assertTrue(diagnostic["composer_remount_observed"])
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_window_recovery_then_temporary_duplicate_continues_same_operation(self):
        session, page = self.session()
        ambiguous = inspection_with_resolution(
            composer_resolution(
                global_generation_control_count=2,
                global_visible_generation_control_count=2,
                active_composer_generation_control_count=2,
                structurally_owned_generation_control_count=2,
                actionable_owned_generation_control_count=2,
                composer_token=50,
                ready=False,
                failure_reason="owned_generation_control_ambiguous",
                owned_control_label=None,
            )
        )
        ready = inspection_with_resolution(composer_resolution(composer_token=51))
        session.inspect_environment = MagicMock(return_value=ready)
        recovery = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": True,
            "recovery_success": True,
        }
        usable = {
            "classification": "WINDOW_RECOVERY",
            "recovery_required": False,
            "recovery_success": True,
        }
        candidate = DiscoveredCandidate(
            "rendered-image", candidate_id="same-operation-candidate"
        )

        def configure(_settings):
            settled = session._wait_for_active_composer_generation_inspection(
                ambiguous,
                timeout_ms=500,
            )
            self.assertIs(settled, ready)
            return {"configured_variants": 1}

        with patch.object(
            FlowSession, "__enter__", return_value=session
        ), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session,
            "ensure_production_window_usable",
            side_effect=[recovery, usable],
        ) as normalize, patch.object(
            session, "configure_generation", side_effect=configure
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session,
            "submit_prompt",
            return_value={
                "acknowledgement": {"signal": "new_generation_progress"},
                "ack_elapsed_ms": 50,
                "prestart_timing": {
                    "schema": "nano-banana-prestart-timing/v1",
                    "fill_to_mouse_down_ms": 425,
                },
                "post_start_ack_timing": {
                    "schema": "nano-banana-post-start-ack-timing/v1",
                    "ack_ms": 50,
                    "ack_signal": "new_generation_progress",
                },
            },
        ) as submit, patch.object(
            session, "wait_for_result", return_value=(candidate,)
        ), patch.object(
            session, "_stop_current_generation_attempt_observation"
        ):
            result = session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )

        self.assertEqual(result.candidates, (candidate,))
        persisted_details = json.loads(result.details)
        self.assertEqual(
            persisted_details["prestart_timing"]["fill_to_mouse_down_ms"],
            425,
        )
        self.assertEqual(
            persisted_details["post_start_ack_timing"]["ack_signal"],
            "new_generation_progress",
        )
        self.assertEqual(normalize.call_count, 2)
        submit.assert_called_once()
        page.wait_for_timeout.assert_called_once_with(150)

    def test_owned_ambiguity_timeout_fails_without_activation(self):
        session, page = self.session()
        prompt = MagicMock()
        ambiguous = composer_resolution(
            global_generation_control_count=2,
            global_visible_generation_control_count=2,
            active_composer_generation_control_count=2,
            structurally_owned_generation_control_count=2,
            actionable_owned_generation_control_count=2,
            composer_token=30,
            ready=False,
            failure_reason="owned_generation_control_ambiguous",
            owned_control_label=None,
        )
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(prompt, [], self.runtime_facts(ambiguous)),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 1.0],
        ), self.assertRaisesRegex(
            FlowGenerationPreflightError, "No generation was submitted"
        ):
            session._wait_for_generation_submit_button(
                intended_prompt="safe prompt", timeout_ms=500
            )

        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_resolution_then_activation_dispatches_exactly_one_start(self):
        session, page = self.session()
        prompt = MagicMock()
        button = MagicMock()
        ready = composer_resolution()
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(prompt, [button], self.runtime_facts(ready)),
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ):
            _, resolved, _, stable = session._wait_for_generation_submit_button(
                intended_prompt="safe prompt", timeout_ms=500
            )
        with patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ):
            session._activate_generation_submit_button(resolved, stable)

        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        button.click.assert_not_called()

    def test_failed_final_pre_mouse_proof_is_not_submitted(self):
        session, page = self.session()
        button = MagicMock()
        box = {"x": 1.0, "y": 2.0, "width": 30.0, "height": 30.0}
        with patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=FlowAutomationError("GENERATION_SUBMIT_DISABLED"),
        ), self.assertRaises(FlowGenerationPreflightError) as raised:
            session._activate_generation_submit_button(button, box)

        self.assertEqual(raised.exception.code, "FLOW_GENERATION_NOT_SUBMITTED")
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_prompt_entry_failure_is_not_submitted_and_never_starts(self):
        session, page = self.session()
        prompt = MagicMock()
        prompt.fill.side_effect = RuntimeError("editor remounted")
        with patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(prompt, composer_resolution(
                actionable_owned_generation_control_count=0,
                generation_control_actionable=False,
            )),
        ), patch.object(
            session, "_wait_for_generation_submit_button"
        ) as wait_actionable, patch.object(
            session, "_begin_current_generation_attempt_observation"
        ) as begin_observer, self.assertRaises(
            FlowGenerationPreflightError
        ) as raised:
            session.submit_prompt(
                "safe prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
            )

        self.assertEqual(raised.exception.code, "FLOW_GENERATION_NOT_SUBMITTED")
        wait_actionable.assert_not_called()
        begin_observer.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_resolution_paths_have_no_global_positional_or_click_fallback(self):
        source = "\n".join(
            (
                inspect.getsource(
                    FlowSession._read_active_composer_generation_resolution
                ),
                inspect.getsource(FlowSession._current_generation_submit_buttons),
                inspect.getsource(FlowSession._wait_for_generation_submit_button),
            )
        ).casefold()
        for forbidden in (
            ".first",
            ".last",
            ".nth(",
            ".click(",
            "bringtofront",
            "bring_to_front",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("node.contains(control)", source)
        self.assertIn("composer.get_by_role", source)
        self.assertIn("const structural = (element) => visible(element)", source)
        self.assertIn(
            "item.settingcount > 0 && item.structurallyowned.length > 0",
            source,
        )
        self.assertNotIn(
            "item.settingcount > 0 && item.actionableowned.length > 0",
            source,
        )

    def test_diagnostics_are_bounded_and_prompt_free(self):
        candidates = [
            {
                "candidate_index": index,
                "tag": "button",
                "role": "button",
                "relationship": "outside",
                "connected": True,
                "visible": True,
                "enabled": True,
                "positive_geometry": True,
                "pointer_events": "auto",
                "bounding_box": {"x": index, "y": 1, "width": 2, "height": 3},
                "prompt": "private prompt",
            }
            for index in range(80)
        ]
        normalized = _normalize_composer_generation_resolution(
            {
                **composer_resolution(),
                "candidates": candidates,
            }
        )
        self.assertEqual(len(normalized["candidates"]), 30)
        self.assertNotIn("private prompt", str(normalized))


class PreStartTimingDiagnosticsTests(unittest.TestCase):
    class FakeClock:
        def __init__(self):
            self.current = 0.0

        def __call__(self):
            return self.current

        def advance(self, seconds):
            self.current += seconds

    def session(self, *, debug=False):
        session = FlowSession(
            PROJECT_URL,
            Path("profile"),
            prestart_timing_debug=debug,
        )
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        return session, page

    def test_complete_timeline_is_monotonic_calculated_and_prompt_free(self):
        session, page = self.session()
        clock = self.FakeClock()
        prompt = MagicMock()
        button = MagicMock()
        ready = composer_resolution(prompt_populated=True)
        facts = ComposerGenerationSettleTests.runtime_facts(ready)
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]

        prompt.fill.side_effect = lambda _value: clock.advance(0.100)

        def current_controls():
            clock.advance(0.200)
            return prompt, [button], facts

        def prompt_text(_control):
            clock.advance(0.100)
            return "private timing prompt"

        def inspect_start(_control):
            clock.advance(0.300)
            return snapshot

        def hit_start(_control, _box):
            clock.advance(0.050)
            return box

        page.mouse.move.side_effect = lambda *_args: clock.advance(0.030)
        page.mouse.down.side_effect = lambda **_kwargs: clock.advance(0.010)
        page.mouse.up.side_effect = lambda **_kwargs: clock.advance(0.010)

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(prompt, facts),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=current_controls,
        ), patch.object(
            session, "_prompt_control_text", side_effect=prompt_text
        ), patch.object(
            session, "_inspect_generation_submit_button", side_effect=inspect_start
        ), patch.object(
            session, "_current_generation_submit_hit", side_effect=hit_start
        ), patch.object(
            session,
            "_begin_current_generation_attempt_observation",
            side_effect=lambda: clock.advance(0.020),
        ), patch.object(
            session,
            "_arm_current_generation_attempt_observation",
            side_effect=lambda: clock.advance(0.010),
        ), patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: (clock.advance(0.200) or ("Generating",)),
        ), patch.object(
            session, "_stop_current_generation_attempt_observation"
        ):
            session._begin_prestart_timing()
            session._prestart_mark("candidate_baseline_start_ms")
            clock.advance(0.100)
            session._prestart_mark("candidate_baseline_complete_ms")
            session._prestart_mark("progress_baseline_start_ms")
            clock.advance(0.050)
            session._prestart_mark("progress_baseline_complete_ms")
            result = session.submit_prompt(
                "private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
            )

        timing = result["prestart_timing"]
        expected_phases = {
            "prompt_fill_start_ms",
            "prompt_fill_end_ms",
            "prompt_verification_start_ms",
            "prompt_verified_ms",
            "composer_reacquire_start_ms",
            "composer_reacquired_ms",
            "start_control_reacquire_start_ms",
            "start_control_reacquired_ms",
            "start_actionability_wait_start_ms",
            "start_actionable_ms",
            "candidate_baseline_start_ms",
            "candidate_baseline_complete_ms",
            "progress_baseline_start_ms",
            "progress_baseline_complete_ms",
            "failure_observer_arm_start_ms",
            "failure_observer_armed_ms",
            "final_prestart_validation_start_ms",
            "final_prestart_validation_complete_ms",
            "mouse_position_start_ms",
            "mouse_position_complete_ms",
            "start_mouse_down_ms",
            "start_mouse_up_ms",
            "start_ack_wait_start_ms",
            "start_ack_ms",
        }
        self.assertTrue(expected_phases.issubset(timing))
        self.assertTrue(timing["monotonic_elapsed_non_decreasing"])
        self.assertEqual(
            timing["fill_to_mouse_down_ms"],
            timing["start_mouse_down_ms"] - timing["prompt_fill_start_ms"],
        )
        self.assertEqual(
            timing["fill_end_to_mouse_down_ms"],
            timing["start_mouse_down_ms"] - timing["prompt_fill_end_ms"],
        )
        self.assertEqual(
            timing["total_prestart_duration_ms"],
            timing["fill_to_mouse_down_ms"],
        )
        self.assertLess(timing["candidate_baseline_start_ms"], 0)
        self.assertLess(
            timing["failure_observer_armed_ms"], timing["start_mouse_down_ms"]
        )
        self.assertEqual(
            timing["polling"]["post_fill_composer_actionability"]["poll_count"],
            1,
        )
        self.assertEqual(
            timing["polling"]["composer_reacquisition"]["final_reason"],
            "fresh_composer_reacquired",
        )
        self.assertEqual(
            timing["polling"]["owned_start_reacquisition"]["final_reason"],
            "fresh_owned_start_reacquired",
        )
        self.assertEqual(
            timing["polling"]["angular_remount_settle"]["final_reason"],
            "fresh_dom_stable",
        )
        self.assertNotIn("private timing prompt", json.dumps(timing))
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")

    def test_four_second_actionability_wait_is_attributed_without_real_sleep(self):
        session, page = self.session()
        clock = self.FakeClock()
        prompt = MagicMock()
        button = MagicMock()
        disabled = composer_resolution(
            actionable_owned_generation_control_count=0,
            generation_control_actionable=False,
            prompt_populated=True,
        )
        ready = composer_resolution(prompt_populated=True)
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        page.wait_for_timeout.side_effect = lambda _ms: clock.advance(4.0)

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=[
                (
                    prompt,
                    [],
                    ComposerGenerationSettleTests.runtime_facts(disabled),
                ),
                (
                    prompt,
                    [button],
                    ComposerGenerationSettleTests.runtime_facts(ready),
                ),
            ],
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ):
            session._begin_prestart_timing()
            session._prestart_mark("prompt_fill_start_ms")
            session._prestart_mark("start_actionability_wait_start_ms")
            session._wait_for_generation_submit_button(
                intended_prompt="safe prompt",
                timeout_ms=5_000,
            )

        timing = session._prestart_timing_snapshot()
        self.assertEqual(timing["start_actionability_duration_ms"], 4_000)
        actionability = timing["polling"]["post_fill_composer_actionability"]
        self.assertEqual(actionability["poll_count"], 2)
        self.assertFalse(actionability["success_immediate"])
        self.assertEqual(actionability["final_reason"], "stable_owned_start_actionable")
        page.wait_for_timeout.assert_called_once_with(150)

    def test_immediate_actionability_is_zero_in_fake_time(self):
        session, page = self.session()
        clock = self.FakeClock()
        prompt = MagicMock()
        button = MagicMock()
        ready = composer_resolution(prompt_populated=True)
        snapshot = generation_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(
                prompt,
                [button],
                ComposerGenerationSettleTests.runtime_facts(ready),
            ),
        ), patch.object(
            session, "_prompt_control_text", return_value="safe prompt"
        ), patch.object(
            session, "_inspect_generation_submit_button", return_value=snapshot
        ), patch.object(
            session, "_current_generation_submit_hit", return_value=box
        ):
            session._begin_prestart_timing()
            session._prestart_mark("prompt_fill_start_ms")
            session._prestart_mark("start_actionability_wait_start_ms")
            session._wait_for_generation_submit_button(
                intended_prompt="safe prompt",
            )

        timing = session._prestart_timing_snapshot()
        self.assertEqual(timing["start_actionability_duration_ms"], 0)
        self.assertTrue(
            timing["polling"]["post_fill_composer_actionability"][
                "success_immediate"
            ]
        )
        page.wait_for_timeout.assert_not_called()

    def test_terminal_output_is_opt_in_and_prompt_free(self):
        timing = {
            key: index * 10
            for index, key in enumerate(
                (
                    "fill_duration_ms",
                    "prompt_verify_duration_ms",
                    "composer_reacquire_duration_ms",
                    "start_control_reacquire_duration_ms",
                    "start_actionability_duration_ms",
                    "candidate_baseline_duration_ms",
                    "progress_baseline_duration_ms",
                    "observer_arm_duration_ms",
                    "final_prestart_validation_duration_ms",
                    "mouse_position_duration_ms",
                    "fill_end_to_mouse_down_ms",
                    "mouse_down_to_ack_ms",
                )
            )
        }
        output = FlowSession._format_prestart_timing(timing)
        self.assertIn("PRE-START TIMING", output)
        self.assertIn("fill end -> mouse down", output)
        self.assertNotIn("private prompt", output)

        session, _page = self.session(debug=False)
        session._prestart_timing_observation = timing
        with redirect_stdout(StringIO()) as captured:
            if session.prestart_timing_debug:
                print(session._format_prestart_timing(timing))
        self.assertEqual(captured.getvalue(), "")

    def test_timing_instrumentation_adds_no_sleep_or_activation_fallback(self):
        source = "\n".join(
            (
                inspect.getsource(FlowSession.submit_prompt),
                inspect.getsource(FlowSession._wait_for_generation_submit_button),
                inspect.getsource(FlowSession._activate_generation_submit_button),
            )
        ).casefold()
        self.assertNotIn("time.sleep", source)
        self.assertNotIn("asyncio.sleep", source)
        self.assertNotIn(".click(", source)
        self.assertNotIn("bringtofront", source)
        self.assertIn(
            "page.wait_for_timeout(_composer_generation_settle_interval_ms)",
            source,
        )


class PostStartAcknowledgementTimingTests(unittest.TestCase):
    FakeClock = PreStartTimingDiagnosticsTests.FakeClock

    def session(self):
        session = FlowSession(
            PROJECT_URL,
            Path("profile"),
            prestart_timing_debug=True,
        )
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        return session, page

    @staticmethod
    def begin_at_mouse_down(session, clock, *, composer_token=10, mouse_up_ms=0):
        session._post_start_initial_composer_token = composer_token
        session._begin_post_start_ack_timing(origin_source="mouse_down_boundary")
        if mouse_up_ms:
            clock.advance(mouse_up_ms / 1_000)
        session._post_start_ack_timing.mark_once("start_mouse_up_ms")

    def test_first_mutation_and_progress_timestamps_are_captured(self):
        session, _page = self.session()
        clock = self.FakeClock()

        def failures():
            clock.advance(0.084)
            session._current_generation_failure_poll_observation = {
                "generation_dom_mutation_count": 3,
                "exact_explicit_failure_detected": False,
            }
            return ()

        def progress():
            clock.advance(0.800)
            return ("Generating",)

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session, "_current_generation_attempt_failures", side_effect=failures
        ), patch.object(
            session, "_progress_signals", side_effect=progress
        ), patch.object(session, "discover_candidates") as discover:
            self.begin_at_mouse_down(session, clock, mouse_up_ms=18)
            result = session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "new_generation_progress")
        self.assertEqual(timing["start_mouse_up_ms"], 18)
        self.assertEqual(timing["first_post_start_mutation_ms"], 102)
        self.assertEqual(timing["first_progress_signal_ms"], 902)
        self.assertEqual(timing["ack_ms"], 902)
        self.assertEqual(timing["ack_signal"], "new_generation_progress")
        discover.assert_not_called()
        self.assertNotIn("private timing prompt", json.dumps(timing))

    def test_candidate_and_uuid_first_signal_timestamps_are_captured(self):
        session, _page = self.session()
        clock = self.FakeClock()
        candidate = DiscoveredCandidate("rendered-image", candidate_id="current-v01")

        def advance_empty(seconds, value):
            clock.advance(seconds)
            return value

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            side_effect=lambda: advance_empty(0.100, ()),
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: advance_empty(0.100, ()),
        ), patch.object(
            session,
            "discover_candidates",
            side_effect=lambda *_args, **_kwargs: advance_empty(0.200, (candidate,)),
        ):
            self.begin_at_mouse_down(session, clock)
            result = session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "new_candidate_visible")
        self.assertEqual(timing["first_candidate_signal_ms"], 400)
        self.assertEqual(timing["candidate_uuid_appeared_ms"], 400)
        self.assertEqual(timing["ack_ms"], 400)

    def test_start_hidden_timestamp_and_existing_ack_signal_are_captured(self):
        session, _page = self.session()
        clock = self.FakeClock()
        hidden = ComposerGenerationSettleTests.runtime_facts(
            composer_resolution(
                active_composer_generation_control_count=1,
                structurally_owned_generation_control_count=0,
                actionable_owned_generation_control_count=0,
                structural_generation_control_ready=False,
                generation_control_actionable=False,
            )
        )

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "discover_candidates",
            side_effect=lambda *_args, **_kwargs: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=lambda: (clock.advance(0.200) or (MagicMock(), [], hidden)),
        ):
            self.begin_at_mouse_down(session, clock)
            result = session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "submit_button_hidden")
        self.assertEqual(timing["start_button_hidden_ms"], 500)
        self.assertEqual(timing["ack_ms"], 500)

    def test_start_disabled_timestamp_preserves_existing_disabled_ack(self):
        session, _page = self.session()
        clock = self.FakeClock()
        prompt = MagicMock()
        button = MagicMock()
        ready = ComposerGenerationSettleTests.runtime_facts(
            composer_resolution(prompt_populated=True)
        )

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "discover_candidates",
            side_effect=lambda *_args, **_kwargs: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=lambda: (clock.advance(0.100) or (prompt, [button], ready)),
        ), patch.object(
            session,
            "_live_control_facts",
            side_effect=lambda _button: (
                clock.advance(0.100)
                or {
                    "connected": True,
                    "visible": True,
                    "enabled": False,
                    "pointer_events": "auto",
                }
            ),
        ):
            self.begin_at_mouse_down(session, clock)
            result = session._wait_for_generation_start(
                prompt_input=prompt,
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "submit_button_disabled")
        self.assertEqual(timing["start_button_disabled_ms"], 500)
        self.assertNotIn("start_button_hidden_ms", timing)

    def test_prompt_transition_and_composer_remount_are_captured(self):
        session, _page = self.session()
        clock = self.FakeClock()
        prompt = MagicMock()
        button = MagicMock()
        remounted = ComposerGenerationSettleTests.runtime_facts(
            composer_resolution(composer_token=11, prompt_populated=False)
        )

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "discover_candidates",
            side_effect=lambda *_args, **_kwargs: (clock.advance(0.100) or ()),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=lambda: (clock.advance(0.100) or (prompt, [button], remounted)),
        ), patch.object(
            session,
            "_live_control_facts",
            side_effect=lambda _button: (
                clock.advance(0.100)
                or {
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                    "pointer_events": "auto",
                }
            ),
        ), patch.object(
            session,
            "_prompt_control_post_start_state",
            side_effect=lambda *_args: (
                clock.advance(0.100)
                or {
                    "connected": True,
                    "changed": True,
                    "cleared": True,
                    "disabled": True,
                }
            ),
        ):
            self.begin_at_mouse_down(session, clock, composer_token=10)
            result = session._wait_for_generation_start(
                prompt_input=prompt,
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "prompt_editor_transition")
        self.assertEqual(timing["composer_remounted_ms"], 400)
        self.assertEqual(timing["prompt_editor_transition_ms"], 600)
        self.assertEqual(timing["prompt_editor_cleared_ms"], 600)
        self.assertEqual(timing["prompt_editor_disabled_ms"], 600)

    def test_exact_failure_timestamp_uses_existing_immediate_exception(self):
        session, _page = self.session()
        clock = self.FakeClock()

        def explicit_failure():
            clock.advance(0.250)
            session._current_generation_failure_poll_observation = {
                "generation_dom_mutation_count": 2,
                "exact_explicit_failure_detected": True,
            }
            raise FlowAutomationError("exact synthetic current-attempt failure")

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_raise_for_current_generation_attempt_failure",
            side_effect=explicit_failure,
        ), self.assertRaises(FlowAutomationError):
            self.begin_at_mouse_down(session, clock)
            session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(timing["explicit_failure_ms"], 250)
        self.assertEqual(timing["first_post_start_mutation_ms"], 250)
        self.assertEqual(timing["ack_signal"], "explicit_failure")

    def test_state_change_trace_is_run_length_compressed_and_bounded(self):
        session, _page = self.session()
        clock = self.FakeClock()
        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ):
            self.begin_at_mouse_down(session, clock)
            recorder = session._post_start_ack_timing
            recorder.configure_wait(timeout_ms=5_000, poll_interval_ms=100)
            for index in range(40):
                recorder.begin_poll()
                clock.advance(0.001)
                recorder.stage_complete(
                    "progress_check",
                    progress_signal_count=index % 2,
                )
                recorder.finish_poll()

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(timing["poll_count"], 40)
        self.assertEqual(len(timing["state_change_trace"]), 16)
        self.assertTrue(timing["trace_truncated"])
        self.assertNotIn("private", json.dumps(timing))

    def test_seven_second_wall_time_identifies_pre_wait_and_poll_overrun(self):
        session, _page = self.session()
        clock = self.FakeClock()
        hidden = ComposerGenerationSettleTests.runtime_facts(
            composer_resolution(
                active_composer_generation_control_count=1,
                structurally_owned_generation_control_count=0,
                actionable_owned_generation_control_count=0,
                structural_generation_control_ready=False,
                generation_control_actionable=False,
            )
        )

        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            side_effect=lambda: (clock.advance(1.500) or ()),
        ), patch.object(
            session,
            "_progress_signals",
            side_effect=lambda: (clock.advance(1.500) or ()),
        ), patch.object(
            session,
            "discover_candidates",
            side_effect=lambda *_args, **_kwargs: (clock.advance(1.500) or ()),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            side_effect=lambda: (clock.advance(1.500) or (MagicMock(), [], hidden)),
        ):
            self.begin_at_mouse_down(session, clock, mouse_up_ms=800)
            result = session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        timing = session._post_start_ack_timing_snapshot()
        self.assertEqual(result["signal"], "submit_button_hidden")
        self.assertEqual(timing["ack_wait_start_ms"], 800)
        self.assertEqual(timing["nominal_ack_deadline_ms"], 5_800)
        self.assertEqual(timing["ack_ms"], 6_800)
        self.assertEqual(timing["deadline_overrun_ms"], 1_000)
        self.assertEqual(
            timing["stage_max_ms"],
            {
                "explicit_failure_check": 1_500,
                "progress_check": 1_500,
                "candidate_check": 1_500,
                "composer_check": 1_500,
            },
        )

    def test_terminal_trace_is_concise_and_prompt_free(self):
        timing = {
            "start_mouse_up_ms": 18,
            "first_post_start_mutation_ms": 102,
            "start_button_hidden_ms": 4_122,
            "ack_ms": 4_130,
            "ack_signal": "submit_button_hidden",
            "state_change_trace": [],
        }

        output = FlowSession._format_post_start_ack_timing(timing)

        self.assertIn("POST-START ACK TIMING", output)
        self.assertIn("+    0 ms  mouse down", output)
        self.assertIn("ACK: submit_button_hidden", output)
        self.assertNotIn("private prompt", output)
        self.assertLessEqual(len(output.splitlines()), 12)

    def test_instrumentation_adds_no_sleep_or_acknowledgement_branch(self):
        source = inspect.getsource(FlowSession._wait_for_generation_start).casefold()
        for forbidden in ("time.sleep", "asyncio.sleep", ".click(", "bringtofront"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(
            source.count(
                '_ack_call("poll_wait", page.wait_for_timeout, _generation_start_ack_poll_interval_ms)'
            ),
            1,
        )
        for signal in (
            "new_generation_progress",
            "new_candidate_visible",
            "submit_button_removed",
            "submit_button_hidden",
            "submit_button_disabled",
            "prompt_editor_transition",
        ):
            self.assertIn(f'acknowledged("{signal}")', source)

    def test_ctrl_c_preempts_all_acknowledgement_reads(self):
        session, page = self.session()
        clock = self.FakeClock()
        with patch(
            "nano_batch.adapters.flow.time.perf_counter", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=clock
        ), patch(
            "nano_batch.adapters.flow.cancellation_checkpoint",
            side_effect=KeyboardInterrupt,
        ), patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ) as failure, patch.object(
            session, "_progress_signals"
        ) as progress, patch.object(
            session, "discover_candidates"
        ) as candidates, self.assertRaises(KeyboardInterrupt):
            self.begin_at_mouse_down(session, clock)
            session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="private timing prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
                timeout_ms=5_000,
            )

        failure.assert_not_called()
        progress.assert_not_called()
        candidates.assert_not_called()
        page.wait_for_timeout.assert_not_called()
        self.assertEqual(session._post_start_ack_timing_snapshot()["poll_count"], 0)


class GenerationInterruptCheckpointTests(unittest.TestCase):
    def session(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        return session, page

    def test_interrupt_before_prompt_entry_is_proven_not_submitted(self):
        session, _page = self.session()
        with patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            side_effect=KeyboardInterrupt,
        ), self.assertRaises(KeyboardInterrupt) as raised:
            with session._generation_interrupt_scope():
                session.submit_prompt(
                    "safe prompt",
                    baseline_fingerprints=set(),
                    baseline_progress=set(),
                )

        self.assertEqual(raised.exception.interrupt_phase, "composer_resolution")
        self.assertFalse(raised.exception.prompt_written)
        self.assertFalse(raised.exception.start_mouse_down_sent)
        self.assertFalse(raised.exception.start_acknowledged)
        self.assertEqual(raised.exception.submission_state, "not_submitted")

    def test_interrupt_after_prompt_entry_while_start_disabled_is_not_submitted(self):
        session, page = self.session()
        prompt = MagicMock()
        with patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(prompt, composer_resolution(
                actionable_owned_generation_control_count=0,
                generation_control_actionable=False,
            )),
        ), patch.object(
            session,
            "_wait_for_generation_submit_button",
            side_effect=KeyboardInterrupt,
        ), self.assertRaises(KeyboardInterrupt) as raised:
            with session._generation_interrupt_scope():
                session.submit_prompt(
                    "safe prompt",
                    baseline_fingerprints=set(),
                    baseline_progress=set(),
                )

        prompt.fill.assert_called_once_with("safe prompt")
        self.assertEqual(
            raised.exception.interrupt_phase,
            "post_input_start_actionability",
        )
        self.assertTrue(raised.exception.prompt_written)
        self.assertFalse(raised.exception.start_mouse_down_sent)
        self.assertEqual(raised.exception.submission_state, "not_submitted")
        page.mouse.down.assert_not_called()

    def test_interrupt_immediately_before_mouse_down_is_not_submitted(self):
        session, page = self.session()
        button = MagicMock()
        box = {"x": 1.0, "y": 2.0, "width": 30.0, "height": 30.0}
        with patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=[box, KeyboardInterrupt()],
        ), self.assertRaises(KeyboardInterrupt) as raised:
            with session._generation_interrupt_scope():
                session._activate_generation_submit_button(button, box)

        self.assertEqual(raised.exception.interrupt_phase, "pre_start_activation")
        self.assertFalse(raised.exception.start_mouse_down_sent)
        self.assertEqual(raised.exception.submission_state, "not_submitted")
        page.mouse.move.assert_called_once()
        page.mouse.down.assert_not_called()

    def test_interrupt_during_mouse_down_is_ambiguous_or_sent(self):
        session, page = self.session()
        button = MagicMock()
        box = {"x": 1.0, "y": 2.0, "width": 30.0, "height": 30.0}
        page.mouse.down.side_effect = KeyboardInterrupt()
        with patch.object(
            session,
            "_current_generation_submit_hit",
            return_value=box,
        ), self.assertRaises(KeyboardInterrupt) as raised:
            with session._generation_interrupt_scope():
                session._activate_generation_submit_button(button, box)

        self.assertEqual(raised.exception.interrupt_phase, "start_mouse_down")
        self.assertTrue(raised.exception.start_mouse_down_sent)
        self.assertFalse(raised.exception.start_acknowledged)
        self.assertEqual(
            raised.exception.submission_state,
            "ambiguous_or_sent",
        )
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_not_called()

    def test_interrupt_during_result_wait_retains_exactly_once_protection(self):
        session, _page = self.session()
        submit_result = {
            "activation": {"activation_count": 1},
            "acknowledgement": {"signal": "new_generation_progress"},
            "ack_elapsed_ms": 50,
        }
        with patch.object(
            FlowSession, "__enter__", return_value=session
        ), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session, "ensure_production_window_usable"
        ), patch.object(
            session,
            "configure_generation",
            return_value={"configured_variants": 1},
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session, "submit_prompt", return_value=submit_result
        ) as submit, patch.object(
            session, "wait_for_result", side_effect=KeyboardInterrupt
        ), patch.object(
            session, "_stop_current_generation_attempt_observation"
        ), self.assertRaises(KeyboardInterrupt) as raised:
            session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )

        submit.assert_called_once()
        self.assertEqual(raised.exception.interrupt_phase, "result_wait")
        self.assertTrue(raised.exception.start_mouse_down_sent)
        self.assertTrue(raised.exception.start_acknowledged)
        self.assertEqual(
            raised.exception.submission_state,
            "ambiguous_or_sent",
        )

    def test_intentional_cleanup_filters_only_known_asyncio_socket_warning(self):
        session, _page = self.session()
        browser = MagicMock()
        playwright = MagicMock()
        session._browser = browser
        session._playwright = playwright

        def stop_with_warnings():
            logger = logging.getLogger("asyncio")
            logger.warning("socket.send() raised exception.")
            logger.warning("unexpected CDP transport failure")

        playwright.stop.side_effect = stop_with_warnings
        with self.assertLogs("asyncio", level="WARNING") as captured:
            session.close(intentional_cancellation=True)

        self.assertNotIn(
            "socket.send() raised exception.",
            "\n".join(captured.output),
        )
        self.assertIn("unexpected CDP transport failure", "\n".join(captured.output))
        browser.close.assert_not_called()
        playwright.stop.assert_called_once_with()

    def test_normal_cleanup_does_not_suppress_socket_warning(self):
        session, _page = self.session()
        browser = MagicMock()
        session._browser = browser
        browser.close.side_effect = lambda: logging.getLogger("asyncio").warning(
            "socket.send() raised exception."
        )

        with self.assertLogs("asyncio", level="WARNING") as captured:
            session.close()

        self.assertIn("socket.send() raised exception.", "\n".join(captured.output))

    def test_keyboard_interrupt_exit_enables_only_cancellation_cleanup_filter(self):
        session, _page = self.session()
        exc = KeyboardInterrupt()
        with patch.object(session, "close") as close:
            session.__exit__(KeyboardInterrupt, exc, None)

        close.assert_called_once_with(intentional_cancellation=True)

    def test_unexpected_transport_failure_during_normal_cleanup_surfaces(self):
        session, _page = self.session()
        browser = MagicMock()
        session._browser = browser
        browser.close.side_effect = RuntimeError("unexpected CDP disconnect")

        with self.assertRaisesRegex(RuntimeError, "unexpected CDP disconnect"):
            session.close()


if __name__ == "__main__":
    unittest.main()
