import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowDebugUnavailableError,
    FlowInspection,
    FlowProfileLockedError,
    FlowSession,
    FlowTabAmbiguousError,
    FlowTabNotFoundError,
    FlowUsageLimitReached,
    _REQUIRED_BACKGROUND_FLAGS,
    _bounded_project_ui_controls,
    _candidate_more_options_has_material_snackbar_occlusion,
    _candidate_more_options_safe_point,
    _composer_model_labels,
    _download_action_acceptance,
    _download_submenu_signature,
    _flow_project_identity,
    _normalize_download_control_snapshot,
    _normalize_download_submenu_snapshot,
    _normalize_model_name,
    _normalize_trace_hook_event,
    _observed_download_mechanisms,
    _stable_download_control_box,
    _correlate_download_trace,
    _safe_label,
    _safe_trace_header,
    _safe_visible_url,
    _sanitize_trace_url,
    _stable_candidate_more_options_box,
    _stable_generation_submit_box,
)
from nano_batch.models import (
    BatchItem,
    BrowserDownloadResult,
    CandidateRecord,
    DiscoveredCandidate,
    FlowProjectReference,
    GenerationSettings,
)


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"


class PageStub:
    def __init__(self, url):
        self.url = url


def flow_inspection(**overrides):
    prompt = {
        "tag": "div",
        "role": None,
        "label": None,
        "placeholder": "What do you want to create?",
        "name": None,
        "contenteditable": "true",
    }
    values = {
        "current_url": PROJECT_URL,
        "title": "Flow project",
        "document_ready_state": "complete",
        "visible_body_text_length": 378,
        "authentication_state": "editor_visible",
        "visible_buttons": ("Start generation",),
        "editable_controls": (prompt,),
        "prompt_control_candidates": (prompt,),
        "comboboxes": (),
        "tabs": (),
        "menus": (),
        "selected_controls": (),
        "generation_controls": ("Nano Banana 2 crop_16_9 x2", "Start generation"),
        "generation_action_controls": ("Start generation",),
        "model_labels": ("Nano Banana 2 crop_16_9 x2",),
        "model_related_text": ("Nano Banana 2 crop_16_9 x2",),
        "aspect_ratio_controls": ("Nano Banana 2 crop_16_9 x2",),
        "download_controls": (),
        "rendered_assets": (),
        "media": (),
        "iframes": (),
        "loading_or_error_states": (),
    }
    values.update(overrides)
    return FlowInspection(**values)


def composer_model_control(**overrides):
    values = {
        "tag": "button",
        "role": "button",
        "connected": True,
        "visible": True,
        "innerText": "",
        "ariaLabel": "",
        "labelledByText": "",
        "title": "",
        "nestedVisibleText": [],
    }
    values.update(overrides)
    return values


def download_control_snapshot(**overrides):
    values = {
        "tag": "button",
        "role": "menuitem",
        "visible_text": "downloadDownload",
        "aria_label": None,
        "title": None,
        "disabled": False,
        "aria_disabled": None,
        "tabindex": "0",
        "pointer_events": "auto",
        "connected": True,
        "visible": True,
        "bounding_box": {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0},
        "bounding_box_samples": [
            {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0},
            {"x": 100.2, "y": 200.1, "width": 140.0, "height": 24.0},
            {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0},
        ],
        "center_top_belongs_to_control": True,
        "center_top": {"tag": "span", "text": "Download", "connected": True},
        "elements_from_center": [
            {"tag": "span", "text": "Download", "connected": True},
            {"tag": "button", "role": "menuitem", "text": "downloadDownload", "connected": True},
        ],
        "descendants": [{"tag": "span", "text": "Download", "connected": True}],
        "ancestors": [{"tag": "div", "role": "menu", "connected": True}],
        "active_element": {"tag": "button", "text": "More options", "connected": True},
    }
    values.update(overrides)
    return _normalize_download_control_snapshot(values)


def raise_inert_download_submenu_failure(diagnostics):
    diagnostics.update(
        {
            "parent_activation_count": 1,
            "activation_dispatch_returned_normally": True,
            "aria_expanded": "false",
            "aria_expanded_ever_true": False,
            "controlled_submenu_ever_appeared": False,
            "controlled_panel_seen_temporarily": False,
            "ambiguity_persisted": False,
            "final_parent": {
                "connected": True,
                "visible": True,
                "enabled": True,
                "pointer_events": "auto",
                "aria_expanded": "false",
                "aria_controls": None,
            },
            "final_safe_failure_classification": "DOWNLOAD_SUBMENU_NOT_FOUND",
        }
    )
    raise FlowAutomationError(
        "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu appeared"
    )


def visibility_component_raw(**overrides):
    values = {
        "is_element": True,
        "id": "mat-menu-panel-665",
        "tag": "div",
        "role": "menu",
        "connected": True,
        "bounding_box": {
            "x": 960.0,
            "y": 280.0,
            "width": 180.0,
            "height": 144.0,
        },
        "visibility": "visible",
        "display": "block",
        "opacity": "1",
        "pointer_events": "auto",
    }
    values.update(overrides)
    return values


def hover_handoff_target_raw(
    *,
    panel_id="mat-menu-panel-665",
    opacity="1",
    **overrides,
):
    visible = bool(float(opacity) > 0.01)
    target = visibility_component_raw(id=panel_id, opacity=opacity)
    values = {
        "parent_connected": True,
        "parent_role": "menuitem",
        "aria_haspopup": "menu",
        "aria_expanded": "true",
        "aria_controls": panel_id,
        "controls_id_valid": True,
        "controlled_target_count": 1,
        "controlled_target_connected_count": 1,
        "controlled_target_visible_count": int(visible),
        "controlled_role_menu_count": 1,
        "controlled_role_menu_connected_count": 1,
        "controlled_role_menu_visible_count": int(visible),
        "controlled_target_facts": target,
        "controlled_role_menu_facts": dict(target),
        "handoff_point": {"x": 1050.0, "y": 352.0},
        "handoff_hit_owned": True,
    }
    values.update(overrides)
    return values


def before_hover_handoff_raw(**overrides):
    panel = {
        "id": "mat-menu-panel-665",
        "tag": "div",
        "role": "menu",
        "aria_haspopup": None,
        "aria_expanded": None,
        "aria_controls": None,
        "connected": True,
        "visible": True,
        "hover": False,
        "bounding_box": {
            "x": 960.0,
            "y": 280.0,
            "width": 180.0,
            "height": 144.0,
        },
        "class_summary": ["mat-mdc-menu-panel", "download-submenu"],
    }
    values = {
        "parent": {
            "id": "download-parent",
            "tag": "button",
            "role": "menuitem",
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "connected": True,
            "visible": True,
            "hover": True,
            "bounding_box": {
                "x": 593.528,
                "y": 283.68,
                "width": 118.66,
                "height": 19.2,
            },
            "class_summary": ["mat-mdc-menu-item", "mat-mdc-menu-trigger"],
        },
        "current_aria_controls": "mat-menu-panel-665",
        "controls_id_valid": True,
        "controlled_target_count": 1,
        "controlled_target_connected_count": 1,
        "controlled_target_visible_count": 1,
        "controlled_target": panel,
        "controlled_role_menu_count": 1,
        "controlled_role_menu_connected_count": 1,
        "controlled_role_menu_visible_count": 1,
        "menu_candidates": [panel],
        "handoff_point": {"x": 976.0, "y": 330.0},
        "handoff_hit_owned": True,
        "handoff_hit_target": {
            "tag": "button",
            "role": "menuitem",
            "connected": True,
            "visible": True,
            "hover": False,
            "bounding_box": {
                "x": 960.0,
                "y": 280.0,
                "width": 180.0,
                "height": 48.0,
            },
            "class_summary": ["mat-mdc-menu-item"],
        },
        "visibility_observation": {
            "parent": {
                "id": "download-parent",
                "tag": "button",
                "role": "menuitem",
                "aria_haspopup": "menu",
                "aria_expanded": "true",
                "aria_controls": "mat-menu-panel-665",
                "connected": True,
                "visible": True,
                "hover": True,
                "bounding_box": {
                    "x": 593.528,
                    "y": 283.68,
                    "width": 118.66,
                    "height": 19.2,
                },
                "class_summary": ["mat-mdc-menu-item", "mat-mdc-menu-trigger"],
            },
            "controlled_target": visibility_component_raw(),
            "role_menu": visibility_component_raw(),
            "hit_coordinate": {"x": 1050.0, "y": 352.0},
            "hit_target": {
                "id": "one-k-item",
                "tag": "button",
                "role": "menuitem",
                "connected": True,
                "visible": True,
                "hover": False,
                "bounding_box": {
                    "x": 960.0,
                    "y": 328.0,
                    "width": 180.0,
                    "height": 48.0,
                },
                "class_summary": ["mat-mdc-menu-item"],
            },
            "hit_inside_controlled_target": True,
            "hit_inside_role_menu": True,
        },
    }
    values.update(overrides)
    return values


def after_hover_handoff_raw(**overrides):
    values = {
        "parent_connected": True,
        "parent_aria_expanded": "true",
        "current_aria_controls": "mat-menu-panel-665",
        "controlled_target_count": 1,
        "controlled_role_menu_count": 1,
        "panel": {
            "id": "mat-menu-panel-665",
            "tag": "div",
            "role": "menu",
            "aria_haspopup": None,
            "aria_expanded": None,
            "aria_controls": None,
            "connected": True,
            "visible": True,
            "hover": True,
            "bounding_box": {
                "x": 960.0,
                "y": 280.0,
                "width": 180.0,
                "height": 144.0,
            },
            "class_summary": ["mat-mdc-menu-panel", "download-submenu"],
        },
        "viewport": {"width": 1536, "height": 791},
        "hit_target": {
            "tag": "button",
            "role": "menuitem",
            "connected": True,
            "visible": True,
            "hover": True,
            "bounding_box": {
                "x": 960.0,
                "y": 280.0,
                "width": 180.0,
                "height": 48.0,
            },
            "class_summary": ["mat-mdc-menu-item"],
        },
        "hit_owned_by_panel": True,
        "one_k_exact_match_count": 1,
        "one_k_visible_match_count": 1,
        "one_k_enabled_match_count": 1,
        "one_k_visible_enabled_match_count": 1,
        "one_k_belongs_to_controlled_panel": True,
    }
    values.update(overrides)
    return values


def generation_submit_snapshot(**overrides):
    values = {
        "tag": "button",
        "role": "button",
        "aria_label": "Start generation",
        "disabled": False,
        "aria_disabled": None,
        "pointer_events": "auto",
        "connected": True,
        "visible": True,
        "bounding_box": {"x": 400.0, "y": 300.0, "width": 48.0, "height": 48.0},
        "bounding_box_samples": [
            {"x": 400.0, "y": 300.0, "width": 48.0, "height": 48.0},
            {"x": 400.2, "y": 300.1, "width": 48.0, "height": 48.0},
            {"x": 400.1, "y": 300.0, "width": 48.0, "height": 48.0},
        ],
        "center_top_belongs_to_control": True,
    }
    values.update(overrides)
    return _normalize_download_control_snapshot(values)


def candidate_tile_probe(**overrides):
    values = {
        "image_match_count": 1,
        "image_connected": True,
        "image_visible": True,
        "tile_found": True,
        "tile_connected": True,
        "tile_visible": True,
        "tile_owns_exact_image": True,
    }
    values.update(overrides)
    return values


def candidate_history_snapshot(**overrides):
    values = {
        "exact_match_count": 0,
        "exact_ready": False,
        "mounted_candidate_tile_count": 4,
        "surface_status": "ready",
        "surface_candidate_count": 4,
        "surface": {
            "kind": "tile_scroll_ancestor",
            "tag": "div",
            "role": "feed",
            "id": "results-scroll",
            "data_testid": "results-scroll",
            "token": "candidate-history-surface-1",
            "connected": True,
            "scroll_top": 500,
            "scroll_height": 1500,
            "client_height": 500,
            "max_scroll_top": 1000,
        },
        "requested_scroll_top": None,
        "moved": False,
    }
    values.update(overrides)
    return values


def more_options_hit_snapshot(probes, **overrides):
    snapshot = download_control_snapshot(
        role="button",
        visible_text="More options",
        **overrides,
    )
    snapshot["hit_test_probes"] = probes
    snapshot["safe_owned_point"] = next(
        (probe for probe in probes if probe.get("ownership")),
        None,
    )
    return snapshot


def material_snackbar_overlay_snapshot(**overrides):
    snapshot = more_options_hit_snapshot(
        [
            {
                "name": name,
                "x": x,
                "y": y,
                "ownership": None,
                "top": {
                    "tag": "div",
                    "class_summary": [
                        "mat-mdc-snack-bar-label",
                        "mdc-snackbar__label",
                        "label",
                    ],
                    "pointer_events": "auto",
                    "visibility": "visible",
                    "opacity": "1",
                    "connected": True,
                    "material_snackbar": True,
                },
            }
            for name, x, y in (
                ("center", 170.0, 212.0),
                ("left", 144.8, 212.0),
                ("right", 195.2, 212.0),
                ("up", 170.0, 207.68),
                ("down", 170.0, 216.32),
            )
        ],
        center_top_belongs_to_control=False,
    )
    snapshot["visible_material_snackbar_count"] = 1
    snapshot.update(overrides)
    return snapshot


def ready_more_options_snapshot(*, visible_snackbar_count=0):
    snapshot = more_options_hit_snapshot(
        [
            {
                "name": "center",
                "x": 170.0,
                "y": 212.0,
                "ownership": "self",
                "top": {
                    "tag": "button",
                    "pointer_events": "auto",
                    "visibility": "visible",
                    "opacity": "1",
                    "connected": True,
                },
            }
        ],
    )
    snapshot["visible_material_snackbar_count"] = visible_snackbar_count
    return snapshot


class FlowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = GenerationSettings(
            mode="image",
            aspect_ratio="16:9",
            variants=2,
            preferred_model_hint="Nano Banana 2",
        )
        self.download_candidate = CandidateRecord(
            variant=1,
            suggested_filename="0001__scene_0001_visual_001__v01.png",
            source_kind="rendered-image",
            asset_url="https://flow-content.google/image/00000000-0000-4000-8000-000000000003",
        )

    def test_semantic_start_generation_button_is_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        prompt = MagicMock()
        composer = MagicMock()
        button = MagicMock()
        prompt.locator.return_value = composer
        composer.get_by_role.return_value.all.return_value = [button]
        session.page = page
        with patch.object(
            session,
            "_read_active_composer_generation_resolution",
            return_value={
                "schema": "nano-banana-composer-generation/v1",
                "prompt_editor_count": 1,
                "global_generation_control_count": 2,
                "global_visible_generation_control_count": 2,
                "active_composer_count": 1,
                "active_composer_generation_control_count": 1,
                "structurally_owned_generation_control_count": 1,
                "actionable_owned_generation_control_count": 1,
                "ignored_generation_control_count": 1,
                "composer_depth": 1,
                "composer_token": 7,
                "composer_semantic": True,
                "scan_truncated": False,
                "structural_generation_control_ready": True,
                "generation_control_actionable": True,
                "ready": True,
                "failure_reason": "ready",
                "owned_control_label": "Start generation",
                "candidates": [],
                "remount_observed": False,
                "settle_elapsed_ms": 0,
                "settle_poll_count": 0,
            },
        ), patch.object(
            session, "_prompt_input", return_value=prompt
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={
                "connected": True,
                "visible": True,
                "enabled": True,
                "pointer_events": "auto",
                "bounding_box": {"width": 30, "height": 30},
            },
        ):
            resolved_prompt, buttons, facts = (
                session._current_generation_submit_buttons()
            )

        self.assertIs(resolved_prompt, prompt)
        self.assertEqual(buttons, [button])
        self.assertEqual(facts["visible_match_count"], 2)
        self.assertEqual(facts["ignored_generation_control_count"], 1)
        role, = composer.get_by_role.call_args.args
        pattern = composer.get_by_role.call_args.kwargs["name"]
        self.assertEqual(role, "button")
        self.assertIsNotNone(pattern.fullmatch("Start generation"))

    def test_generation_submit_mouse_activation_is_exactly_once_without_locator_click(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        button = MagicMock()
        session.page = page
        box = generation_submit_snapshot()["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=[box, box, box],
        ) as hit:
            result = session._activate_generation_submit_button(button, box)

        self.assertEqual(result["activation_count"], 1)
        self.assertEqual(result["input"], "mouse_move_down_up")
        self.assertEqual(hit.call_count, 3)
        page.mouse.move.assert_called_once_with(424.1, 324.0)
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        button.click.assert_not_called()

    def test_generation_submit_stable_box_is_accepted(self):
        box = _stable_generation_submit_box(generation_submit_snapshot())
        self.assertEqual(box["width"], 48.0)
        self.assertEqual(box["height"], 48.0)

    def test_generation_submit_unstable_box_fails_closed(self):
        snapshot = generation_submit_snapshot(
            bounding_box_samples=[
                {"x": 400.0, "y": 300.0, "width": 48.0, "height": 48.0},
                {"x": 408.0, "y": 300.0, "width": 48.0, "height": 48.0},
            ]
        )
        with self.assertRaisesRegex(FlowAutomationError, "GENERATION_SUBMIT_BBOX_UNSTABLE"):
            _stable_generation_submit_box(snapshot)

    def test_generation_submit_obscured_button_fails_closed(self):
        with self.assertRaisesRegex(FlowAutomationError, "GENERATION_SUBMIT_OVERLAID"):
            _stable_generation_submit_box(
                generation_submit_snapshot(center_top_belongs_to_control=False)
            )

    def test_generation_submit_disabled_button_fails_closed(self):
        with self.assertRaisesRegex(FlowAutomationError, "GENERATION_SUBMIT_DISABLED"):
            _stable_generation_submit_box(
                generation_submit_snapshot(aria_disabled="true")
            )

    def test_historical_usage_limit_cards_are_never_scanned_before_start(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        prompt_input = MagicMock()
        button = MagicMock()
        snapshot = generation_submit_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_begin_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_arm_current_generation_attempt_observation",
        ) as arm_observer, patch.object(
            session,
            "_stop_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(),
        ) as current_failures, patch.object(
            session,
            "_prompt_input",
            return_value=prompt_input,
        ), patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(
                prompt_input,
                {"structural_generation_control_ready": True},
            ),
        ), patch.object(
            session,
            "_prompt_control_text",
            return_value="safe prompt",
        ), patch.object(
            session,
            "_wait_for_generation_submit_button",
            return_value=(prompt_input, button, snapshot, box),
        ), patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=[box, box, box],
        ), patch.object(
            session,
            "_progress_signals",
            return_value=("Generating images",),
        ), patch.object(
            session,
            "discover_candidates",
            return_value=(),
        ) as discover:
            result = session.submit_prompt(
                "safe prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
            )

        self.assertEqual(result["acknowledgement"]["signal"], "new_generation_progress")
        self.assertFalse(hasattr(session, "_failure_card_snapshot"))
        self.assertEqual(current_failures.call_count, 1)
        arm_observer.assert_called_once_with()
        prompt_input.fill.assert_called_once_with("safe prompt")
        page.mouse.move.assert_called_once()
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        button.click.assert_not_called()
        discover.assert_not_called()

    def test_current_attempt_observer_defers_bounded_baseline_until_start_boundary(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        session._begin_current_generation_attempt_observation()

        script = page.evaluate.call_args.args[0]
        self.assertIn("new MutationObserver", script)
        self.assertIn("active: false", script)
        self.assertIn("if (!state.active) return", script)
        self.assertNotIn("document.querySelectorAll", script)
        self.assertIn("failureBaseline: new WeakMap()", script)
        self.assertIn("count > 10000", script)
        session._arm_current_generation_attempt_observation()
        self.assertIn("state.captureFailureBaseline()", page.evaluate.call_args.args[0])
        self.assertNotIn("structure", script)
        self.assertNotIn("sha256", script.casefold())

    def test_generation_observer_records_bounded_dom_activity_without_content(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page

        session._begin_current_generation_attempt_observation()
        begin_script = page.evaluate.call_args.args[0]
        session._arm_current_generation_attempt_observation()
        arm_script = page.evaluate.call_args.args[0]

        self.assertIn("Math.min(100000, state.mutationCount + records.length)", begin_script)
        self.assertIn("state.lastMutationAtMs = performance.now()", begin_script)
        self.assertIn("state.mutationCount = 0", arm_script)
        self.assertIn("if (state.addedRootCount < 10000)", begin_script)
        self.assertIn("if (state.progressRegionCount >= 200)", begin_script)
        self.assertIn("state.addedRoots = new WeakSet()", arm_script)
        self.assertIn("state.progressAncestors = new WeakSet()", arm_script)
        self.assertNotIn("record.target.innerText", begin_script)

    def test_progress_observation_only_marks_regions_after_attempt_is_armed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = ["Generating"]
        session.page = page
        session._begin_current_generation_attempt_observation()
        begin_script = page.evaluate.call_args.args[0]
        self.assertIn("if (!state.active || !element?.isConnected", begin_script)
        self.assertIn("state.noteProgress = (element)", begin_script)
        self.assertEqual(session._progress_signals(), ("Generating",))
        progress_script = page.evaluate.call_args.args[0]
        self.assertIn("?.noteProgress?.(element)", progress_script)

    def test_generation_failure_observation_is_count_only_and_read_only(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = {
            "renderer": {
                "visibility": "hidden",
                "has_focus": False,
                "observer_active": True,
                "ms_since_activation": 320000,
                "mutation_count": 7,
                "ms_since_last_mutation": 310000,
                "observed_failure_count": 0,
            },
            "page": {"prompt_control_count": 1, "generation_submit_control_count": 1},
        }
        page.is_closed.return_value = False
        session.page = page
        session._generation_result_observation = {
            "polls": 2, "last_candidate_count": 0,
            "max_progress_count": 1, "last_progress_count": 1,
        }
        candidates = (DiscoveredCandidate("rendered-image", candidate_id="private-id"),)
        with patch.object(session, "_progress_signals", return_value=("Generating",)), patch.object(
            session, "discover_candidates", return_value=candidates
        ) as discover:
            observation = session._generation_failure_observation(
                error=FlowAutomationError("No stable terminal image result"),
                submission={
                    "acknowledgement": {"signal": "new_generation_progress"},
                    "ack_elapsed_ms": 105,
                },
                baseline_fingerprints={"private-fingerprint"},
                baseline_progress=set(),
                baseline_candidate_ids={"private-baseline-id"},
                forbidden_candidate_ids=frozenset({"private-history-id"}),
            )

        self.assertEqual(observation["start_ack_signal"], "new_generation_progress")
        self.assertEqual(observation["current_progress_count"], 1)
        self.assertEqual(observation["current_candidate_count"], 1)
        self.assertEqual(observation["renderer"]["visibility"], "hidden")
        self.assertEqual(observation["page"]["target_attached"], True)
        self.assertEqual(
            observation["failure_category"], "result_timeout_progress_stalled_evidence"
        )
        self.assertEqual(observation["causality"], "diagnostic_evidence_only")
        self.assertNotIn("private-", json.dumps(observation))
        discover.assert_called_once()
        page.mouse.down.assert_not_called()

    def test_generation_failure_observation_survives_unavailable_renderer(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("renderer unavailable")
        page.is_closed.return_value = False
        session.page = page
        with patch.object(session, "_progress_signals", side_effect=RuntimeError()), patch.object(
            session, "discover_candidates", side_effect=RuntimeError()
        ):
            observation = session._generation_failure_observation(
                error=FlowAutomationError("No stable terminal image result"),
                submission=None,
                baseline_fingerprints=set(),
                baseline_progress=set(),
                baseline_candidate_ids=set(),
                forbidden_candidate_ids=frozenset(),
            )

        self.assertEqual(observation["phase"], "submission")
        self.assertEqual(observation["renderer"], {"capture": "unavailable"})
        self.assertIsNone(observation["current_progress_count"])
        self.assertIsNone(observation["current_candidate_count"])
        self.assertEqual(observation["failure_category"], "connectivity_or_page_failure")

    def _observe_generation_failure(self, error, renderer, result_wait, *, submission=True):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.is_closed.return_value = False
        page.evaluate.return_value = {
            "renderer": renderer,
            "page": {"prompt_control_count": 1, "generation_submit_control_count": 1},
        }
        session.page = page
        session._generation_result_observation = result_wait
        with patch.object(session, "_progress_signals", return_value=()), patch.object(
            session, "discover_candidates", return_value=()
        ):
            return session._generation_failure_observation(
                error=error,
                submission=(
                    {"acknowledgement": {"signal": "submit_button_disabled"}, "ack_elapsed_ms": 196}
                    if submission else None
                ),
                baseline_fingerprints=set(),
                baseline_progress=set(),
                baseline_candidate_ids=set(),
                forbidden_candidate_ids=frozenset(),
            )

    def test_explicit_flow_failure_and_start_ack_failure_have_distinct_categories(self):
        renderer = {
            "visibility": "visible", "has_focus": True, "observer_active": True,
            "mutation_count": 3, "ms_since_last_mutation": 20,
            "observed_failure_count": 1,
        }
        explicit = self._observe_generation_failure(
            FlowAutomationError("Flow reported a terminal failure for the current generation"),
            renderer,
            None,
        )
        unacknowledged = self._observe_generation_failure(
            FlowAutomationError("GENERATION_START_UNCONFIRMED"), renderer, None, submission=False
        )
        self.assertEqual(explicit["failure_category"], "explicit_flow_failure")
        self.assertEqual(explicit["causality"], "explicit_flow_signal")
        self.assertEqual(unacknowledged["failure_category"], "start_not_acknowledged")

    def test_timeout_distinguishes_active_renderer_from_stall_evidence(self):
        result_wait = {
            "polls": 40, "max_progress_count": 0, "last_progress_count": 0,
            "max_candidate_count": 0, "last_candidate_count": 0, "elapsed_ms": 80000,
        }
        active = self._observe_generation_failure(
            FlowAutomationError("No stable terminal image result"),
            {
                "visibility": "hidden", "has_focus": False, "observer_active": True,
                "mutation_count": 400, "ms_since_last_mutation": 50,
                "observed_failure_count": 0,
            },
            result_wait,
        )
        stalled = self._observe_generation_failure(
            FlowAutomationError("No stable terminal image result"),
            {
                "visibility": "visible", "has_focus": True, "observer_active": True,
                "mutation_count": 4, "ms_since_last_mutation": 70000,
                "observed_failure_count": 0,
            },
            result_wait,
        )
        self.assertEqual(active["failure_category"], "result_timeout_active_renderer_no_result")
        self.assertEqual(stalled["failure_category"], "result_timeout_renderer_stalled_evidence")
        self.assertEqual(stalled["causality"], "diagnostic_evidence_only")

    def test_failure_diagnostic_records_safe_live_cdp_facts_without_raw_command_line(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.is_closed.return_value = False
        page.evaluate.return_value = {
            "renderer": {
                "visibility": "hidden", "has_focus": False, "observer_active": True,
                "mutation_count": 1, "ms_since_last_mutation": 10,
                "observed_failure_count": 0,
            },
            "page": {"prompt_control_count": 1, "generation_submit_control_count": 1},
        }
        session.page = page
        cdp = MagicMock()
        cdp.send.side_effect = lambda method, params=None: {
            "Runtime.evaluate": {"result": {"value": 1}},
            "Browser.getWindowForTarget": {"bounds": {"windowState": "minimized"}},
            "Browser.getVersion": {"product": "Chrome/140.0", "protocolVersion": "1.3"},
            "Browser.getBrowserCommandLine": {
                "arguments": [
                    "chrome.exe", "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-features=CalculateNativeWinOcclusion",
                    "--user-data-dir=private-path",
                ]
            },
        }[method]
        session._page_cdp_session = cdp
        with patch.object(session, "_progress_signals", return_value=()), patch.object(
            session, "discover_candidates", return_value=()
        ):
            observation = session._generation_failure_observation(
                error=FlowAutomationError("No stable terminal image result"),
                submission={"acknowledgement": {"signal": "new_generation_progress"}},
                baseline_fingerprints=set(), baseline_progress=set(),
                baseline_candidate_ids=set(), forbidden_candidate_ids=frozenset(),
            )
        self.assertEqual(observation["page"]["window_state"], "minimized")
        self.assertTrue(all(observation["browser"]["startup_flag_presence"].values()))
        serialized = json.dumps(observation)
        self.assertNotIn("private-path", serialized)
        self.assertNotIn("user-data-dir", serialized)
        self.assertLess(len(serialized), 5000)

    def test_generate_one_has_no_usage_limit_preflight_scan(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        source = inspect.getsource(FlowSession.generate_one)
        self.assertNotIn("baseline_failures", source)
        self.assertNotIn("failure_card", source)
        self.assertFalse(hasattr(session, "_failure_signals"))
        self.assertFalse(hasattr(session, "_failure_card_snapshot"))

    def test_current_post_start_usage_limit_stops_without_second_start(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        prompt_input = MagicMock()
        button = MagicMock()
        snapshot = generation_submit_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_begin_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_arm_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_stop_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=("Failed You have reached your usage limit.",),
        ), patch.object(
            session,
            "_prompt_input",
            return_value=prompt_input,
        ), patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(
                prompt_input,
                {"structural_generation_control_ready": True},
            ),
        ), patch.object(
            session,
            "_prompt_control_text",
            return_value="safe prompt",
        ), patch.object(
            session,
            "_wait_for_generation_submit_button",
            return_value=(prompt_input, button, snapshot, box),
        ), patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=[box, box, box],
        ), patch.object(
            session,
            "discover_candidates",
        ) as discover, self.assertRaises(FlowUsageLimitReached) as raised:
            session.submit_prompt(
                "safe prompt",
                baseline_fingerprints=set(),
                baseline_progress=set(),
            )

        self.assertTrue(raised.exception.attempt_activated)
        page.mouse.move.assert_called_once()
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        button.click.assert_not_called()
        discover.assert_not_called()
        self.assertFalse(
            any("retry" in str(call).casefold() for call in page.mock_calls)
        )

    def test_unrelated_historical_usage_limit_after_start_is_ignored(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = []
        session.page = page
        page.locator.return_value = MagicMock(
            text_content="Historical: You've reached your usage limit."
        )

        self.assertEqual(session._current_generation_attempt_failures(), ())
        page.locator.assert_not_called()

    def test_current_usage_limit_during_result_wait_stops_before_candidate_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=("Failed You've reached your usage limit.",),
        ), patch.object(
            session,
            "discover_candidates",
        ) as discover, self.assertRaises(FlowUsageLimitReached):
            session.wait_for_result(set(), 1)

        discover.assert_not_called()

    def test_current_non_limit_generation_failure_remains_terminal(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=("Generation failed for the current request.",),
        ), patch.object(
            session,
            "discover_candidates",
        ) as discover, self.assertRaisesRegex(
            FlowAutomationError,
            "current generation",
        ):
            session.wait_for_result(set(), 1)

        discover.assert_not_called()

    def test_successful_current_generation_uses_normal_candidate_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        candidate = DiscoveredCandidate(
            source_kind="rendered-image",
            candidate_id="current-v01",
        )
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(),
        ), patch.object(
            session,
            "_progress_signals",
            return_value=(),
        ), patch.object(
            session,
            "discover_candidates",
            return_value=(candidate,),
        ) as discover, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.0, 0.0],
        ):
            result = session.wait_for_result(set(), 1, expected_count=1)

        self.assertEqual(result, (candidate,))
        self.assertEqual(discover.call_count, 3)

    def test_result_wait_observation_tracks_first_and_last_progress_and_candidate_polls(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        candidate = DiscoveredCandidate("rendered-image", candidate_id="new-v01")
        with patch.object(session, "_current_generation_attempt_failures", return_value=()), patch.object(
            session, "_progress_signals", side_effect=[("Generating",), (), (), ()]
        ), patch.object(
            session, "discover_candidates", side_effect=[(), (candidate,), (candidate,), (candidate,)]
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0] * 5
        ):
            result = session.wait_for_result(set(), 1, expected_count=1)

        self.assertEqual(result, (candidate,))
        self.assertEqual(
            session._generation_result_observation,
            {
                "polls": 4,
                "first_progress_poll": 1,
                "last_progress_poll": 1,
                "first_candidate_poll": 2,
                "last_candidate_poll": 4,
                "max_candidate_count": 1,
                "last_candidate_count": 1,
                "max_progress_count": 1,
                "last_progress_count": 0,
                "progress_change_count": 2,
                "last_progress_change_poll": 2,
                "elapsed_ms": 0,
                "window_watchdog_checks": 0,
                "window_recovery_attempted": False,
                "window_recovery_count": 0,
                "window_recovery_events": [],
            },
        )

    def test_hidden_unfocused_renderer_does_not_change_success_gate(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = {"visibility": "hidden", "has_focus": False}
        session.page = page
        candidate = DiscoveredCandidate("rendered-image", candidate_id="current-v01")
        with patch.object(session, "_current_generation_attempt_failures", return_value=()), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(session, "discover_candidates", return_value=(candidate,)), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0] * 4
        ):
            result = session.wait_for_result(set(), 1, expected_count=1)
        self.assertEqual(result, (candidate,))
        page.mouse.down.assert_not_called()

    def test_generation_start_accepts_new_progress_signal(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(),
        ), patch.object(
            session,
            "_progress_signals",
            return_value=("Generating images",),
        ):
            result = session._wait_for_generation_start(
                prompt_input=MagicMock(),
                intended_prompt="safe prompt",
                baseline_fingerprints={"old"},
                baseline_progress=set(),
                timeout_ms=500,
            )
        self.assertEqual(result["signal"], "new_generation_progress")

    def test_ambiguous_generation_start_never_causes_second_activation(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        prompt_input = MagicMock()
        button = MagicMock()
        snapshot = generation_submit_snapshot()
        box = snapshot["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_prompt_input",
            return_value=prompt_input,
        ), patch.object(
            session,
            "_wait_for_owned_generation_control_structure",
            return_value=(
                prompt_input,
                {"structural_generation_control_ready": True},
            ),
        ), patch.object(
            session,
            "_prompt_control_text",
            return_value="safe prompt",
        ), patch.object(
            session,
            "_wait_for_generation_submit_button",
            return_value=(prompt_input, button, snapshot, box),
        ), patch.object(
            session,
            "_current_generation_submit_hit",
            side_effect=[box, box, box],
        ), patch.object(
            session,
            "_begin_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_arm_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_stop_current_generation_attempt_observation",
        ), patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(),
        ), patch.object(
            session,
            "_progress_signals",
            return_value=(),
        ), patch.object(
            session,
            "discover_candidates",
            return_value=(),
        ), patch.object(
            session,
            "_current_generation_submit_buttons",
            return_value=(
                prompt_input,
                [button],
                {
                    "visible_match_count": 1,
                    "active_composer_generation_control_count": 1,
                    "actionable_owned_generation_control_count": 1,
                },
            ),
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 6.0],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "GENERATION_START_UNCONFIRMED",
        ):
            session.submit_prompt(
                "safe prompt",
                baseline_fingerprints={"old"},
                baseline_progress=set(),
            )

        prompt_input.fill.assert_called_once_with("safe prompt")
        page.mouse.move.assert_called_once()
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        button.click.assert_not_called()

    def test_submit_prompt_rejects_existing_generation_activity_before_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(
            session,
            "_begin_current_generation_attempt_observation",
        ) as begin_observer, patch.object(
            session,
            "_prompt_input",
        ) as prompt_input, self.assertRaisesRegex(
            FlowAutomationError,
            "GENERATION_ALREADY_IN_PROGRESS",
        ):
            session.submit_prompt(
                "safe prompt",
                baseline_fingerprints=set(),
                baseline_progress={"Generating"},
            )
        prompt_input.assert_not_called()
        begin_observer.assert_not_called()

    def test_persisted_flow_candidate_url_maps_to_data_media_id(self):
        self.assertEqual(
            FlowSession._candidate_media_id(self.download_candidate),
            "00000000-0000-4000-8000-000000000003",
        )
        conflicting = CandidateRecord(
            variant=1,
            suggested_filename=self.download_candidate.suggested_filename,
            source_kind="rendered-image",
            candidate_id="different-id",
            asset_url=self.download_candidate.asset_url,
        )
        with self.assertRaisesRegex(FlowAutomationError, "conflicting persisted identities"):
            FlowSession._candidate_media_id(conflicting)

    def test_data_media_id_is_identity_when_rendered_src_is_transient_asb(self):
        candidate_id = "00000000-0000-4000-8000-000000000003"
        candidate = CandidateRecord(
            variant=1,
            suggested_filename=self.download_candidate.suggested_filename,
            source_kind="rendered-image",
            candidate_id=candidate_id,
            asset_url="https://flow.google.com/asb/transient-rendered-source",
        )
        self.assertEqual(FlowSession._candidate_media_id(candidate), candidate_id)

        session = FlowSession(PROJECT_URL, Path("profile"))
        with patch.object(
            session,
            "_asset_snapshot",
            return_value=[
                {
                    "kind": "rendered-image",
                    "src": candidate.asset_url,
                    "mediaId": candidate_id,
                    "name": "candidate",
                    "width": 1376,
                    "height": 768,
                    "fingerprint": "fingerprint-one",
                }
            ],
        ):
            discovered = session.discover_candidates()
        self.assertEqual(discovered[0].candidate_id, candidate_id)
        self.assertIsNone(discovered[0].asset_url)

    @staticmethod
    def _candidate_asset(candidate_id, fingerprint, src=None):
        return {
            "kind": "rendered-image",
            "src": src or f"https://flow.google.com/asb/{fingerprint}",
            "mediaId": candidate_id,
            "name": "generated candidate",
            "width": 1376,
            "height": 768,
            "path": f"main:0/flow-image-tile:{fingerprint}",
            "fingerprint": fingerprint,
        }

    def test_pre_start_candidate_uuid_is_excluded_even_with_changed_fingerprint(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        prior_id = "prior-candidate-uuid"
        remounted = self._candidate_asset(
            prior_id,
            "changed-fingerprint",
            src="https://flow.google.com/asb/changed-source",
        )
        with patch.object(session, "_asset_snapshot", return_value=[remounted]):
            discovered = session.discover_candidates(
                {"old-fingerprint"},
                baseline_candidate_ids={prior_id},
            )
        self.assertEqual(discovered, ())

    def test_x1_remounted_and_owned_history_with_duplicate_nodes_still_accepts_only_new_uuid(self):
        baseline_old = self._candidate_asset("baseline-old-uuid", "remounted-old")
        owned_old = self._candidate_asset("run-owned-old-uuid", "history-old")
        duplicate_old = self._candidate_asset("run-owned-old-uuid", "history-duplicate")
        new = self._candidate_asset("current-new-uuid", "new-current")
        for assets in (
            [baseline_old, owned_old, duplicate_old, new],
            [new, duplicate_old, owned_old, baseline_old],
        ):
            session = FlowSession(PROJECT_URL, Path("profile"))
            session.page = MagicMock()
            with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
                session, "_current_generation_attempt_failures", return_value=()
            ), patch.object(session, "_progress_signals", return_value=()), patch(
                "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0] * 4
            ):
                result = session.wait_for_result(
                    {"original-baseline-fingerprint"},
                    1,
                    expected_count=1,
                    baseline_candidate_ids={"baseline-old-uuid"},
                    forbidden_candidate_ids={"run-owned-old-uuid"},
                )
            self.assertEqual([candidate.candidate_id for candidate in result], ["current-new-uuid"])
            session.page.mouse.down.assert_not_called()

    def test_x1_eight_baseline_uuids_and_one_new_uuid_still_succeed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        baseline = {f"baseline-uuid-{index}" for index in range(8)}
        assets = [
            self._candidate_asset(candidate_id, f"remounted-{index}")
            for index, candidate_id in enumerate(sorted(baseline))
        ] + [self._candidate_asset("current-new-uuid", "new-result")]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0] * 4
        ):
            result = session.wait_for_result(
                set(), 1, expected_count=1, baseline_candidate_ids=baseline,
            )
        self.assertEqual([candidate.candidate_id for candidate in result], ["current-new-uuid"])

    def test_x1_seven_post_start_unknown_uuids_plus_result_remain_ambiguous(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        baseline = {f"baseline-uuid-{index}" for index in range(8)}
        assets = [
            self._candidate_asset(candidate_id, f"remounted-{index}")
            for index, candidate_id in enumerate(sorted(baseline))
        ] + [
            self._candidate_asset(f"unknown-uuid-{index}", f"loaded-after-start-{index}")
            for index in range(7)
        ] + [self._candidate_asset("current-new-uuid", "new-result")]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaisesRegex(
            FlowAutomationError, "expected exactly 1 new candidate UUIDs, observed 8"
        ):
            session.wait_for_result(
                set(), 1, expected_count=1, baseline_candidate_ids=baseline,
            )
        session.page.mouse.down.assert_not_called()

    def test_offscreen_new_candidate_is_not_discarded_by_diagnostic_categories(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        candidate = self._candidate_asset("current-offscreen-uuid", "new-result")
        candidate["offscreen"] = True
        with patch.object(session, "_asset_snapshot", return_value=[candidate]):
            discovered = session.discover_candidates(set())
        self.assertEqual([item.candidate_id for item in discovered], ["current-offscreen-uuid"])

    def test_x1_two_unowned_uuids_still_fail_without_selecting_one(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        assets = [
            self._candidate_asset("new-candidate-one", "new-one"),
            self._candidate_asset("new-candidate-two", "new-two"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaisesRegex(FlowAutomationError, "expected exactly 1 new candidate UUIDs, observed 2"):
            session.wait_for_result(set(), 1, expected_count=1)
        session.page.mouse.down.assert_not_called()

    def test_x1_zero_new_uuids_still_times_out_without_claiming_history(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        assets = [self._candidate_asset("run-owned-old-uuid", "remounted")]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0, 2.0]
        ), self.assertRaisesRegex(FlowAutomationError, "No stable terminal image result"):
            session.wait_for_result(
                set(), 1, expected_count=1,
                forbidden_candidate_ids={"run-owned-old-uuid"},
            )

    def test_correlation_failure_observation_counts_seven_without_persisting_ids(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = {
            "valid_uuid_nodes": 10,
            "production_visible_gate_nodes": 10,
            "duplicate_uuid_count": 1,
            "truncated": False,
        }
        session.page = page
        assets = [
            self._candidate_asset("baseline-old-uuid", "remounted"),
            self._candidate_asset("run-owned-old-uuid", "owned"),
            *[
                self._candidate_asset(f"unowned-uuid-{index}", f"new-{index}")
                for index in range(7)
            ],
            self._candidate_asset("unowned-uuid-0", "duplicate-node"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets):
            observed = session._candidate_correlation_observation(
                baseline_fingerprints={"old-fingerprint"},
                baseline_candidate_ids={"baseline-old-uuid"},
                forbidden_candidate_ids={"run-owned-old-uuid"},
                observed_count=7,
                expected_count=1,
            )
        self.assertEqual(observed["pre_start_candidate_uuid_count"], 1)
        self.assertEqual(observed["run_owned_uuid_count"], 1)
        self.assertEqual(observed["post_start_visible_candidate_uuid_count"], 9)
        self.assertEqual(observed["post_start_baseline_overlap_uuid_count"], 1)
        self.assertEqual(observed["post_start_owned_overlap_uuid_count"], 1)
        self.assertEqual(observed["post_start_claimable_uuid_count"], 7)
        self.assertEqual(observed["visible_duplicate_uuid_count"], 1)
        self.assertEqual(observed["visible_duplicate_extra_node_count"], 1)
        self.assertEqual(observed["ownership_scope"], "current_run_only")
        self.assertNotIn("uuid-", json.dumps(observed))
        script = page.evaluate.call_args.args[0]
        self.assertIn("const limit = 5000", script)
        self.assertIn("hidden_or_zero_rect_nodes", script)
        self.assertIn("offscreen_visible_gate_nodes", script)
        self.assertIn("center_hit_test_owned_nodes", script)
        self.assertNotIn("element.src", script)
        self.assertIn("progress_ancestor_distance", script)
        self.assertIn("post_start_subtree_nodes", script)
        self.assertIn("ancestry_levels", script)
        self.assertIn("tile_parent_groups", script)
        self.assertIn("outer_container_groups", script)
        self.assertIn("const limit = 5000", script)
        self.assertNotIn("return id", script)
        self.assertEqual(
            page.evaluate.call_args.args[1],
            {"baselineIds": ["baseline-old-uuid"], "ownedIds": ["run-owned-old-uuid"]},
        )
        page.mouse.down.assert_not_called()

    def test_correlation_structure_is_failure_only_and_never_selects_uuid(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        assets = [
            self._candidate_asset("unknown-history-uuid", "late-history"),
            self._candidate_asset("current-result-uuid", "new-result"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch.object(
            session, "_candidate_correlation_observation", return_value={"raw_dom": {"structure": {}}}
        ) as diagnostic, patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaisesRegex(
            FlowAutomationError, "expected exactly 1 new candidate UUIDs, observed 2"
        ):
            session.wait_for_result(set(), 1, expected_count=1)
        diagnostic.assert_called_once()

    def test_x1_overcount_records_diagnostic_but_keeps_same_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        candidates = tuple(
            DiscoveredCandidate("rendered-image", candidate_id=f"unknown-uuid-{index}")
            for index in range(7)
        )
        with patch.object(session, "_current_generation_attempt_failures", return_value=()), patch.object(
            session, "discover_candidates", return_value=candidates
        ), patch.object(session, "_progress_signals", return_value=()), patch.object(
            session, "_candidate_correlation_observation", return_value={"post_start_claimable_uuid_count": 7}
        ) as diagnostic, patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0]
        ), self.assertRaisesRegex(FlowAutomationError, "expected exactly 1 new candidate UUIDs, observed 7"):
            session.wait_for_result(
                {"baseline-fingerprint"}, 1, expected_count=1,
                baseline_candidate_ids={"baseline-old-uuid"},
                forbidden_candidate_ids={"run-owned-old-uuid"},
            )
        diagnostic.assert_called_once()
        self.assertEqual(
            session._candidate_correlation_failure_observation,
            {"post_start_claimable_uuid_count": 7},
        )
        session.page.mouse.down.assert_not_called()

    def test_persisted_order_two_and_three_uuids_are_rejected_after_remount(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        order_two = "persisted-order-two-v02"
        order_three = "persisted-order-three-v02"
        assets = [
            self._candidate_asset(order_two, "remount-two"),
            self._candidate_asset(order_three, "remount-three"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets):
            discovered = session.discover_candidates(
                set(),
                forbidden_candidate_ids={order_two, order_three},
            )
        self.assertEqual(discovered, ())

    def test_exactly_two_genuinely_new_candidate_uuids_are_accepted(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        assets = [
            self._candidate_asset("current-candidate-v01", "new-one"),
            self._candidate_asset("current-candidate-v02", "new-two"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets):
            discovered = session.discover_candidates(
                {"baseline"},
                baseline_candidate_ids={"visible-before-start"},
                forbidden_candidate_ids={"persisted-history"},
            )
        self.assertEqual(
            [candidate.candidate_id for candidate in discovered],
            ["current-candidate-v01", "current-candidate-v02"],
        )

    def test_one_new_plus_one_historical_candidate_never_satisfies_x2(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        assets = [
            self._candidate_asset("current-candidate-v01", "new-one"),
            self._candidate_asset("persisted-order-two-v02", "remounted-old"),
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(session, "_progress_signals", return_value=()), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=[0.0, 0.0, 2.0]
        ), self.assertRaisesRegex(FlowAutomationError, "No stable terminal"):
            session.wait_for_result(
                set(),
                1,
                expected_count=2,
                forbidden_candidate_ids={"persisted-order-two-v02"},
            )

    def test_more_than_two_new_candidate_uuids_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        assets = [
            self._candidate_asset(f"current-candidate-v0{index}", f"new-{index}")
            for index in (1, 2, 3)
        ]
        with patch.object(session, "_asset_snapshot", return_value=assets), patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), self.assertRaisesRegex(FlowAutomationError, "CANDIDATE_CORRELATION_UNPROVEN"):
            session.wait_for_result(set(), 1, expected_count=2)

    def test_download_context_rejects_different_flow_project(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        inspection = flow_inspection(
            current_url="https://flow.google.com/project/different-project"
        )
        with patch.object(
            session,
            "_assert_editor_available",
            return_value=inspection,
        ), self.assertRaisesRegex(FlowAutomationError, "does not match the configured project"):
            session._assert_download_context()

    def test_exact_candidate_tile_selection(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        image_matches = MagicMock()
        image_matches.all.return_value = [image]
        tile_matches = MagicMock()
        tile_matches.all.return_value = [tile]
        page.locator.side_effect = [image_matches, tile_matches]
        session.page = page
        with patch.object(
            session,
            "_candidate_image_tile_probe",
            return_value=candidate_tile_probe(),
        ):
            candidate_id, selected_image, selected_tile = session._exact_candidate_tile(
                self.download_candidate
            )
        self.assertEqual(candidate_id, "00000000-0000-4000-8000-000000000003")
        self.assertIs(selected_image, image)
        self.assertIs(selected_tile, tile)
        self.assertEqual(
            [call.args[0] for call in page.locator.call_args_list],
            [
                'img[data-media-id="00000000-0000-4000-8000-000000000003"]',
                'flow-image-tile:has(img[data-media-id="00000000-0000-4000-8000-000000000003"])',
            ],
        )
        image.locator.assert_not_called()

    def test_candidate_image_tile_probe_uses_exact_uuid_in_one_page_dom_read(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = candidate_tile_probe()
        session.page = page

        facts = session._candidate_image_tile_probe(
            "00000000-0000-4000-8000-000000000004"
        )

        self.assertEqual(facts, candidate_tile_probe())
        script, candidate_id = page.evaluate.call_args.args
        self.assertEqual(candidate_id, "00000000-0000-4000-8000-000000000004")
        self.assertIn('querySelectorAll("img[data-media-id]")', script)
        self.assertIn('getAttribute("data-media-id") === candidateId', script)
        self.assertIn('closest("flow-image-tile")', script)
        self.assertNotIn("xpath=ancestor", script)

    def test_missing_and_duplicate_candidate_matches_fail_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        for match_count in (0, 2):
            with self.subTest(count=match_count), patch.object(
                session,
                "_candidate_image_tile_probe",
                return_value=candidate_tile_probe(
                    image_match_count=match_count,
                    image_connected=False,
                    image_visible=False,
                    tile_found=False,
                    tile_connected=False,
                    tile_visible=False,
                    tile_owns_exact_image=False,
                ),
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0.0, 0.2],
            ), self.assertRaisesRegex(
                FlowAutomationError,
                f"image_match_count={match_count}",
            ):
                session._exact_candidate_image_tile(
                    "candidate-id",
                    timeout_ms=100,
                )

    def test_candidate_image_temporarily_detached_then_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        image_matches = MagicMock()
        image_matches.all.return_value = [image]
        tile_matches = MagicMock()
        tile_matches.all.return_value = [tile]
        page.locator.side_effect = [image_matches, tile_matches]
        session.page = page
        with patch.object(
            session,
            "_candidate_image_tile_probe",
            side_effect=[
                candidate_tile_probe(
                    image_match_count=0,
                    image_connected=False,
                    image_visible=False,
                    tile_found=False,
                    tile_connected=False,
                    tile_visible=False,
                    tile_owns_exact_image=False,
                ),
                candidate_tile_probe(),
            ],
        ) as probe, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ):
            selected_image, selected_tile = session._exact_candidate_image_tile(
                "candidate-id",
                timeout_ms=500,
            )

        self.assertIs(selected_image, image)
        self.assertIs(selected_tile, tile)
        self.assertEqual(probe.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_short_candidate_probe_timeout_does_not_fail_whole_reacquisition(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image_matches = MagicMock()
        image_matches.all.return_value = [MagicMock()]
        tile_matches = MagicMock()
        tile_matches.all.return_value = [MagicMock()]
        page.locator.side_effect = [image_matches, tile_matches]
        session.page = page
        with patch.object(
            session,
            "_candidate_image_tile_probe",
            side_effect=[TimeoutError("individual probe timed out"), candidate_tile_probe()],
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ):
            image, tile = session._exact_candidate_image_tile(
                "candidate-id",
                timeout_ms=500,
            )

        self.assertIsNotNone(image)
        self.assertIsNotNone(tile)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_candidate_reacquisition_exhaustion_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        with patch.object(
            session,
            "_candidate_image_tile_probe",
            return_value=candidate_tile_probe(
                tile_found=False,
                tile_connected=False,
                tile_visible=False,
                tile_owns_exact_image=False,
            ),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.2],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_REACQUISITION_TIMEOUT.*tile_found=False",
        ):
            session._exact_candidate_image_tile(
                "candidate-id",
                timeout_ms=100,
            )
        page.locator.assert_not_called()

    def test_rendered_candidate_does_not_search_history(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        image_matches = MagicMock()
        image_matches.all.return_value = [image]
        tile_matches = MagicMock()
        tile_matches.all.return_value = [tile]
        page.locator.side_effect = [image_matches, tile_matches]
        session.page = page

        with patch.object(
            session, "_candidate_image_tile_probe", return_value=candidate_tile_probe()
        ), patch.object(
            session, "_reveal_virtualized_candidate_history"
        ) as history_search:
            selected_image, selected_tile = session._exact_candidate_image_tile(
                "candidate-id",
                reveal_virtualized_history=True,
            )

        self.assertIs(selected_image, image)
        self.assertIs(selected_tile, tile)
        history_search.assert_not_called()

    def test_zero_match_history_search_reacquires_same_exact_uuid(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        image_matches = MagicMock()
        image_matches.all.return_value = [image]
        tile_matches = MagicMock()
        tile_matches.all.return_value = [tile]
        page.locator.side_effect = [image_matches, tile_matches]
        session.page = page
        missing = candidate_tile_probe(
            image_match_count=0,
            image_connected=False,
            image_visible=False,
            tile_found=False,
            tile_connected=False,
            tile_visible=False,
            tile_owns_exact_image=False,
        )

        with patch.object(
            session,
            "_candidate_image_tile_probe",
            side_effect=[missing, candidate_tile_probe()],
        ) as exact_probe, patch.object(
            session,
            "_reveal_virtualized_candidate_history",
            return_value={"outcome": "exact_candidate_revealed"},
        ) as history_search, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.2],
        ):
            selected_image, selected_tile = session._exact_candidate_image_tile(
                "same-persisted-candidate-id",
                timeout_ms=100,
                reveal_virtualized_history=True,
            )

        self.assertIs(selected_image, image)
        self.assertIs(selected_tile, tile)
        history_search.assert_called_once_with("same-persisted-candidate-id")
        self.assertEqual(
            [call.args[0] for call in exact_probe.call_args_list],
            ["same-persisted-candidate-id", "same-persisted-candidate-id"],
        )
        self.assertIn(
            'same-persisted-candidate-id',
            page.locator.call_args_list[0].args[0],
        )

    def test_history_search_exhaustion_preserves_reacquisition_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        missing = candidate_tile_probe(
            image_match_count=0,
            image_connected=False,
            image_visible=False,
            tile_found=False,
            tile_connected=False,
            tile_visible=False,
            tile_owns_exact_image=False,
        )
        diagnostics = {
            "candidate_id": "same-persisted-candidate-id",
            "reveal_steps": 3,
            "outcome": "search_exhausted",
        }

        with patch.object(
            session, "_candidate_image_tile_probe", return_value=missing
        ), patch.object(
            session,
            "_reveal_virtualized_candidate_history",
            return_value=diagnostics,
        ) as history_search, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.2],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_REACQUISITION_TIMEOUT.*history_search=.*search_exhausted",
        ):
            session._exact_candidate_image_tile(
                "same-persisted-candidate-id",
                timeout_ms=100,
                reveal_virtualized_history=True,
            )

        history_search.assert_called_once_with("same-persisted-candidate-id")
        page.locator.assert_not_called()

    def test_duplicate_exact_uuid_never_enters_history_search(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        duplicate = candidate_tile_probe(
            image_match_count=2,
            image_connected=False,
            image_visible=False,
            tile_found=False,
            tile_connected=False,
            tile_visible=False,
            tile_owns_exact_image=False,
        )

        with patch.object(
            session, "_candidate_image_tile_probe", return_value=duplicate
        ), patch.object(
            session, "_reveal_virtualized_candidate_history"
        ) as history_search, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.2],
        ), self.assertRaisesRegex(
            FlowAutomationError, "image_match_count=2"
        ):
            session._exact_candidate_image_tile(
                "same-persisted-candidate-id",
                timeout_ms=100,
                reveal_virtualized_history=True,
            )

        history_search.assert_not_called()
        page.locator.assert_not_called()

    def test_virtualized_history_reveals_after_multiple_fresh_steps_and_remount(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        moved_one = candidate_history_snapshot(
            surface={
                **candidate_history_snapshot()["surface"],
                "scroll_top": 333,
            },
            moved=True,
        )
        settled_one = candidate_history_snapshot(
            surface={
                **candidate_history_snapshot()["surface"],
                "scroll_top": 333,
            }
        )
        moved_two = candidate_history_snapshot(
            surface={
                **candidate_history_snapshot()["surface"],
                "token": "candidate-history-surface-2",
                "scroll_top": 667,
            },
            moved=True,
        )
        found = candidate_history_snapshot(
            exact_match_count=1,
            exact_ready=True,
            surface={
                **candidate_history_snapshot()["surface"],
                "token": "candidate-history-surface-2",
                "scroll_top": 667,
            },
        )
        with patch.object(
            session,
            "ensure_production_window_usable",
            return_value={
                "after": {
                    "window_state": "maximized",
                    "viewport": {"width": 1920, "height": 1080},
                }
            },
        ) as ensure_window, patch.object(
            session,
            "_candidate_history_scroll_snapshot",
            side_effect=[
                candidate_history_snapshot(),
                moved_one,
                settled_one,
                moved_two,
                found,
            ],
        ) as snapshots, patch(
            "nano_batch.adapters.flow.time.monotonic",
            return_value=0.0,
        ):
            diagnostics = session._reveal_virtualized_candidate_history(
                "same-persisted-candidate-id",
                max_steps=4,
                timeout_seconds=1.0,
                settle_ms=25,
            )

        self.assertEqual(diagnostics["outcome"], "exact_candidate_revealed")
        self.assertEqual(diagnostics["reveal_steps"], 2)
        self.assertTrue(diagnostics["result_container_remounted"])
        self.assertEqual(
            [stage["exact_match_count"] for stage in diagnostics["stages"]],
            [0, 0, 1],
        )
        self.assertEqual(snapshots.call_count, 5)
        self.assertEqual(
            sorted(
                round(call.kwargs["target_scroll_top"])
                for call in snapshots.call_args_list
                if "target_scroll_top" in call.kwargs
            ),
            [333, 667],
        )
        ensure_window.assert_called_once_with(
            reason="startup_nonmaximized",
            candidate_id="same-persisted-candidate-id",
        )
        self.assertEqual(
            session._candidate_history_recovery["candidate_id"],
            "same-persisted-candidate-id",
        )
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    def test_virtualized_history_search_is_bounded_and_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        current_top = 500.0

        def snapshot(_candidate_id, *, target_scroll_top=None):
            nonlocal current_top
            if target_scroll_top is not None:
                current_top = float(target_scroll_top)
            return candidate_history_snapshot(
                surface={
                    **candidate_history_snapshot()["surface"],
                    "scroll_top": current_top,
                },
                requested_scroll_top=target_scroll_top,
                moved=target_scroll_top is not None,
            )

        with patch.object(
            session,
            "ensure_production_window_usable",
            return_value={"after": {"window_state": "maximized", "viewport": {}}},
        ), patch.object(
            session,
            "_candidate_history_scroll_snapshot",
            side_effect=snapshot,
        ) as snapshots, patch(
            "nano_batch.adapters.flow.time.monotonic",
            return_value=0.0,
        ):
            diagnostics = session._reveal_virtualized_candidate_history(
                "missing-persisted-candidate-id",
                max_steps=3,
                timeout_seconds=1.0,
                settle_ms=25,
            )

        self.assertEqual(diagnostics["outcome"], "search_exhausted")
        self.assertEqual(diagnostics["reveal_steps"], 3)
        self.assertEqual(diagnostics["max_reveal_steps"], 3)
        self.assertEqual(
            [stage["exact_match_count"] for stage in diagnostics["stages"]],
            [0, 0, 0, 0],
        )
        self.assertTrue(diagnostics["original_view_restored"])
        self.assertLessEqual(snapshots.call_count, 8)

    def test_virtualized_history_diagnostics_are_bounded_and_privacy_safe(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = {
            **candidate_history_snapshot(),
            "surface": {
                **candidate_history_snapshot()["surface"],
                "id": "account@example.com",
                "data_testid": "results-scroll",
            },
            "prompt": "private prompt",
            "url": "https://signed.example/token",
        }
        session.page = page

        snapshot = session._candidate_history_scroll_snapshot(
            "same-persisted-candidate-id"
        )

        self.assertIsNone(snapshot["surface"]["id"])
        self.assertEqual(snapshot["surface"]["data_testid"], "results-scroll")
        self.assertNotIn("prompt", snapshot)
        self.assertNotIn("url", snapshot)
        script, arguments = page.evaluate.call_args.args
        self.assertEqual(arguments["candidateId"], "same-persisted-candidate-id")
        self.assertIn("flow-image-tile", script)
        self.assertIn("overflowY", script)
        self.assertNotIn("innerText", script)

    def test_history_recovery_source_is_download_only_and_exact_uuid_owned(self):
        source = "\n".join(
            (
                inspect.getsource(FlowSession._candidate_history_scroll_snapshot),
                inspect.getsource(FlowSession._reveal_virtualized_candidate_history),
            )
        )

        self.assertIn("data-media-id", source)
        self.assertIn("exact_match_count", source)
        self.assertNotIn("submit_prompt", source)
        self.assertNotIn("generate_one", source)
        self.assertNotIn("attempt_count", source)
        self.assertNotIn("automatic_policy_retry", source)
        self.assertNotIn("create_flow_project", source)
        self.assertNotIn("run_state", source)
        self.assertNotIn("bringToFront", source)
        self.assertNotIn("force=", source)

    def test_candidate_tile_replaced_before_measurement_is_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        old_image, old_tile = MagicMock(), MagicMock()
        new_image, new_tile = MagicMock(), MagicMock()
        with patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(old_image, old_tile), (new_image, new_tile)],
        ) as reacquire, patch.object(
            session,
            "_current_candidate_tile_center",
            side_effect=[
                FlowAutomationError("candidate tile replaced"),
                {"x": 25.0, "y": 35.0},
            ],
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ):
            image, tile, center = session._reacquire_candidate_tile_for_reveal(
                "candidate-id",
                timeout_ms=500,
            )

        self.assertIs(image, new_image)
        self.assertIs(tile, new_tile)
        self.assertEqual(center, {"x": 25.0, "y": 35.0})
        self.assertEqual(reacquire.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_missing_more_options_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        tile.evaluate.return_value = {"x": 10, "y": 20}
        tile.get_by_role.return_value.all.return_value = []
        session.page = page
        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(image, tile),
        ), patch.object(
            session,
            "_recover_candidate_more_options_after_window_normalization",
            return_value=(
                None,
                {},
                {"recovery_required": True, "recovery_success": True},
                "control_not_present",
            ),
        ), self.assertRaisesRegex(FlowAutomationError, "CANDIDATE_MORE_OPTIONS_NOT_PRESENT"):
            session._reveal_candidate_options("candidate-id", image, tile)
        self.assertEqual(page.mouse.move.call_count, 3)
        self.assertEqual(
            [entry.args for entry in page.wait_for_timeout.call_args_list],
            [(50,), (50,), (50,), (50,), (50,), (50,)],
        )
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_ambiguous_more_options_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        controls = [MagicMock(), MagicMock()]
        tile = MagicMock()
        tile.evaluate.return_value = {"x": 10, "y": 20}
        tile.get_by_role.return_value.all.return_value = controls
        session.page = page
        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 10.0, "y": 20.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(image, tile),
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), self.assertRaisesRegex(FlowAutomationError, "More options control is ambiguous"):
            session._reveal_candidate_options("candidate-id", image, tile)

    def test_candidate_toolbar_and_more_options_are_reacquired_after_tile_replacement(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        original_image = MagicMock()
        original_tile = MagicMock()
        hover_image = MagicMock()
        hover_tile = MagicMock()
        fresh_image = MagicMock()
        fresh_tile = MagicMock()
        fresh_options = MagicMock()
        fresh_tile.get_by_role.return_value.all.return_value = [fresh_options]
        snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        box = snapshot["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(fresh_image, fresh_tile),
        ) as reacquire, patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(hover_image, hover_tile, {"x": 25.0, "y": 35.0}),
        ) as reveal_reacquire, patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=snapshot,
        ) as inspect_options, patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ), patch.object(
            session,
            "_wait_for_candidate_more_options_snackbar_clearance",
        ) as snackbar_wait:
            selected, selected_snapshot, selected_box = session._reveal_candidate_options(
                "candidate-id",
                original_image,
                original_tile,
            )

        self.assertIs(selected, fresh_options)
        self.assertIs(selected_snapshot, snapshot)
        self.assertEqual(selected_box, box)
        reveal_reacquire.assert_called_once_with("candidate-id", timeout_ms=1_200)
        reacquire.assert_called_once_with(
            "candidate-id",
            timeout_ms=500,
            poll_interval_ms=50,
        )
        page.mouse.move.assert_called_once_with(25.0, 35.0)
        original_tile.get_by_role.assert_not_called()
        fresh_tile.get_by_role.assert_called_once_with("button", name="More options")
        inspect_options.assert_called_once_with(fresh_options, "candidate-id")
        snackbar_wait.assert_not_called()
        page.wait_for_timeout.assert_not_called()
        self.assertGreaterEqual(len(selected_snapshot["bounding_box_samples"]), 2)

    def test_more_options_hover_poll_retains_bounded_paint_opportunity(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        image = MagicMock()
        tile = MagicMock()
        control = MagicMock()
        tile.get_by_role.return_value.all.side_effect = [[], [control]]
        snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        box = snapshot["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(image, tile, {"x": 25.0, "y": 35.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(image, tile),
        ) as reacquire, patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=snapshot,
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, _, _ = session._reveal_candidate_options(
                "candidate-id",
                image,
                tile,
            )

        self.assertIs(selected, control)
        self.assertEqual(reacquire.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(50)

    def test_material_snackbar_wait_reacquires_remounted_control_then_succeeds(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        initial_tile, fresh_tile = MagicMock(), MagicMock()
        initial_control, fresh_control = MagicMock(), MagicMock()
        initial_tile.get_by_role.return_value.all.return_value = [initial_control]
        fresh_tile.get_by_role.return_value.all.return_value = [fresh_control]
        blocked = material_snackbar_overlay_snapshot()
        ready = ready_more_options_snapshot()
        box = ready["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), initial_tile, {"x": 10.0, "y": 20.0}),
                (MagicMock(), fresh_tile, {"x": 30.0, "y": 40.0}),
            ],
        ) as candidate_reacquire, patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[
                (MagicMock(), initial_tile),
                (MagicMock(), fresh_tile),
            ],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            side_effect=[blocked, ready],
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, snapshot, selected_box = session._reveal_candidate_options(
                "candidate-id",
                MagicMock(),
                MagicMock(),
            )

        self.assertIs(selected, fresh_control)
        self.assertEqual(selected_box, box)
        self.assertEqual(candidate_reacquire.call_count, 2)
        self.assertEqual(snapshot["transient_overlay_type"], "snackbar")
        self.assertTrue(snapshot["snackbar_wait_entered"])
        self.assertEqual(snapshot["snackbar_poll_count"], 1)
        self.assertTrue(snapshot["snackbar_disappeared"])
        self.assertTrue(snapshot["snackbar_stopped_intersecting"])
        self.assertEqual(snapshot["snackbar_candidate_reacquisition_count"], 1)
        self.assertEqual(snapshot["snackbar_control_reacquisition_count"], 1)
        self.assertEqual(snapshot["prior_reveal_outcomes"], ["snackbar_occlusion_wait"])
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.mouse.click.assert_not_called()
        page.bring_to_front.assert_not_called()

    def test_visible_snackbar_moved_away_from_hit_area_succeeds(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tile = MagicMock()
        control = MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        ready = ready_more_options_snapshot(visible_snackbar_count=1)
        box = ready["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(MagicMock(), tile, {"x": 30.0, "y": 40.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(MagicMock(), tile),
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=ready,
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, snapshot, _ = (
                session._wait_for_candidate_more_options_snackbar_clearance(
                    "candidate-id"
                )
            )

        self.assertIs(selected, control)
        self.assertFalse(snapshot["snackbar_disappeared"])
        self.assertTrue(snapshot["snackbar_stopped_intersecting"])
        page.mouse.down.assert_not_called()

    def test_snackbar_pointer_events_none_allows_fresh_owned_hit_test(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tile = MagicMock()
        control = MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        blocked = material_snackbar_overlay_snapshot()
        pointer_transparent = ready_more_options_snapshot(visible_snackbar_count=1)
        box = pointer_transparent["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(MagicMock(), tile, {"x": 30.0, "y": 40.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(MagicMock(), tile),
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            side_effect=[blocked, pointer_transparent],
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, snapshot, _ = (
                session._wait_for_candidate_more_options_snackbar_clearance(
                    "candidate-id"
                )
            )

        self.assertIs(selected, control)
        self.assertEqual(snapshot["snackbar_poll_count"], 2)
        self.assertFalse(snapshot["snackbar_disappeared"])
        self.assertTrue(snapshot["snackbar_stopped_intersecting"])
        self.assertIn(((100,), {}), [
            (call.args, call.kwargs) for call in page.wait_for_timeout.call_args_list
        ])
        page.mouse.down.assert_not_called()

    def test_permanent_material_snackbar_overlap_fails_closed_without_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tile = MagicMock()
        control = MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        blocked = material_snackbar_overlay_snapshot()

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            return_value=(MagicMock(), tile, {"x": 30.0, "y": 40.0}),
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            return_value=(MagicMock(), tile),
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=blocked,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 4.0],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "transient_overlay_type=snackbar.*snackbar_poll_count=2",
        ):
            session._wait_for_candidate_more_options_snackbar_clearance(
                "candidate-id"
            )

        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.mouse.click.assert_not_called()
        page.bring_to_front.assert_not_called()

    def test_arbitrary_overlay_never_enters_snackbar_wait(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tiles = [MagicMock(), MagicMock(), MagicMock()]
        controls = [MagicMock(), MagicMock(), MagicMock()]
        for tile, control in zip(tiles, controls):
            tile.get_by_role.return_value.all.return_value = [control]
        arbitrary = more_options_hit_snapshot(
            [
                {
                    "name": name,
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": None,
                    "top": {
                        "tag": "div",
                        "class_summary": ["mdc-snackbar__label"],
                        "pointer_events": "auto",
                        "visibility": "visible",
                        "opacity": "1",
                        "connected": True,
                        "material_snackbar": False,
                    },
                }
                for name in ("center", "left", "right", "up", "down")
            ],
            center_top_belongs_to_control=False,
        )

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), tile, {"x": 10.0, "y": 20.0}) for tile in tiles
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), tile) for tile in tiles],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=arbitrary,
        ), patch.object(
            session,
            "_wait_for_candidate_more_options_snackbar_clearance",
        ) as snackbar_wait, self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_OVERLAID",
        ):
            session._reveal_candidate_options(
                "candidate-id",
                MagicMock(),
                MagicMock(),
            )

        snackbar_wait.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.click.assert_not_called()

    def test_toolbar_initially_unrevealed_then_appears_after_bounded_retry(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        first_tile = MagicMock()
        second_tile = MagicMock()
        first_tile.get_by_role.return_value.all.return_value = []
        control = MagicMock()
        second_tile.get_by_role.return_value.all.return_value = [control]
        snapshot = download_control_snapshot(role="button", visible_text="More options")
        box = snapshot["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), first_tile, {"x": 10.0, "y": 20.0}),
                (MagicMock(), second_tile, {"x": 30.0, "y": 40.0}),
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), first_tile), (MagicMock(), second_tile)],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=snapshot,
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, selected_snapshot, _ = session._reveal_candidate_options(
                "candidate-id", MagicMock(), MagicMock()
            )

        self.assertIs(selected, control)
        self.assertEqual(selected_snapshot["reveal_attempt"], 1)
        page.mouse.move.assert_called_once_with(10.0, 20.0)
        page.wait_for_timeout.assert_called_once_with(50)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_image_topmost_then_second_reveal_reacquisition_succeeds(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tiles = [MagicMock(), MagicMock()]
        controls = [MagicMock(), MagicMock()]
        for tile, control in zip(tiles, controls):
            tile.get_by_role.return_value.all.return_value = [control]
        overlaid = more_options_hit_snapshot(
            [
                {
                    "name": name,
                    "x": x,
                    "y": y,
                    "ownership": None,
                    "top": {"tag": "img", "connected": True},
                }
                for name, x, y in (
                    ("center", 170.0, 212.0),
                    ("left", 144.8, 212.0),
                    ("right", 195.2, 212.0),
                    ("up", 170.0, 207.68),
                    ("down", 170.0, 216.32),
                )
            ],
            center_top_belongs_to_control=False,
        )
        ready = download_control_snapshot(role="button", visible_text="More options")
        box = ready["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), tiles[0], {"x": 10.0, "y": 20.0}),
                (MagicMock(), tiles[1], {"x": 10.0, "y": 20.0}),
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), tiles[0]), (MagicMock(), tiles[1])],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            side_effect=[overlaid, ready],
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, selected_snapshot, _ = session._reveal_candidate_options(
                "candidate-id", MagicMock(), MagicMock()
            )

        self.assertIs(selected, controls[1])
        self.assertEqual(selected_snapshot["reveal_attempt"], 2)
        self.assertEqual(page.mouse.move.call_count, 2)
        page.mouse.down.assert_not_called()

    def test_stale_more_options_box_is_discarded_for_fresh_control(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tiles = [MagicMock(), MagicMock()]
        controls = [MagicMock(), MagicMock()]
        for tile, control in zip(tiles, controls):
            tile.get_by_role.return_value.all.return_value = [control]
        snapshot = download_control_snapshot(role="button", visible_text="More options")
        box = snapshot["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), tiles[0], {"x": 10.0, "y": 20.0}),
                (MagicMock(), tiles[1], {"x": 11.0, "y": 21.0}),
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), tiles[0]), (MagicMock(), tiles[1])],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=snapshot,
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            side_effect=[
                FlowAutomationError("CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE"),
                box,
            ],
        ):
            selected, selected_snapshot, _ = session._reveal_candidate_options(
                "candidate-id", MagicMock(), MagicMock()
            )

        self.assertIs(selected, controls[1])
        self.assertEqual(selected_snapshot["reveal_attempt"], 2)
        self.assertEqual(page.mouse.move.call_count, 2)

    def test_tile_remount_between_reveal_and_hit_test_restarts_local_reveal(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tiles = [MagicMock(), MagicMock()]
        controls = [MagicMock(), MagicMock()]
        for tile, control in zip(tiles, controls):
            tile.get_by_role.return_value.all.return_value = [control]
        snapshot = download_control_snapshot(role="button", visible_text="More options")
        box = snapshot["bounding_box_samples"][-1]

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), tiles[0], {"x": 10.0, "y": 20.0}),
                (MagicMock(), tiles[1], {"x": 12.0, "y": 22.0}),
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), tiles[0]), (MagicMock(), tiles[1])],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            side_effect=[
                FlowAutomationError("CANDIDATE_MORE_OPTIONS_WRONG_TILE"),
                snapshot,
            ],
        ), patch.object(
            session,
            "_current_candidate_more_options_hit",
            return_value=box,
        ):
            selected, selected_snapshot, _ = session._reveal_candidate_options(
                "candidate-id", MagicMock(), MagicMock()
            )

        self.assertIs(selected, controls[1])
        self.assertEqual(selected_snapshot["reveal_attempt"], 2)
        self.assertEqual(page.mouse.move.call_count, 2)

    def test_image_remains_topmost_after_bounded_reveals_fails_without_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        tiles = [MagicMock(), MagicMock(), MagicMock()]
        for tile in tiles:
            tile.get_by_role.return_value.all.return_value = [MagicMock()]
        overlaid = more_options_hit_snapshot(
            [
                {
                    "name": name,
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": None,
                    "top": {"tag": "img", "connected": True},
                }
                for name in ("center", "left", "right", "up", "down")
            ],
            center_top_belongs_to_control=False,
        )

        with patch.object(
            session,
            "_reacquire_candidate_tile_for_reveal",
            side_effect=[
                (MagicMock(), tile, {"x": 10.0, "y": 20.0}) for tile in tiles
            ],
        ), patch.object(
            session,
            "_exact_candidate_image_tile",
            side_effect=[(MagicMock(), tile) for tile in tiles],
        ), patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session,
            "_inspect_candidate_more_options",
            return_value=overlaid,
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_IMAGE_TOPMOST",
        ):
            session._reveal_candidate_options(
                "candidate-id", MagicMock(), MagicMock()
            )

        self.assertEqual(page.mouse.move.call_count, 3)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.mouse.click.assert_not_called()

    def test_stable_candidate_more_options_box_requires_center_hit(self):
        snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        box = _stable_candidate_more_options_box(snapshot)
        self.assertEqual(box, snapshot["bounding_box_samples"][-1])
        with self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_OVERLAID",
        ):
            _stable_candidate_more_options_box(
                download_control_snapshot(
                    role="button",
                    visible_text="More options",
                    center_top_belongs_to_control=False,
                )
            )

    def test_more_options_zero_opacity_is_hidden_even_with_nonzero_box(self):
        snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
            opacity="0",
            visibility="visible",
            display="block",
        )
        with self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_HIDDEN",
        ):
            _stable_candidate_more_options_box(snapshot)

    def test_more_options_center_accepts_direct_descendant_and_composed_ownership(self):
        cases = (
            ("self", "button"),
            ("descendant", "span"),
            ("descendant", "div"),
            ("composed_descendant", "mat-ripple"),
        )
        for ownership, tag in cases:
            with self.subTest(ownership=ownership):
                snapshot = more_options_hit_snapshot(
                    [
                        {
                            "name": "center",
                            "x": 170.0,
                            "y": 212.0,
                            "ownership": ownership,
                            "top": {"tag": tag, "connected": True},
                        }
                    ]
                )
                box = _stable_candidate_more_options_box(snapshot)
                point = _candidate_more_options_safe_point(snapshot, box)
                self.assertEqual(point["name"], "center")
                self.assertEqual(point["ownership"], ownership)

    def test_blocked_center_selects_first_owned_bounded_interior_point(self):
        snapshot = more_options_hit_snapshot(
            [
                {
                    "name": "center",
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": None,
                    "top": {"tag": "div", "connected": True},
                },
                {
                    "name": "left",
                    "x": 144.8,
                    "y": 212.0,
                    "ownership": "descendant",
                    "top": {"tag": "mat-icon", "connected": True},
                },
                {
                    "name": "right",
                    "x": 195.2,
                    "y": 212.0,
                    "ownership": "descendant",
                    "top": {"tag": "span", "connected": True},
                },
            ],
            center_top_belongs_to_control=False,
        )
        box = _stable_candidate_more_options_box(snapshot)
        point = _candidate_more_options_safe_point(snapshot, box)
        self.assertEqual(point["name"], "left")
        self.assertEqual((point["x"], point["y"]), (144.8, 212.0))

    def test_all_more_options_interior_points_blocked_by_overlay_fail_closed(self):
        probes = [
            {
                "name": name,
                "x": x,
                "y": y,
                "ownership": None,
                "top": {"tag": "div", "role": "tooltip", "connected": True},
            }
            for name, x, y in (
                ("center", 170.0, 212.0),
                ("left", 144.8, 212.0),
                ("right", 195.2, 212.0),
                ("up", 170.0, 207.68),
                ("down", 170.0, 216.32),
            )
        ]
        with self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_OVERLAID",
        ):
            _stable_candidate_more_options_box(
                more_options_hit_snapshot(
                    probes,
                    center_top_belongs_to_control=False,
                )
            )

    def test_snackbar_occlusion_requires_structural_visible_pointer_active_evidence(self):
        proven = material_snackbar_overlay_snapshot()
        self.assertTrue(
            _candidate_more_options_has_material_snackbar_occlusion(proven)
        )

        arbitrary = material_snackbar_overlay_snapshot()
        for probe in arbitrary["hit_test_probes"]:
            probe["top"]["material_snackbar"] = False
        self.assertFalse(
            _candidate_more_options_has_material_snackbar_occlusion(arbitrary)
        )

        pointer_transparent = material_snackbar_overlay_snapshot()
        for probe in pointer_transparent["hit_test_probes"]:
            probe["top"]["pointer_events"] = "none"
        self.assertFalse(
            _candidate_more_options_has_material_snackbar_occlusion(
                pointer_transparent
            )
        )

    def test_unrelated_sibling_at_more_options_probe_is_never_owned(self):
        snapshot = more_options_hit_snapshot(
            [
                {
                    "name": "center",
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": None,
                    "top": {"tag": "button", "aria_label": "Different control"},
                }
            ],
            center_top_belongs_to_control=False,
        )
        with self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_OVERLAID",
        ):
            _stable_candidate_more_options_box(snapshot)

    def test_same_toolbar_unrelated_div_is_rejected_with_safe_diagnostics(self):
        snapshot = more_options_hit_snapshot(
            [
                {
                    "name": "center",
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": None,
                    "top": {
                        "tag": "div",
                        "role": "presentation",
                        "aria_label": "Toolbar surface",
                        "class_summary": ["toolbar-surface", "mat-layer"],
                        "pointer_events": "auto",
                        "visibility": "visible",
                        "opacity": "1",
                        "bounding_box": {
                            "x": 100.0,
                            "y": 200.0,
                            "width": 140.0,
                            "height": 24.0,
                        },
                        "top_contains_control": False,
                        "same_toolbar": True,
                        "same_candidate_tile": True,
                        "event_path_reaches_control": False,
                        "composed_ownership": None,
                    },
                }
            ],
            center_top_belongs_to_control=False,
        )
        with self.assertRaises(FlowAutomationError) as raised:
            _stable_candidate_more_options_box(snapshot)

        message = str(raised.exception)
        self.assertIn("tag=div", message)
        self.assertIn("class=toolbar-surface.mat-layer", message)
        self.assertIn("same_toolbar=True", message)
        self.assertIn("same_candidate_tile=True", message)
        self.assertIn("event_path_reaches_control=False", message)

    def test_more_options_owned_probe_does_not_bypass_unstable_geometry(self):
        snapshot = more_options_hit_snapshot(
            [
                {
                    "name": "center",
                    "x": 170.0,
                    "y": 212.0,
                    "ownership": "self",
                    "top": {"tag": "button"},
                }
            ],
            bounding_box_samples=[
                {"x": 100, "y": 200, "width": 140, "height": 24},
                {"x": 105, "y": 200, "width": 140, "height": 24},
            ],
        )
        with self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MORE_OPTIONS_BBOX_UNSTABLE",
        ):
            _stable_candidate_more_options_box(snapshot)

    def test_more_options_probe_inspection_records_only_structural_ownership(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        control = MagicMock()
        raw_snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        raw_probes = {
            "belongs_to_candidate_tile": True,
            "probes": [
                {
                    "name": "center",
                    "x": 170,
                    "y": 212,
                    "ownership": "composed_descendant",
                    "top": {
                        "tag": "span",
                        "connected": True,
                        "class_summary": ["icon-wrapper"],
                        "pointer_events": "auto",
                        "visibility": "visible",
                        "opacity": "1",
                        "same_toolbar": True,
                        "same_candidate_tile": True,
                        "event_path_reaches_control": True,
                        "composed_ownership": "composed_descendant",
                    },
                }
            ],
        }
        with patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=raw_snapshot,
        ):
            control.evaluate.return_value = raw_probes
            snapshot = session._inspect_candidate_more_options(
                control,
                "candidate-id",
            )

        self.assertEqual(
            snapshot["safe_owned_point"]["ownership"],
            "composed_descendant",
        )
        top = snapshot["hit_test_probes"][0]["top"]
        self.assertEqual(top["class_summary"], ["icon-wrapper"])
        self.assertTrue(top["same_toolbar"])
        self.assertTrue(top["same_candidate_tile"])
        self.assertTrue(top["event_path_reaches_control"])
        self.assertEqual(top["composed_ownership"], "composed_descendant")
        script = control.evaluate.call_args.args[0]
        self.assertIn("document.elementFromPoint", script)
        self.assertIn("ShadowRoot", script)
        self.assertIn("class_summary", script)
        self.assertIn("same_candidate_tile", script)
        self.assertIn("event_path_reaches_control", script)
        self.assertIn("materialSnackbarRoot", script)
        self.assertIn(".mat-mdc-snack-bar-container", script)
        self.assertNotIn(".click()", script)

    def test_more_options_activation_uses_exactly_one_move_down_up_not_click(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        box = download_control_snapshot()["bounding_box_samples"][-1]
        with patch.object(
            session,
            "_current_candidate_more_options_hit",
            side_effect=[box, box, box],
        ) as hit:
            result = session._activate_candidate_more_options(
                control,
                "candidate-id",
                box,
            )

        self.assertEqual(result["activation_count"], 1)
        self.assertEqual(result["input"], "mouse_move_down_up")
        self.assertEqual(hit.call_count, 3)
        page.mouse.move.assert_called_once_with(170.1, 212.0)
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        control.click.assert_not_called()

    def test_more_options_activation_uses_selected_noncenter_safe_point(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        box = {
            **download_control_snapshot()["bounding_box_samples"][-1],
            "activation_point": {
                "name": "left",
                "x": 144.8,
                "y": 212.0,
                "ownership": "descendant",
            },
        }
        with patch.object(
            session,
            "_current_candidate_more_options_hit",
            side_effect=[box, box, box],
        ):
            result = session._activate_candidate_more_options(
                control,
                "candidate-id",
                box,
            )

        page.mouse.move.assert_called_once_with(144.8, 212.0)
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        self.assertEqual(result["safe_point"]["name"], "left")

    def test_preexisting_visible_menu_is_excluded_from_fresh_candidate_menu(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        menu = MagicMock()
        download = MagicMock()
        menu_query = MagicMock()
        menu_query.all.return_value = [menu]
        item_query = MagicMock()
        item_query.all.return_value = [download]
        page.get_by_role.side_effect = lambda role, **kwargs: (
            menu_query if role == "menu" else item_query
        )
        menu.evaluate.return_value = True
        download.evaluate.return_value = {
            "inside_visible_menu": True,
            "stale_at_baseline": True,
        }
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ):
            eligible, facts = session._current_candidate_menu_controls()

        self.assertEqual(eligible, [])
        self.assertEqual(facts["live_menu_count"], 1)
        self.assertEqual(facts["stale_visible_menu_count"], 1)
        self.assertEqual(facts["fresh_live_menu_count"], 0)
        self.assertEqual(facts["fresh_download_match_count"], 0)

    def test_fresh_post_activation_menu_is_accepted(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        menu = MagicMock()
        download = MagicMock()
        menu_query = MagicMock()
        menu_query.all.return_value = [menu]
        item_query = MagicMock()
        item_query.all.return_value = [download]
        page.get_by_role.side_effect = lambda role, **kwargs: (
            menu_query if role == "menu" else item_query
        )
        menu.evaluate.return_value = False
        download.evaluate.return_value = {
            "inside_visible_menu": True,
            "stale_at_baseline": False,
        }
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ):
            eligible, facts = session._current_candidate_menu_controls()

        self.assertEqual(eligible, [download])
        self.assertEqual(facts["fresh_live_menu_count"], 1)
        self.assertTrue(facts["menu_visible"])

    def test_missing_and_ambiguous_download_menu_items_fail_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        enabled = [MagicMock(), MagicMock()]
        for control in enabled:
            control.is_enabled.return_value = True
        for matches, expected in (([], "menu item is missing"), (enabled, "menu item is ambiguous")):
            with self.subTest(count=len(matches)), patch.object(
                session,
                "_visible_role_matches",
                return_value=matches,
            ), self.assertRaisesRegex(FlowAutomationError, expected):
                session._unique_download_menu_item()

    def test_mouse_click_control_uses_bounding_box_and_real_mouse_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        control = MagicMock()
        control.bounding_box.return_value = {
            "x": 100,
            "y": 200,
            "width": 40,
            "height": 20,
        }
        session.page = page

        session._mouse_click_control(control)

        control.bounding_box.assert_called_once_with()
        control.evaluate.assert_not_called()
        page.mouse.move.assert_called_once_with(120.0, 210.0)
        page.mouse.click.assert_called_once_with(120.0, 210.0)

    def test_download_menu_item_dom_structural_inspection_is_safely_normalized(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        control = MagicMock()
        control.evaluate.return_value = download_control_snapshot()

        snapshot = session._inspect_download_menu_item(control)

        self.assertEqual(snapshot["tag"], "button")
        self.assertEqual(snapshot["role"], "menuitem")
        self.assertEqual(snapshot["tabindex"], "0")
        self.assertEqual(snapshot["pointer_events"], "auto")
        self.assertTrue(snapshot["connected"])
        self.assertTrue(snapshot["has_descendants"])
        self.assertEqual(snapshot["center_top"]["tag"], "span")
        self.assertFalse(snapshot["overlay_above"])
        self.assertEqual(len(snapshot["bounding_box_samples"]), 3)
        args = control.evaluate.call_args.args[1]
        self.assertEqual(args, {"sampleCount": 4, "sampleIntervalMs": 100})

    def test_disabled_and_aria_disabled_download_controls_are_rejected(self):
        for overrides in ({"disabled": True}, {"aria_disabled": "true"}):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_CONTROL_DISABLED",
            ):
                _stable_download_control_box(download_control_snapshot(**overrides))

    def test_download_control_overlay_is_rejected(self):
        snapshot = download_control_snapshot(center_top_belongs_to_control=False)
        with self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_CONTROL_OVERLAID"):
            _stable_download_control_box(snapshot)

    def test_stale_detached_download_control_is_rejected(self):
        snapshot = download_control_snapshot(connected=False, visible=True)
        with self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_CONTROL_NOT_ACTIONABLE"):
            _stable_download_control_box(snapshot)

    def test_unstable_download_control_bounding_box_is_rejected(self):
        snapshot = download_control_snapshot(
            bounding_box_samples=[
                {"x": 100, "y": 200, "width": 140, "height": 24},
                {"x": 103, "y": 200, "width": 140, "height": 24},
            ]
        )
        with self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_CONTROL_BBOX_UNSTABLE"):
            _stable_download_control_box(snapshot)

    def test_stable_download_control_bounding_box_is_accepted(self):
        box = _stable_download_control_box(download_control_snapshot())
        self.assertEqual(box, {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0})

    def test_opening_animation_can_settle_into_three_stable_samples(self):
        snapshot = download_control_snapshot(
            bounding_box_samples=[
                {"x": 97, "y": 196, "width": 136, "height": 23},
                {"x": 100, "y": 200, "width": 140, "height": 24},
                {"x": 100, "y": 200, "width": 140, "height": 24},
                {"x": 100, "y": 200, "width": 140, "height": 24},
            ]
        )
        self.assertEqual(
            _stable_download_control_box(snapshot),
            {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0},
        )

    def test_candidate_menu_opens_before_download_control_is_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        options = MagicMock()
        download = MagicMock()
        options_snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        options_box = options_snapshot["bounding_box_samples"][-1]
        diagnostics = session._empty_candidate_menu_diagnostics()
        timeline = []
        before = {
            "live_menu_count": 0,
            "fresh_download_match_count": 0,
            "fresh_download_visible_count": 0,
            "fresh_download_connected_count": 0,
            "fresh_download_enabled_count": 0,
            "menu_visible": False,
        }
        with patch.object(
            session,
            "_current_candidate_menu_controls",
            return_value=([], before),
        ), patch.object(
            session,
            "_reveal_candidate_options",
            return_value=(options, options_snapshot, options_box),
        ), patch.object(
            session,
            "_arm_candidate_menu_baseline",
            side_effect=lambda page, candidate_id: (
                timeline.append(("baseline", candidate_id))
                or {"candidate_id": candidate_id, "visible_menu_count": 0}
            ),
        ), patch.object(
            session,
            "_activate_candidate_more_options",
            side_effect=lambda control, candidate_id, box: (
                timeline.append(("open", control))
                or {"input": "mouse_move_down_up", "activation_count": 1}
            ),
        ), patch.object(
            session,
            "_clear_candidate_menu_baseline",
        ), patch.object(
            session,
            "_wait_for_candidate_menu",
            side_effect=lambda candidate_id, report: (
                timeline.append(("reacquire", candidate_id))
                or (download, download_control_snapshot())
            ),
        ):
            selected, _ = session._open_candidate_menu(
                "candidate-id",
                image,
                tile,
                diagnostics,
            )

        self.assertIs(selected, download)
        self.assertEqual(
            timeline,
            [
                ("baseline", "candidate-id"),
                ("open", options),
                ("reacquire", "candidate-id"),
            ],
        )
        self.assertTrue(diagnostics["more_options_before_activation"]["connected"])
        self.assertEqual(diagnostics["more_options_activation_count"], 1)

    def test_slow_candidate_menu_acknowledgement_never_reactivates_more_options(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        options = MagicMock()
        snapshot = download_control_snapshot(
            role="button",
            visible_text="More options",
        )
        box = snapshot["bounding_box_samples"][-1]
        diagnostics = session._empty_candidate_menu_diagnostics()
        before = {
            "live_menu_count": 0,
            "fresh_download_match_count": 0,
            "fresh_download_visible_count": 0,
            "fresh_download_connected_count": 0,
            "fresh_download_enabled_count": 0,
            "menu_visible": False,
        }
        with patch.object(
            session,
            "_current_candidate_menu_controls",
            return_value=([], before),
        ), patch.object(
            session,
            "_reveal_candidate_options",
            return_value=(options, snapshot, box),
        ), patch.object(
            session,
            "_arm_candidate_menu_baseline",
            return_value={"candidate_id": "candidate-id", "visible_menu_count": 0},
        ), patch.object(
            session,
            "_activate_candidate_more_options",
            return_value={"input": "mouse_move_down_up", "activation_count": 1},
        ) as activate, patch.object(
            session,
            "_wait_for_candidate_menu",
            side_effect=FlowAutomationError("CANDIDATE_MENU_OPEN_TIMEOUT"),
        ), patch.object(
            session,
            "_clear_candidate_menu_baseline",
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "CANDIDATE_MENU_OPEN_TIMEOUT",
        ):
            session._open_candidate_menu(
                "candidate-id",
                MagicMock(),
                MagicMock(),
                diagnostics,
            )

        activate.assert_called_once_with(options, "candidate-id", box)
        self.assertEqual(diagnostics["more_options_activation_count"], 1)

    def test_candidate_menu_wait_retries_then_returns_fresh_connected_item(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        diagnostics = session._empty_candidate_menu_diagnostics()
        absent = {
            "live_menu_count": 0,
            "fresh_download_match_count": 0,
            "fresh_download_visible_count": 0,
            "fresh_download_connected_count": 0,
            "fresh_download_enabled_count": 0,
            "menu_visible": False,
        }
        ready = {
            "live_menu_count": 1,
            "fresh_download_match_count": 1,
            "fresh_download_visible_count": 1,
            "fresh_download_connected_count": 1,
            "fresh_download_enabled_count": 1,
            "menu_visible": True,
        }
        with patch.object(
            session,
            "_current_candidate_menu_controls",
            side_effect=[([], absent), ([control], ready)],
        ), patch.object(
            session,
            "_remember_download_node_identity",
            return_value=(False, False),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=download_control_snapshot(),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            selected, snapshot = session._wait_for_candidate_menu(
                "candidate-id",
                diagnostics,
                timeout_ms=500,
            )

        self.assertIs(selected, control)
        self.assertTrue(snapshot["connected"])
        self.assertEqual(diagnostics["poll_count"], 2)
        self.assertEqual(diagnostics["fresh_download_connected_count"], 1)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_candidate_menu_wait_discards_detached_node_and_reacquires_replacement(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        stale = MagicMock()
        fresh = MagicMock()
        diagnostics = session._empty_candidate_menu_diagnostics()
        ready = {
            "live_menu_count": 1,
            "fresh_download_match_count": 1,
            "fresh_download_visible_count": 1,
            "fresh_download_connected_count": 1,
            "fresh_download_enabled_count": 1,
            "menu_visible": True,
        }
        with patch.object(
            session,
            "_current_candidate_menu_controls",
            side_effect=[([stale], ready), ([fresh], ready)],
        ), patch.object(
            session,
            "_remember_download_node_identity",
            side_effect=[(False, False), (True, True)],
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            side_effect=[
                download_control_snapshot(connected=False, visible=True),
                download_control_snapshot(),
            ],
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            selected, _ = session._wait_for_candidate_menu(
                "candidate-id",
                diagnostics,
                timeout_ms=500,
            )

        self.assertIs(selected, fresh)
        self.assertTrue(diagnostics["download_node_detached_during_wait"])
        self.assertTrue(diagnostics["download_node_identity_changed_after_open"])

    def test_candidate_menu_open_wait_is_bounded_and_times_out_clearly(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        diagnostics = session._empty_candidate_menu_diagnostics()
        absent = {
            "live_menu_count": 0,
            "fresh_download_match_count": 0,
            "fresh_download_visible_count": 0,
            "fresh_download_connected_count": 0,
            "fresh_download_enabled_count": 0,
            "menu_visible": False,
        }
        with patch.object(
            session,
            "_current_candidate_menu_controls",
            return_value=([], absent),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 1.0],
        ), self.assertRaisesRegex(FlowAutomationError, "CANDIDATE_MENU_OPEN_TIMEOUT"):
            session._wait_for_candidate_menu(
                "candidate-id",
                diagnostics,
                timeout_ms=250,
            )
        self.assertEqual(diagnostics["poll_count"], 1)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_mouse_activation_uses_verified_move_down_up_sequence(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        snapshot = download_control_snapshot()
        box = snapshot["bounding_box_samples"][0]
        diagnostic_trace = {}
        with patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=snapshot,
        ), patch.object(
            session,
            "_current_download_control_hit",
            side_effect=[box, box, box],
        ) as hit:
            details = session._activate_download_control(
                control,
                "mouse",
                diagnostic_trace=diagnostic_trace,
            )

        self.assertEqual(details["input"], "mouse_move_down_up")
        self.assertEqual(hit.call_count, 3)
        page.mouse.move.assert_called_once_with(170.0, 212.0)
        page.mouse.down.assert_called_once_with(button="left")
        page.mouse.up.assert_called_once_with(button="left")
        page.mouse.click.assert_not_called()
        control.click.assert_not_called()
        self.assertTrue(diagnostic_trace["activation_dispatch_began"])
        self.assertTrue(diagnostic_trace["activation_mouse_move_completed"])
        self.assertTrue(diagnostic_trace["activation_mouse_down_completed"])
        self.assertTrue(diagnostic_trace["activation_mouse_up_completed"])
        self.assertTrue(diagnostic_trace["activation_dispatch_returned_normally"])

    def test_keyboard_activation_focuses_exact_item_and_uses_enter(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        control.evaluate.return_value = True
        with patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=download_control_snapshot(),
        ), patch.object(
            session,
            "_current_download_control_hit",
            return_value={"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0},
        ):
            details = session._activate_download_control(control, "keyboard")

        control.focus.assert_called_once_with(timeout=3_000)
        page.keyboard.down.assert_called_once_with("Enter")
        page.keyboard.up.assert_called_once_with("Enter")
        page.mouse.click.assert_not_called()
        self.assertEqual(details["key"], "Enter")
        self.assertTrue(details["hit_verified_before_focus"])

    def test_locator_activation_uses_normal_click_without_force(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        control = MagicMock()
        with patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=download_control_snapshot(),
        ):
            details = session._activate_download_control(control, "locator")

        control.click.assert_called_once_with(timeout=3_000)
        self.assertNotIn("force", control.click.call_args.kwargs)
        self.assertEqual(details["input"], "locator_click")

    def test_trace_hook_normalizes_full_mouse_and_keyboard_event_sequences(self):
        raw_events = []
        for event_type in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
            raw_events.append(
                {
                    "kind": "input_event",
                    "event_type": event_type,
                    "is_trusted": True,
                    "tag": "span",
                    "role": "menuitem",
                    "target_label": "Download",
                    "default_prevented": False,
                    "button": 0,
                    "buttons": 1 if event_type in {"pointerdown", "mousedown"} else 0,
                    "private_text": "must not survive",
                }
            )
        for event_type in ("keydown", "keyup"):
            raw_events.append(
                {
                    "kind": "input_event",
                    "event_type": event_type,
                    "is_trusted": True,
                    "tag": "button",
                    "role": "menuitem",
                    "target_label": "Download",
                    "default_prevented": False,
                    "key": "Enter",
                }
            )
        page = MagicMock()
        page.evaluate.return_value = {
            "events": raw_events,
            "final": {
                "menu_connected": False,
                "menu_visible": False,
                "target_connected": False,
                "target_visible": False,
                "active_element": {"tag": "body", "connected": True, "inside_menu": False},
            },
        }

        result = FlowSession._remove_download_trace_hooks(page)

        self.assertEqual(
            [event["event_type"] for event in result["events"]],
            ["pointerdown", "mousedown", "pointerup", "mouseup", "click", "keydown", "keyup"],
        )
        self.assertTrue(all(event["is_trusted"] for event in result["events"]))
        self.assertNotIn("private_text", json.dumps(result))
        self.assertFalse(result["final"]["menu_visible"])

    def test_manual_hook_installer_is_observation_only_and_candidate_scoped(self):
        page = MagicMock()
        candidate_id = "00000000-0000-4000-8000-000000000003"

        FlowSession._install_manual_download_trace_hooks(page, candidate_id)

        script, argument = page.evaluate.call_args.args
        self.assertEqual(argument, candidate_id)
        self.assertIn("__nanoBatchManualDownloadTraceV1", script)
        self.assertIn("candidateMenuRequested", script)
        self.assertIn("candidate_more_options", script)
        self.assertIn('document.addEventListener(eventType, state.inputListener, true)', script)
        self.assertNotIn("dispatchEvent(", script)
        self.assertNotIn(".click()", script)
        self.assertNotIn(".focus()", script)

    def test_download_submenu_dom_probe_captures_parent_hierarchy_and_items(self):
        page = MagicMock()
        page.evaluate.return_value = {
            "visible_menu_count": 2,
            "menus": [
                {
                    "index": 0,
                    "tag": "div",
                    "role": "menu",
                    "visible_text": "Download Full size",
                    "connected": True,
                    "visible": True,
                    "bounding_box": {"x": 100, "y": 100, "width": 180, "height": 300},
                    "items": [
                        {
                            "index": 0,
                            "tag": "button",
                            "role": "menuitem",
                            "visible_text": "downloadDownloadchevron_right",
                            "disabled": False,
                            "aria_disabled": "false",
                            "tabindex": "0",
                            "aria_haspopup": "menu",
                            "aria_expanded": "true",
                            "connected": True,
                            "visible": True,
                            "bounding_box": {"x": 100, "y": 180, "width": 180, "height": 24},
                            "icons": [
                                {"tag": "mat-icon", "role": "img", "text": "download"},
                                {"tag": "mat-icon", "role": "img", "text": "chevron_right"},
                            ],
                        }
                    ],
                },
                {
                    "index": 1,
                    "tag": "div",
                    "role": "menu",
                    "visible_text": "Full size Compressed",
                    "connected": True,
                    "visible": True,
                    "bounding_box": {"x": 280, "y": 180, "width": 170, "height": 80},
                    "items": [
                        {
                            "index": 0,
                            "tag": "button",
                            "role": "menuitem",
                            "visible_text": "Full size",
                            "disabled": False,
                            "tabindex": "0",
                            "connected": True,
                            "visible": True,
                            "bounding_box": {"x": 280, "y": 180, "width": 170, "height": 24},
                            "icons": [],
                        },
                        {
                            "index": 1,
                            "tag": "button",
                            "role": "menuitem",
                            "visible_text": "Compressed",
                            "disabled": True,
                            "aria_disabled": "true",
                            "tabindex": "-1",
                            "connected": True,
                            "visible": True,
                            "bounding_box": {"x": 280, "y": 204, "width": 170, "height": 24},
                            "icons": [],
                        },
                    ],
                },
            ],
            "download_parent": {
                "match_count": 1,
                "parent_menu_index": 0,
                "parent_item_index": 0,
                "aria_haspopup": "menu",
                "aria_expanded": "true",
                "has_aria_controls": False,
                "has_aria_owns": False,
                "submenu_indicator": [
                    {"tag": "mat-icon", "role": "img", "text": "chevron_right"}
                ],
                "relationship": {
                    "status": "inferred_visible_sibling",
                    "basis": ["visible_sibling_with_submenu_indicator"],
                    "submenu_menu_indices": [1],
                },
            },
            "submenu_detected": True,
            "framework_component": "must not survive",
        }

        result = FlowSession._inspect_download_submenu_dom(page)

        self.assertEqual(result["visible_menu_count"], 2)
        self.assertEqual(result["download_parent"]["parent_menu_index"], 0)
        self.assertEqual(result["download_parent"]["aria_haspopup"], "menu")
        self.assertEqual(result["download_parent"]["aria_expanded"], "true")
        self.assertEqual(
            result["download_parent"]["relationship"]["submenu_menu_indices"], [1]
        )
        self.assertEqual(
            [item["visible_text"] for item in result["menus"][1]["items"]],
            ["Full size", "Compressed"],
        )
        self.assertTrue(result["menus"][1]["items"][1]["disabled"])
        self.assertNotIn("framework_component", json.dumps(result))
        script = page.evaluate.call_args.args[0]
        self.assertNotIn("dispatchEvent(", script)
        self.assertNotIn(".click()", script)
        self.assertNotIn(".focus()", script)

    def test_submenu_signature_ignores_animation_bounding_box_changes(self):
        raw = {
            "menus": [
                {
                    "index": 0,
                    "tag": "div",
                    "role": "menu",
                    "connected": True,
                    "visible": True,
                    "bounding_box": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "items": [],
                }
            ],
            "download_parent": {"match_count": 0, "relationship": {"status": "absent"}},
        }
        first = _normalize_download_submenu_snapshot(raw)
        raw["menus"][0]["bounding_box"]["x"] = 99
        second = _normalize_download_submenu_snapshot(raw)
        self.assertEqual(_download_submenu_signature(first), _download_submenu_signature(second))

    def test_menu_close_is_positive_download_action_acceptance(self):
        result = _download_action_acceptance(
            {
                "ui": {
                    "menu_open_before_activation": True,
                    "menu_open_after_activation": False,
                },
                "events": [],
                "cdp": {},
                "staging_files": [],
            }
        )
        self.assertEqual(result["code"], "DOWNLOAD_ACTION_ACCEPTED")
        self.assertIn("menu_closed_or_removed", result["signals"])

    def test_open_menu_without_downstream_activity_is_not_accepted(self):
        result = _download_action_acceptance(
            {
                "ui": {
                    "menu_open_before_activation": True,
                    "menu_open_after_activation": True,
                },
                "events": [{"kind": "download_control_activation", "tag": "button"}],
                "cdp": {},
                "staging_files": [],
            }
        )
        self.assertEqual(result["code"], "DOWNLOAD_ACTION_NOT_ACCEPTED")
        self.assertFalse(result["accepted"])

    def test_relevant_downstream_network_activity_confirms_acceptance(self):
        result = _download_action_acceptance(
            {
                "ui": {
                    "menu_open_before_activation": True,
                    "menu_open_after_activation": True,
                },
                "events": [
                    {
                        "kind": "response",
                        "candidate_bytes": True,
                        "content_type": "image/png",
                    }
                ],
                "cdp": {},
                "staging_files": [],
            }
        )
        self.assertTrue(result["accepted"])
        self.assertIn("relevant_network_activity", result["signals"])

    def test_button_activation_is_not_classified_as_anchor_mechanism(self):
        report = {
            "events": [
                {
                    "kind": "download_control_activation",
                    "tag": "button",
                    "role": "menuitem",
                    "is_trusted": True,
                }
            ],
            "cdp": {},
            "staging_files": [],
        }
        self.assertNotIn("anchor", _observed_download_mechanisms(report))
        report["events"].append({"kind": "anchor_programmatic_click", "tag": "a"})
        self.assertIn("anchor", _observed_download_mechanisms(report))

    def test_download_candidate_uses_1k_child_not_parent_for_cdp_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            image = MagicMock()
            tile = MagicMock()
            download_parent = MagicMock()
            submenu = MagicMock()
            download_1k = MagicMock()
            parent_snapshot = download_control_snapshot(
                aria_haspopup="menu",
                aria_expanded="false",
                has_aria_controls=True,
            )
            final_snapshot = download_control_snapshot(visible_text="1K Original size")
            cdp_result = BrowserDownloadResult(
                candidate_id="00000000-0000-4000-8000-000000000003",
                staged_path=str(destination.resolve()),
                browser_suggested_filename="flow-output.png",
                browser_download_guid="guid-one",
                mechanism="cdp_download_events",
            )
            with patch.object(session, "ensure_production_window_usable"), patch.object(
                session, "_assert_download_context"
            ), patch.object(
                session,
                "_exact_candidate_tile",
                return_value=(
                    "00000000-0000-4000-8000-000000000003",
                    image,
                    tile,
                ),
            ) as exact_candidate, patch.object(
                session,
                "_open_candidate_menu",
                return_value=(download_parent, parent_snapshot),
            ) as open_menu, patch.object(
                session,
                "_expose_download_submenu",
                return_value=(submenu, {"expanded_transition_confirmed": True}),
            ) as expose_submenu, patch.object(
                session,
                "_wait_for_current_1k_original_size_option",
                return_value=(download_1k, final_snapshot),
            ) as exact_1k, patch.object(
                session,
                "_download_with_cdp",
                return_value=cdp_result,
            ) as cdp_download, patch.object(
                session,
                "_restore_candidate_history_view",
            ) as restore_history:
                result = session.download_candidate(
                    candidate=self.download_candidate,
                    destination=destination,
                )
            self.assertEqual(
                result.candidate_id,
                "00000000-0000-4000-8000-000000000003",
            )
            self.assertEqual(result.staged_path, str(destination.resolve()))
            self.assertEqual(result.browser_suggested_filename, "flow-output.png")
            exact_candidate.assert_called_once_with(
                self.download_candidate,
                reveal_virtualized_history=True,
            )
            open_menu.assert_called_once()
            self.assertEqual(open_menu.call_args.args[0], "00000000-0000-4000-8000-000000000003")
            self.assertIs(open_menu.call_args.args[1], image)
            self.assertIs(open_menu.call_args.args[2], tile)
            expose_submenu.assert_called_once()
            self.assertEqual(
                expose_submenu.call_args.args,
                (
                    "00000000-0000-4000-8000-000000000003",
                    download_parent,
                    parent_snapshot,
                ),
            )
            self.assertEqual(
                expose_submenu.call_args.kwargs["diagnostics"],
                {"exposure_attempt": 1, "reacquisition_retry_count": 0},
            )
            exact_1k.assert_called_once_with(
                {"expanded_transition_confirmed": True}
            )
            cdp_download.assert_called_once_with(
                candidate_id="00000000-0000-4000-8000-000000000003",
                download_action=download_1k,
                destination=destination.resolve(),
                prepared_snapshot=final_snapshot,
            )
            restore_history.assert_called_once_with(
                "00000000-0000-4000-8000-000000000003"
            )
            page.expect_download.assert_not_called()

    def test_download_candidate_reacquires_once_after_inert_parent_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            candidate_id = "00000000-0000-4000-8000-000000000003"
            image = MagicMock()
            tile = MagicMock()
            parent = MagicMock()
            snapshot = download_control_snapshot(
                aria_haspopup="menu",
                aria_expanded="false",
                has_aria_controls=False,
            )
            submenu = MagicMock()
            option = MagicMock()
            final_snapshot = download_control_snapshot(visible_text="1K Original size")
            cdp_result = BrowserDownloadResult(
                candidate_id=candidate_id,
                staged_path=str(destination.resolve()),
            )
            exposure_calls = 0

            def expose(_candidate_id, _parent, _snapshot, *, diagnostics):
                nonlocal exposure_calls
                exposure_calls += 1
                if exposure_calls == 1:
                    raise_inert_download_submenu_failure(diagnostics)
                return submenu, diagnostics

            with patch.object(session, "ensure_production_window_usable"), patch.object(
                session, "_assert_download_context"
            ), patch.object(
                session,
                "_exact_candidate_tile",
                return_value=(candidate_id, image, tile),
            ) as exact_candidate, patch.object(
                session,
                "_open_candidate_menu",
                return_value=(parent, snapshot),
            ) as open_menu, patch.object(
                session,
                "_expose_download_submenu",
                side_effect=expose,
            ) as expose_submenu, patch.object(
                session,
                "_wait_for_current_1k_original_size_option",
                return_value=(option, final_snapshot),
            ) as exact_1k, patch.object(
                session,
                "_download_with_cdp",
                return_value=cdp_result,
            ) as cdp_download:
                result = session.download_candidate(
                    candidate=self.download_candidate,
                    destination=destination,
                )

            self.assertIs(result, cdp_result)
            self.assertEqual(exact_candidate.call_count, 2)
            self.assertEqual(open_menu.call_count, 2)
            self.assertEqual(expose_submenu.call_count, 2)
            self.assertEqual(
                expose_submenu.call_args.kwargs["diagnostics"]["exposure_attempt"],
                2,
            )
            exact_1k.assert_called_once()
            cdp_download.assert_called_once()

    def test_download_candidate_surfaces_failure_after_bounded_inert_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            candidate_id = "00000000-0000-4000-8000-000000000003"
            parent = MagicMock()
            snapshot = download_control_snapshot(
                aria_haspopup="menu",
                aria_expanded="false",
                has_aria_controls=False,
            )

            def fail(_candidate_id, _parent, _snapshot, *, diagnostics):
                raise_inert_download_submenu_failure(diagnostics)

            with patch.object(session, "ensure_production_window_usable"), patch.object(
                session, "_assert_download_context"
            ), patch.object(
                session,
                "_exact_candidate_tile",
                return_value=(candidate_id, MagicMock(), MagicMock()),
            ) as exact_candidate, patch.object(
                session,
                "_open_candidate_menu",
                return_value=(parent, snapshot),
            ) as open_menu, patch.object(
                session,
                "_expose_download_submenu",
                side_effect=fail,
            ) as expose_submenu, patch.object(
                session,
                "_wait_for_current_1k_original_size_option",
            ) as exact_1k, patch.object(
                session,
                "_download_with_cdp",
            ) as cdp_download, self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_SUBMENU_NOT_FOUND",
            ):
                session.download_candidate(
                    candidate=self.download_candidate,
                    destination=destination,
                )

            self.assertEqual(exact_candidate.call_count, 2)
            self.assertEqual(open_menu.call_count, 2)
            self.assertEqual(expose_submenu.call_count, 2)
            exact_1k.assert_not_called()
            cdp_download.assert_not_called()

    def test_download_candidate_uses_fresh_candidate_nodes_after_retry_remount(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            session.page = MagicMock()
            candidate_id = "00000000-0000-4000-8000-000000000003"
            stale_image, stale_tile, stale_parent = MagicMock(), MagicMock(), MagicMock()
            fresh_image, fresh_tile, fresh_parent = MagicMock(), MagicMock(), MagicMock()
            snapshot = download_control_snapshot(
                aria_haspopup="menu",
                aria_expanded="false",
                has_aria_controls=False,
            )
            submenu = MagicMock()
            option = MagicMock()
            final_snapshot = download_control_snapshot(visible_text="1K Original size")

            def expose(_candidate_id, parent, _snapshot, *, diagnostics):
                if parent is stale_parent:
                    raise_inert_download_submenu_failure(diagnostics)
                self.assertIs(parent, fresh_parent)
                return submenu, diagnostics

            with patch.object(session, "ensure_production_window_usable"), patch.object(
                session, "_assert_download_context"
            ), patch.object(
                session,
                "_exact_candidate_tile",
                side_effect=[
                    (candidate_id, stale_image, stale_tile),
                    (candidate_id, fresh_image, fresh_tile),
                ],
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=[(stale_parent, snapshot), (fresh_parent, snapshot)],
            ) as open_menu, patch.object(
                session,
                "_expose_download_submenu",
                side_effect=expose,
            ), patch.object(
                session,
                "_wait_for_current_1k_original_size_option",
                return_value=(option, final_snapshot),
            ), patch.object(
                session,
                "_download_with_cdp",
                return_value=BrowserDownloadResult(
                    candidate_id=candidate_id,
                    staged_path=str(destination.resolve()),
                ),
            ):
                session.download_candidate(
                    candidate=self.download_candidate,
                    destination=destination,
                )

            self.assertIs(open_menu.call_args_list[0].args[1], stale_image)
            self.assertIs(open_menu.call_args_list[0].args[2], stale_tile)
            self.assertIs(open_menu.call_args_list[1].args[1], fresh_image)
            self.assertIs(open_menu.call_args_list[1].args[2], fresh_tile)

    def test_download_submenu_retry_introduces_no_unsafe_input_path(self):
        source = inspect.getsource(FlowSession.download_candidate)

        self.assertNotIn("force=", source)
        self.assertNotIn(".evaluate(", source)
        self.assertNotIn("bring_to_front", source)
        self.assertNotIn("bringToFront", source)
        self.assertNotIn(".click(", source)

    def test_latency_path_has_no_variant_or_window_state_branch(self):
        source = "\n".join(
            (
                inspect.getsource(FlowSession._reveal_candidate_options),
                inspect.getsource(FlowSession._expose_download_submenu),
                inspect.getsource(FlowSession._wait_for_current_1k_original_size_option),
            )
        )

        self.assertNotIn("variants", source)
        self.assertNotIn("minimized", source)
        self.assertNotIn("bring_to_front", source)
        self.assertNotIn("bringToFront", source)

    def test_open_download_parent_resolves_explicit_aria_controls_submenu(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "aria-haspopup": "menu",
            "aria-expanded": "true",
            "aria-controls": "download-submenu",
        }.get(name)
        parent.evaluate.return_value = {
            "match_count": 1,
            "connected_count": 1,
            "visible_count": 1,
        }
        parent_menu = MagicMock()
        parent_menu.get_attribute.return_value = "parent-menu"
        submenu = MagicMock()
        submenu.get_attribute.return_value = "download-submenu"
        page.get_by_role.return_value.all.return_value = [parent_menu, submenu]

        def live(control):
            self.assertIn(control, {parent_menu, submenu})
            return {"connected": True, "visible": True, "enabled": True}

        with patch.object(session, "_live_control_facts", side_effect=live):
            matches, facts = session._current_associated_download_submenu(parent)

        self.assertEqual(matches, [submenu])
        self.assertEqual(facts["aria_haspopup"], "menu")
        self.assertEqual(facts["aria_expanded"], "true")
        self.assertTrue(facts["has_aria_controls"])
        self.assertEqual(facts["relationship_basis"], "aria_controls")
        self.assertEqual(facts["live_menu_count"], 2)
        self.assertEqual(facts["related_menu_count"], 1)
        self.assertEqual(facts["aria_controls"], "download-submenu")
        self.assertTrue(facts["controlled_panel_found"])
        self.assertTrue(facts["controlled_panel_connected"])
        self.assertTrue(facts["controlled_panel_visible"])

    def test_aria_controls_wrapper_can_contain_current_role_menu(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "aria-haspopup": "menu",
            "aria-expanded": "true",
            "aria-controls": "mat-menu-panel-208",
        }.get(name)
        parent.evaluate.return_value = {
            "match_count": 1,
            "connected_count": 1,
            "visible_count": 1,
        }
        submenu = MagicMock()
        submenu.get_attribute.return_value = None
        submenu.evaluate.return_value = {"related": True}
        option = MagicMock()
        submenu.get_by_role.return_value.all.return_value = [option]
        page.get_by_role.return_value.all.return_value = [submenu]

        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ):
            matches, facts = session._current_associated_download_submenu(parent)

        self.assertEqual(matches, [submenu])
        self.assertEqual(facts["aria_controls"], "mat-menu-panel-208")
        self.assertEqual(facts["controlled_role_menu_count"], 1)
        self.assertEqual(facts["visible_1k_in_controlled_panel"], 1)

    def test_closed_download_parent_without_aria_controls_is_valid_pre_open_state(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "aria-haspopup": "menu",
            "aria-expanded": "false",
            "aria-controls": None,
        }.get(name)
        parent_menu = MagicMock()
        parent_menu.get_attribute.return_value = "parent-menu"
        parent_menu.get_by_role.return_value.all.return_value = []
        page.get_by_role.return_value.all.return_value = [parent_menu]
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ):
            matches, facts = session._current_associated_download_submenu(parent)

        self.assertEqual(matches, [])
        self.assertEqual(facts["aria_expanded"], "false")
        self.assertFalse(facts["has_aria_controls"])
        self.assertIsNone(facts["relationship_basis"])

    def test_stable_parent_hit_uses_mouse_move_and_never_locator_hover(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        current_box = {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0}
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=parent_snapshot,
        ), patch.object(
            session,
            "_current_download_control_hit",
            return_value=current_box,
        ) as hit_test, patch.object(
            session,
            "_wait_for_associated_download_submenu",
            return_value=submenu,
        ) as wait, patch.object(
            session, "_activate_download_control"
        ) as activate:
            result, diagnostics = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertIs(result, submenu)
        parent.hover.assert_not_called()
        session.page.mouse.move.assert_called_once_with(170.1, 212.0)
        hit_test.assert_called_once()
        wait.assert_called_once()
        activate.assert_not_called()
        self.assertEqual(diagnostics["parent_activation_count"], 0)
        self.assertEqual(diagnostics["hover_method"], "page_mouse_move")
        self.assertTrue(diagnostics["parent_stable"])
        self.assertTrue(diagnostics["parent_hit_tested"])
        self.assertFalse(diagnostics["has_aria_controls_before"])

    def test_before_hover_handoff_records_all_passing_production_gates(self):
        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )

        self.assertEqual(snapshot["decision"], "accepted_for_handoff")
        self.assertTrue(snapshot["relationship_unchanged"])
        self.assertEqual(snapshot["parent"]["id"], "download-parent")
        self.assertTrue(snapshot["parent"]["connected"])
        self.assertTrue(snapshot["parent"]["visible"])
        self.assertTrue(snapshot["parent"]["hover"])
        self.assertEqual(
            snapshot["controlled_target"]["identity"]["bounding_box"],
            {"x": 960.0, "y": 280.0, "width": 180.0, "height": 144.0},
        )
        self.assertTrue(snapshot["menu_validation"]["correlation_unique"])
        self.assertFalse(snapshot["menu_validation"]["ambiguity_detected"])
        self.assertTrue(
            all(gate["passed"] for gate in snapshot["acceptance_gates"])
        )

    def test_before_hover_handoff_records_hidden_target_gate(self):
        raw = before_hover_handoff_raw(
            controlled_target_visible_count=0,
            controlled_role_menu_visible_count=0,
            handoff_point=None,
            handoff_hit_owned=False,
            handoff_hit_target=None,
        )
        raw["controlled_target"] = {
            **raw["controlled_target"],
            "visible": False,
        }
        raw["menu_candidates"] = [raw["controlled_target"]]
        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )
        gates = {gate["name"]: gate for gate in snapshot["acceptance_gates"]}

        self.assertEqual(snapshot["decision"], "controlled_target_hidden")
        self.assertFalse(gates["controlled_target_visible_unique"]["passed"])
        self.assertEqual(
            gates["controlled_target_visible_unique"]["reason"],
            "exact controlled target fails the production visibility gate",
        )

    def test_before_hover_handoff_records_missing_role_menu_gate(self):
        raw = before_hover_handoff_raw(
            controlled_role_menu_count=0,
            controlled_role_menu_connected_count=0,
            controlled_role_menu_visible_count=0,
            menu_candidates=[],
            handoff_point=None,
            handoff_hit_owned=False,
            handoff_hit_target=None,
        )
        raw["controlled_target"] = {
            **raw["controlled_target"],
            "role": "presentation",
        }
        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )
        gates = {gate["name"]: gate for gate in snapshot["acceptance_gates"]}

        self.assertEqual(snapshot["decision"], "no_valid_menu")
        self.assertFalse(gates["controlled_role_menu_unique"]["passed"])
        self.assertEqual(
            gates["controlled_role_menu_unique"]["reason"],
            "controlled role=menu is missing or duplicated",
        )

    def test_before_hover_handoff_records_multiple_menu_ambiguity(self):
        raw = before_hover_handoff_raw(
            controlled_role_menu_count=2,
            controlled_role_menu_connected_count=2,
            controlled_role_menu_visible_count=2,
            handoff_point=None,
            handoff_hit_owned=False,
            handoff_hit_target=None,
        )
        raw["menu_candidates"] = [
            raw["controlled_target"],
            {
                **raw["controlled_target"],
                "id": "mat-menu-panel-665-nested",
            },
        ]
        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )

        self.assertEqual(snapshot["decision"], "multiple_valid_menus")
        self.assertTrue(snapshot["menu_validation"]["ambiguity_detected"])
        self.assertEqual(
            snapshot["menu_validation"]["structural_candidate_count"],
            2,
        )

    def test_before_hover_handoff_records_relationship_change(self):
        raw = before_hover_handoff_raw(
            current_aria_controls="mat-menu-panel-666",
        )
        raw["parent"] = {
            **raw["parent"],
            "aria_controls": "mat-menu-panel-666",
        }
        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )

        self.assertEqual(snapshot["decision"], "relationship_changed")
        self.assertFalse(snapshot["relationship_unchanged"])
        self.assertEqual(snapshot["expected_controls_id"], "mat-menu-panel-665")
        self.assertEqual(snapshot["current_aria_controls"], "mat-menu-panel-666")

    def test_before_hover_handoff_decision_timeline_is_bounded(self):
        diagnostics = {}
        decision = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )

        for _index in range(10):
            FlowSession._record_before_hover_handoff_decision(
                diagnostics,
                decision,
            )

        timeline = diagnostics["before_hover_handoff"]
        self.assertEqual(timeline["observation_count"], 10)
        self.assertEqual(len(timeline["decisions"]), 4)
        self.assertTrue(timeline["truncated"])
        self.assertEqual(len(timeline["visibility_timeline"]), 8)
        self.assertEqual(
            timeline["visibility_timeline_stop_reason"],
            "maximum_observations",
        )
        self.assertTrue(timeline["visibility_timeline_truncated"])

    def test_probe_visibility_timeline_records_opacity_transition(self):
        diagnostics = {}

        for opacity in ("0", "0.005", "0.5", "1"):
            raw = before_hover_handoff_raw(
                controlled_target_visible_count=int(float(opacity) > 0.01),
                controlled_role_menu_visible_count=int(float(opacity) > 0.01),
            )
            raw["visibility_observation"]["controlled_target"] = (
                visibility_component_raw(opacity=opacity)
            )
            raw["visibility_observation"]["role_menu"] = visibility_component_raw(
                opacity=opacity
            )
            decision = FlowSession._normalize_before_hover_handoff_decision(
                raw,
                expected_controls_id="mat-menu-panel-665",
            )
            decision["visibility_observation"]["source"] = "handoff_decision"
            FlowSession._record_before_hover_handoff_decision(diagnostics, decision)

        timeline = diagnostics["before_hover_handoff"]
        observations = timeline["visibility_timeline"]
        self.assertEqual(timeline["visibility_timeline_max_observations"], 8)
        self.assertEqual(
            [item["controlled_target"]["computed_css"]["opacity"] for item in observations],
            ["0", "0.005", "0.5", "1"],
        )
        self.assertEqual(
            [item["controlled_target"]["production_visible"] for item in observations],
            [False, False, True, True],
        )
        self.assertEqual(
            [item["controlled_target"]["clauses"]["opacity_above_threshold"] for item in observations],
            [False, False, True, True],
        )
        self.assertTrue(
            all(
                observations[index]["offset_ms"]
                <= observations[index + 1]["offset_ms"]
                for index in range(len(observations) - 1)
            )
        )

    def test_probe_visibility_timeline_records_visibility_hidden_then_visible(self):
        diagnostics = {}

        for visibility in ("hidden", "visible"):
            raw = before_hover_handoff_raw(
                controlled_target_visible_count=int(visibility == "visible"),
                controlled_role_menu_visible_count=int(visibility == "visible"),
            )
            raw["visibility_observation"]["controlled_target"] = (
                visibility_component_raw(visibility=visibility)
            )
            raw["visibility_observation"]["role_menu"] = visibility_component_raw(
                visibility=visibility
            )
            decision = FlowSession._normalize_before_hover_handoff_decision(
                raw,
                expected_controls_id="mat-menu-panel-665",
            )
            FlowSession._record_before_hover_handoff_decision(diagnostics, decision)

        observations = diagnostics["before_hover_handoff"]["visibility_timeline"]
        self.assertEqual(
            [item["controlled_target"]["clauses"]["visibility_not_hidden"] for item in observations],
            [False, True],
        )
        self.assertEqual(
            [item["controlled_target"]["production_visible"] for item in observations],
            [False, True],
        )

    def test_probe_visibility_timeline_records_display_none(self):
        raw = before_hover_handoff_raw(
            controlled_target_visible_count=0,
            controlled_role_menu_visible_count=0,
        )
        raw["visibility_observation"]["controlled_target"] = visibility_component_raw(
            display="none"
        )
        raw["visibility_observation"]["role_menu"] = visibility_component_raw(
            display="none"
        )

        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )["visibility_observation"]

        self.assertFalse(snapshot["controlled_target"]["clauses"]["display_not_none"])
        self.assertFalse(snapshot["controlled_target"]["production_visible"])

    def test_probe_visibility_timeline_records_unparseable_opacity(self):
        raw = before_hover_handoff_raw(
            controlled_target_visible_count=0,
            controlled_role_menu_visible_count=0,
        )
        raw["visibility_observation"]["controlled_target"] = visibility_component_raw(
            opacity="not-a-number"
        )
        raw["visibility_observation"]["role_menu"] = visibility_component_raw(
            opacity="not-a-number"
        )

        snapshot = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )["visibility_observation"]["controlled_target"]

        self.assertFalse(snapshot["clauses"]["opacity_parseable"])
        self.assertFalse(snapshot["clauses"]["opacity_above_threshold"])
        self.assertFalse(snapshot["production_visible"])

    def test_probe_visibility_positive_geometry_does_not_override_low_opacity(self):
        raw = before_hover_handoff_raw(
            controlled_target_visible_count=0,
            controlled_role_menu_visible_count=0,
        )
        raw["visibility_observation"]["controlled_target"] = visibility_component_raw(
            opacity="0.003"
        )
        raw["visibility_observation"]["role_menu"] = visibility_component_raw(
            opacity="0.003"
        )

        target = FlowSession._normalize_before_hover_handoff_decision(
            raw,
            expected_controls_id="mat-menu-panel-665",
        )["visibility_observation"]["controlled_target"]

        self.assertTrue(target["clauses"]["positive_width"])
        self.assertTrue(target["clauses"]["positive_height"])
        self.assertFalse(target["clauses"]["opacity_above_threshold"])
        self.assertFalse(target["production_visible"])

    def test_probe_visibility_all_clauses_pass(self):
        target = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )["visibility_observation"]["controlled_target"]

        self.assertTrue(all(target["clauses"].values()))
        self.assertTrue(target["production_visible"])
        self.assertEqual(target["computed_css"]["pointer_events"], "auto")

    def test_probe_visibility_timeline_stops_when_relationship_changes(self):
        diagnostics = {}
        initial = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )
        FlowSession._record_before_hover_handoff_decision(diagnostics, initial)
        changed_raw = before_hover_handoff_raw(current_aria_controls="mat-menu-panel-666")
        changed_raw["parent"] = {
            **changed_raw["parent"],
            "aria_controls": "mat-menu-panel-666",
        }
        changed_raw["visibility_observation"]["parent"] = {
            **changed_raw["visibility_observation"]["parent"],
            "aria_controls": "mat-menu-panel-666",
        }
        changed = FlowSession._normalize_before_hover_handoff_decision(
            changed_raw,
            expected_controls_id="mat-menu-panel-665",
        )
        FlowSession._record_before_hover_handoff_decision(diagnostics, changed)
        FlowSession._record_before_hover_handoff_decision(diagnostics, initial)

        timeline = diagnostics["before_hover_handoff"]
        self.assertEqual(len(timeline["visibility_timeline"]), 2)
        self.assertEqual(
            timeline["visibility_timeline_stop_reason"],
            "relationship_changed",
        )
        self.assertFalse(timeline["visibility_timeline"][1]["relationship_unchanged"])

    def test_probe_visibility_timeline_stops_when_target_disappears(self):
        diagnostics = {}
        initial = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )
        FlowSession._record_before_hover_handoff_decision(diagnostics, initial)
        missing_raw = before_hover_handoff_raw(
            controlled_target_count=0,
            controlled_target_connected_count=0,
            controlled_target_visible_count=0,
            controlled_target=None,
            controlled_role_menu_count=0,
            controlled_role_menu_connected_count=0,
            controlled_role_menu_visible_count=0,
            menu_candidates=[],
            handoff_point=None,
            handoff_hit_owned=False,
            handoff_hit_target=None,
        )
        missing_raw["visibility_observation"]["controlled_target"] = {
            "is_element": False
        }
        missing_raw["visibility_observation"]["role_menu"] = {"is_element": False}
        missing_raw["visibility_observation"]["hit_coordinate"] = None
        missing_raw["visibility_observation"]["hit_target"] = None
        missing_raw["visibility_observation"]["hit_inside_controlled_target"] = False
        missing_raw["visibility_observation"]["hit_inside_role_menu"] = False
        missing = FlowSession._normalize_before_hover_handoff_decision(
            missing_raw,
            expected_controls_id="mat-menu-panel-665",
        )
        FlowSession._record_before_hover_handoff_decision(diagnostics, missing)
        FlowSession._record_before_hover_handoff_decision(diagnostics, initial)

        timeline = diagnostics["before_hover_handoff"]
        self.assertEqual(len(timeline["visibility_timeline"]), 2)
        self.assertEqual(timeline["visibility_timeline_stop_reason"], "target_disappeared")
        self.assertFalse(
            timeline["visibility_timeline"][1]["controlled_target"]["clauses"][
                "is_element"
            ]
        )

    def test_probe_visibility_hit_test_is_passive_and_records_ownership(self):
        observation = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(),
            expected_controls_id="mat-menu-panel-665",
        )["visibility_observation"]
        source = inspect.getsource(FlowSession._capture_before_hover_handoff_decision)

        self.assertEqual(observation["hit_test"]["coordinate"], {"x": 1050.0, "y": 352.0})
        self.assertEqual(observation["hit_test"]["target"]["id"], "one-k-item")
        self.assertTrue(observation["hit_test"]["inside_controlled_target"])
        self.assertTrue(observation["hit_test"]["inside_role_menu"])
        self.assertIn("elementFromPoint", source)
        for unsafe in (
            "mouse.move",
            "mouse.down",
            "mouse.up",
            ".click(",
            "dispatchEvent",
            "bringToFront",
            ".focus(",
            "wait_for_timeout",
        ):
            self.assertNotIn(unsafe, source)

    def test_probe_visibility_observer_is_inert_without_probe_flag_and_has_no_wait(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        parent = MagicMock()
        diagnostics = {}

        with patch.object(
            session,
            "_capture_before_hover_handoff_decision",
        ) as capture:
            session._observe_before_hover_handoff_visibility(
                parent,
                diagnostics,
                expanded="true",
                controls_id="mat-menu-panel-665",
                source="associated_submenu_recheck",
            )

        capture.assert_not_called()
        self.assertNotIn("before_hover_handoff", diagnostics)
        source = "\n".join(
            inspect.getsource(method)
            for method in (
                FlowSession._observe_before_hover_handoff_visibility,
                FlowSession._record_before_hover_handoff_decision,
                FlowSession._capture_before_hover_handoff_decision,
            )
        )
        self.assertNotIn("wait_for_timeout", source)
        self.assertNotIn("time.sleep", source)

    def test_after_hover_handoff_snapshot_records_exact_panel_geometry_hover_and_1k(self):
        snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
            after_hover_handoff_raw(),
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 976.0, "y": 330.0},
        )

        self.assertEqual(snapshot["outcome"], "handoff_succeeded")
        self.assertEqual(snapshot["expected_panel_id"], "mat-menu-panel-665")
        self.assertTrue(snapshot["same_controlled_panel"])
        self.assertEqual(snapshot["handoff_coordinate"], {"x": 976.0, "y": 330.0})
        self.assertEqual(snapshot["viewport"], {"width": 1536, "height": 791})
        self.assertEqual(snapshot["panel"]["id"], "mat-menu-panel-665")
        self.assertEqual(snapshot["panel"]["role"], "menu")
        self.assertEqual(
            snapshot["panel"]["bounding_box"],
            {"x": 960.0, "y": 280.0, "width": 180.0, "height": 144.0},
        )
        self.assertTrue(snapshot["panel"]["connected"])
        self.assertTrue(snapshot["panel"]["visible"])
        self.assertTrue(snapshot["panel"]["hover"])
        self.assertTrue(snapshot["hit_owned_by_panel"])
        self.assertTrue(snapshot["pointer_inside_panel"])
        self.assertEqual(snapshot["one_k"]["exact_match_count"], 1)
        self.assertEqual(snapshot["one_k"]["visible_match_count"], 1)
        self.assertEqual(snapshot["one_k"]["enabled_match_count"], 1)
        self.assertTrue(snapshot["one_k"]["belongs_to_controlled_panel"])

    def test_after_hover_handoff_snapshot_classifies_pointer_miss(self):
        raw = after_hover_handoff_raw(hit_owned_by_panel=False)
        raw["panel"] = {**raw["panel"], "hover": False}

        snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
            raw,
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 940.0, "y": 260.0},
        )

        self.assertEqual(snapshot["outcome"], "pointer_not_inside_panel")
        self.assertFalse(snapshot["hit_owned_by_panel"])
        self.assertFalse(snapshot["pointer_inside_panel"])

    def test_after_hover_handoff_snapshot_classifies_missing_or_hidden_panel(self):
        disappeared = after_hover_handoff_raw(
            controlled_target_count=0,
            controlled_role_menu_count=0,
            panel=None,
            hit_target=None,
            hit_owned_by_panel=False,
            one_k_exact_match_count=0,
            one_k_visible_match_count=0,
            one_k_enabled_match_count=0,
            one_k_visible_enabled_match_count=0,
            one_k_belongs_to_controlled_panel=False,
        )
        hidden = after_hover_handoff_raw()
        hidden["panel"] = {**hidden["panel"], "visible": False, "hover": False}

        for raw, expected in (
            (disappeared, "panel_disconnected"),
            (hidden, "panel_not_visible"),
        ):
            with self.subTest(expected=expected):
                snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
                    raw,
                    expected_panel_id="mat-menu-panel-665",
                    handoff_coordinate={"x": 976.0, "y": 330.0},
                )
                self.assertEqual(snapshot["outcome"], expected)

    def test_after_hover_handoff_snapshot_classifies_panel_identity_change(self):
        snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
            after_hover_handoff_raw(
                current_aria_controls="mat-menu-panel-666",
            ),
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 976.0, "y": 330.0},
        )

        self.assertEqual(snapshot["outcome"], "panel_identity_changed")
        self.assertFalse(snapshot["same_controlled_panel"])

    def test_after_hover_handoff_snapshot_records_absent_exact_1k_without_activation(self):
        snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
            after_hover_handoff_raw(
                one_k_exact_match_count=0,
                one_k_visible_match_count=0,
                one_k_enabled_match_count=0,
                one_k_visible_enabled_match_count=0,
                one_k_belongs_to_controlled_panel=False,
            ),
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 976.0, "y": 330.0},
        )

        self.assertEqual(snapshot["outcome"], "exact_1k_not_present")
        self.assertEqual(snapshot["one_k"]["exact_match_count"], 0)
        self.assertEqual(snapshot["one_k"]["visible_match_count"], 0)
        self.assertEqual(snapshot["one_k"]["enabled_match_count"], 0)

    def test_probe_captures_after_hover_handoff_immediately_after_mouse_move(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        submenu = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(
            handoff_point={"x": 976.0, "y": 330.0},
        )
        relationship = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": "aria_controls",
        }
        snapshot = FlowSession._normalize_after_hover_handoff_snapshot(
            after_hover_handoff_raw(),
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 976.0, "y": 330.0},
        )
        before_snapshot = FlowSession._normalize_before_hover_handoff_decision(
            before_hover_handoff_raw(controlled_target_visible_count=0),
            expected_controls_id="mat-menu-panel-665",
        )
        sequence = []
        page.mouse.move.side_effect = lambda *_args: sequence.append("mouse_move")

        def capture_before(*_args, **_kwargs):
            sequence.append("before_snapshot")
            return before_snapshot

        def capture(*_args, **_kwargs):
            sequence.append("after_snapshot")
            return snapshot

        def verify(*_args, **_kwargs):
            sequence.append("revalidate")
            return [submenu], relationship

        diagnostics = {"_passive_parent_trace_enabled": True}
        with patch.object(
            session,
            "_capture_before_hover_handoff_decision",
            side_effect=capture_before,
        ) as capture_before_snapshot, patch.object(
            session,
            "_capture_after_hover_handoff_snapshot",
            side_effect=capture,
        ) as capture_snapshot, patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=verify,
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )

        self.assertIs(result, submenu)
        self.assertEqual(
            sequence,
            ["before_snapshot", "mouse_move", "after_snapshot", "revalidate"],
        )
        capture_before_snapshot.assert_called_once_with(
            parent,
            expected_controls_id="mat-menu-panel-665",
        )
        capture_snapshot.assert_called_once_with(
            parent,
            expected_panel_id="mat-menu-panel-665",
            handoff_coordinate={"x": 976.0, "y": 330.0},
        )
        self.assertEqual(
            diagnostics["before_hover_handoff"]["decisions"][0]["decision"],
            "controlled_target_hidden",
        )
        self.assertIs(diagnostics["after_hover_handoff"], snapshot)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        parent.click.assert_not_called()

        sample = {"submenu_polls": 0, "download_parent_remounted": False}
        session._copy_download_ui_probe_submenu_diagnostics(
            sample,
            {},
            diagnostics,
        )
        self.assertIs(
            sample["before_hover_handoff"],
            diagnostics["before_hover_handoff"],
        )
        self.assertEqual(
            sample["before_hover_handoff"]["visibility_timeline_stop_reason"],
            "exposure_loop_ended",
        )
        self.assertIs(sample["after_hover_handoff"], snapshot)

    def test_production_handoff_does_not_capture_probe_snapshot(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(
            handoff_point={"x": 976.0, "y": 330.0},
        )
        relationship = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": "aria_controls",
        }
        diagnostics = {}

        with patch.object(
            session,
            "_capture_before_hover_handoff_decision",
        ) as capture_before_snapshot, patch.object(
            session,
            "_capture_after_hover_handoff_snapshot",
        ) as capture_snapshot, patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], relationship),
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )

        self.assertIs(result, submenu)
        capture_before_snapshot.assert_not_called()
        capture_snapshot.assert_not_called()
        self.assertNotIn("before_hover_handoff", diagnostics)
        self.assertNotIn("after_hover_handoff", diagnostics)
        production_source = inspect.getsource(FlowSession.download_candidate)
        self.assertNotIn("before_hover_handoff", production_source)
        self.assertNotIn("_capture_before_hover_handoff_decision", production_source)
        self.assertNotIn("after_hover_handoff", production_source)
        self.assertNotIn("_capture_after_hover_handoff_snapshot", production_source)

    def test_previsible_opacity_zero_exact_owned_panel_allows_physical_handoff(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        submenu = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(opacity="0")
        strict_relationship = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": "aria_controls",
        }

        with patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], strict_relationship),
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                {},
            )

        self.assertIs(result, submenu)
        page.mouse.move.assert_called_once_with(1050.0, 352.0)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

        diagnostics = {}
        with patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], strict_relationship),
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )
        self.assertIs(result, submenu)
        self.assertFalse(diagnostics["strict_production_visible_before_handoff"])
        self.assertTrue(diagnostics["handoff_enterable"])
        self.assertTrue(
            diagnostics["handoff_opacity_only_strict_visibility_difference"]
        )
        self.assertTrue(diagnostics["handoff_hit_owned"])
        self.assertTrue(diagnostics["hover_controlled_handoff_attempted"])
        self.assertTrue(
            diagnostics["hover_controlled_handoff_post_move_revalidation"]["passed"]
        )
        sample = {"submenu_polls": 0, "download_parent_remounted": False}
        session._copy_download_ui_probe_submenu_diagnostics(
            sample,
            {},
            diagnostics,
        )
        self.assertFalse(sample["strict_production_visible_before_handoff"])
        self.assertTrue(sample["handoff_enterable"])
        self.assertTrue(sample["handoff_opacity_only_strict_visibility_difference"])
        self.assertTrue(sample["hover_controlled_handoff_attempted"])
        self.assertTrue(
            sample["hover_controlled_handoff_post_move_revalidation"]["passed"]
        )

        before_raw = before_hover_handoff_raw(
            controlled_target_visible_count=0,
            controlled_role_menu_visible_count=0,
            handoff_point=None,
            handoff_hit_owned=False,
            handoff_hit_target=None,
        )
        before_raw["visibility_observation"]["controlled_target"] = (
            visibility_component_raw(opacity="0")
        )
        before_raw["visibility_observation"]["role_menu"] = (
            visibility_component_raw(opacity="0")
        )
        before = FlowSession._normalize_before_hover_handoff_decision(
            before_raw,
            expected_controls_id="mat-menu-panel-665",
        )
        self.assertEqual(before["decision"], "accepted_for_previsible_handoff")
        self.assertFalse(before["strict_production_visible_before_handoff"])
        self.assertTrue(before["handoff_enterable"])
        self.assertTrue(before["opacity_only_strict_visibility_difference"])

    def test_previsible_opacity_zero_unowned_hit_fails_before_mouse_move(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(
            opacity="0",
            handoff_hit_owned=False,
            handoff_point=None,
        )

        result = session._handoff_hover_opened_download_submenu(parent, {})

        self.assertIsNone(result)
        session.page.mouse.move.assert_not_called()

    def test_previsible_opacity_zero_display_none_fails_before_mouse_move(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        raw = hover_handoff_target_raw(opacity="0")
        raw["controlled_target_facts"] = visibility_component_raw(
            display="none",
            opacity="0",
        )
        raw["controlled_role_menu_facts"] = visibility_component_raw(
            display="none",
            opacity="0",
        )
        parent.evaluate.return_value = raw

        result = session._handoff_hover_opened_download_submenu(parent, {})

        self.assertIsNone(result)
        session.page.mouse.move.assert_not_called()

    def test_previsible_opacity_zero_visibility_hidden_fails_before_mouse_move(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        raw = hover_handoff_target_raw(opacity="0")
        raw["controlled_target_facts"] = visibility_component_raw(
            visibility="hidden",
            opacity="0",
        )
        raw["controlled_role_menu_facts"] = visibility_component_raw(
            visibility="hidden",
            opacity="0",
        )
        parent.evaluate.return_value = raw

        result = session._handoff_hover_opened_download_submenu(parent, {})

        self.assertIsNone(result)
        session.page.mouse.move.assert_not_called()

    def test_previsible_opacity_zero_disconnected_panel_fails_before_mouse_move(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        raw = hover_handoff_target_raw(
            opacity="0",
            controlled_target_connected_count=0,
            controlled_role_menu_connected_count=0,
        )
        raw["controlled_target_facts"] = visibility_component_raw(
            connected=False,
            opacity="0",
        )
        raw["controlled_role_menu_facts"] = visibility_component_raw(
            connected=False,
            opacity="0",
        )
        parent.evaluate.return_value = raw

        result = session._handoff_hover_opened_download_submenu(parent, {})

        self.assertIsNone(result)
        session.page.mouse.move.assert_not_called()

    def test_previsible_requires_parseable_opacity_and_pointer_events(self):
        for field, value in (
            ("opacity", "not-a-number"),
            ("pointer_events", "none"),
        ):
            with self.subTest(field=field):
                session = FlowSession(PROJECT_URL, Path("profile"))
                session.page = MagicMock()
                parent = MagicMock()
                raw = hover_handoff_target_raw(opacity="0")
                component_overrides = {"opacity": "0", field: value}
                raw["controlled_target_facts"] = visibility_component_raw(
                    **component_overrides
                )
                raw["controlled_role_menu_facts"] = visibility_component_raw(
                    **component_overrides
                )
                parent.evaluate.return_value = raw

                result = session._handoff_hover_opened_download_submenu(parent, {})

                self.assertIsNone(result)
                session.page.mouse.move.assert_not_called()

    def test_previsible_duplicate_target_or_menu_fails_closed(self):
        for duplicate_key in (
            "controlled_target_count",
            "controlled_role_menu_count",
        ):
            with self.subTest(duplicate_key=duplicate_key):
                session = FlowSession(PROJECT_URL, Path("profile"))
                session.page = MagicMock()
                parent = MagicMock()
                raw = hover_handoff_target_raw(opacity="0")
                raw[duplicate_key] = 2
                parent.evaluate.return_value = raw

                with self.assertRaisesRegex(
                    FlowAutomationError,
                    "DOWNLOAD_SUBMENU_AMBIGUOUS",
                ):
                    session._handoff_hover_opened_download_submenu(parent, {})

                session.page.mouse.move.assert_not_called()

    def test_previsible_relationship_change_before_move_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        initial = hover_handoff_target_raw(opacity="0")
        changed = hover_handoff_target_raw(
            panel_id="mat-menu-panel-666",
            opacity="0",
        )
        parent.evaluate.side_effect = [initial, changed]
        diagnostics = {}

        result = session._handoff_hover_opened_download_submenu(parent, diagnostics)

        self.assertIsNone(result)
        session.page.mouse.move.assert_not_called()
        self.assertFalse(
            diagnostics["hover_controlled_handoff_pre_move_revalidation"]["passed"]
        )
        self.assertTrue(diagnostics["hover_controlled_handoff_lost"])

    def test_previsible_relationship_change_after_move_fails_before_strict_readiness(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        initial = hover_handoff_target_raw(opacity="0")
        changed = hover_handoff_target_raw(
            panel_id="mat-menu-panel-666",
            opacity="0",
        )
        parent.evaluate.side_effect = [initial, initial, changed]
        diagnostics = {}

        with patch.object(
            session,
            "_current_associated_download_submenu",
        ) as strict_readiness:
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )

        self.assertIsNone(result)
        session.page.mouse.move.assert_called_once_with(1050.0, 352.0)
        strict_readiness.assert_not_called()
        self.assertFalse(
            diagnostics["hover_controlled_handoff_post_move_revalidation"]["passed"]
        )
        self.assertTrue(diagnostics["hover_controlled_handoff_lost"])

    def test_previsible_handoff_does_not_imply_strict_submenu_or_1k_readiness(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(opacity="0")
        still_previsible = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": None,
            "controlled_panel_visible": False,
        }

        with patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([], still_previsible),
        ), patch.object(
            session,
            "_exact_1k_original_size_option",
        ) as exact_1k, patch.object(
            session,
            "_activate_download_control",
        ) as activate:
            result = session._handoff_hover_opened_download_submenu(parent, {})

        self.assertIsNone(result)
        session.page.mouse.move.assert_called_once_with(1050.0, 352.0)
        exact_1k.assert_not_called()
        activate.assert_not_called()

    def test_previsible_exception_is_local_and_strict_visibility_paths_remain(self):
        previsible = FlowSession._normalize_hover_handoff_target(
            hover_handoff_target_raw(opacity="0")
        )
        self.assertTrue(previsible["handoff_enterable"])
        self.assertFalse(previsible["strict_production_visible"])
        self.assertFalse(
            previsible["controlled_target"]["clauses"]["opacity_above_threshold"]
        )

        live_source = inspect.getsource(FlowSession._live_control_facts)
        associated_source = inspect.getsource(
            FlowSession._current_associated_download_submenu
        )
        exact_1k_source = inspect.getsource(
            FlowSession._exact_1k_original_size_option
        )
        readiness_source = inspect.getsource(
            FlowSession._wait_for_current_1k_original_size_option
        )
        self.assertIn('Number.parseFloat(style.opacity || "1") > 0.01', live_source)
        self.assertIn("Number.parseFloat(style.opacity || \"1\") > 0.01", associated_source)
        self.assertIn("_live_control_facts", exact_1k_source)
        self.assertIn("_exact_1k_original_size_option", readiness_source)
        self.assertNotIn("handoff_enterable", associated_source)
        self.assertNotIn("handoff_enterable", live_source)

    def test_hover_handoff_has_no_variant_or_window_state_branch(self):
        source = "\n".join(
            inspect.getsource(method)
            for method in (
                FlowSession._normalize_hover_handoff_target,
                FlowSession._current_hover_handoff_target,
                FlowSession._handoff_hover_opened_download_submenu,
            )
        ).lower()

        for forbidden in ("variants", "minimized", "bringtofront", "bring_to_front"):
            self.assertNotIn(forbidden, source)

    def test_hover_opened_controlled_submenu_handoff_enters_exact_panel_and_reaches_1k(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        submenu = MagicMock()
        option = MagicMock()
        option_snapshot = download_control_snapshot(visible_text="1K Original size")
        parent.evaluate.return_value = hover_handoff_target_raw(
            panel_id="mat-menu-panel-221",
            handoff_point={"x": 976.0, "y": 330.0},
        )
        relationship = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-221",
            "relationship_basis": "aria_controls",
            "controlled_panel_found": True,
            "controlled_panel_connected": True,
            "controlled_panel_visible": True,
            "visible_related_menu_count": 1,
        }
        diagnostics = {}

        with patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], relationship),
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )

        self.assertIs(result, submenu)
        page.mouse.move.assert_called_once_with(976.0, 330.0)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        parent.click.assert_not_called()
        self.assertTrue(diagnostics["hover_controlled_handoff_succeeded"])
        self.assertEqual(diagnostics.get("parent_activation_count", 0), 0)
        self.assertTrue(diagnostics["strict_production_visible_before_handoff"])
        self.assertTrue(diagnostics["handoff_enterable"])
        self.assertFalse(
            diagnostics["handoff_opacity_only_strict_visibility_difference"]
        )

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], relationship),
        ), patch.object(
            session,
            "_exact_1k_original_size_option",
            return_value=(option, option_snapshot),
        ) as exact_1k, patch.object(
            session,
            "_download_with_cdp",
        ) as cdp_download:
            ready_option, ready_snapshot = (
                session._wait_for_current_1k_original_size_option(diagnostics)
            )

        self.assertIs(ready_option, option)
        self.assertIs(ready_snapshot, option_snapshot)
        exact_1k.assert_called_once_with(submenu)
        cdp_download.assert_not_called()

    def test_hover_opened_controlled_submenu_is_handed_off_before_broad_menu_scan(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        diagnostics = {
            "poll_count": 0,
            "hover_mouse_move_completed": True,
            "parent_activation_count": 0,
            "hover_controlled_handoff_attempted": False,
        }
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_handoff_hover_opened_download_submenu",
            return_value=submenu,
        ) as handoff, patch.object(
            session,
            "_current_associated_download_submenu",
        ) as broad_scan:
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_500,
            )

        self.assertIs(result, submenu)
        handoff.assert_called_once_with(parent, diagnostics)
        broad_scan.assert_not_called()

    def test_postactivation_previsible_submenu_gets_one_physical_handoff(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        submenu = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(opacity="0")
        relationship = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": "aria_controls",
        }
        diagnostics = {
            "poll_count": 0,
            "hover_mouse_move_completed": True,
            "parent_activation_count": 1,
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], relationship),
        ) as strict_relationship:
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_500,
            )

        self.assertIs(result, submenu)
        self.assertEqual(diagnostics["parent_activation_count"], 1)
        self.assertTrue(diagnostics["hover_controlled_handoff_attempted"])
        self.assertTrue(diagnostics["hover_controlled_handoff_mouse_move_completed"])
        self.assertTrue(diagnostics["hover_controlled_handoff_succeeded"])
        page.mouse.move.assert_called_once_with(1050.0, 352.0)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        strict_relationship.assert_called_once_with(parent)

    def test_attempted_handoff_is_not_started_again_and_strict_path_remains(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        relationship = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": "aria_controls",
        }
        diagnostics = {
            "poll_count": 0,
            "hover_mouse_move_completed": True,
            "parent_activation_count": 1,
            "hover_controlled_handoff_attempted": True,
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_handoff_hover_opened_download_submenu",
        ) as handoff, patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], relationship),
        ) as strict_relationship:
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_500,
            )

        self.assertIs(result, submenu)
        handoff.assert_not_called()
        strict_relationship.assert_called_once_with(parent)

    def test_pre_move_handoff_loss_is_not_retried_by_later_poll(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        initial = hover_handoff_target_raw(opacity="0")
        changed = hover_handoff_target_raw(
            panel_id="mat-menu-panel-666",
            opacity="0",
        )
        parent.evaluate.side_effect = [initial, changed]
        waiting = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": None,
            "controlled_panel_found": True,
            "visible_menu_panel_count": 0,
        }
        ready = {
            **waiting,
            "relationship_basis": "aria_controls",
            "visible_menu_panel_count": 1,
        }
        diagnostics = {
            "poll_count": 0,
            "hover_mouse_move_completed": True,
            "parent_activation_count": 1,
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_handoff_hover_opened_download_submenu",
            wraps=session._handoff_hover_opened_download_submenu,
        ) as handoff, patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([], waiting), ([submenu], ready)],
        ):
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_500,
            )

        self.assertIs(result, submenu)
        handoff.assert_called_once_with(parent, diagnostics)
        self.assertFalse(
            diagnostics["hover_controlled_handoff_pre_move_revalidation"]["passed"]
        )
        self.assertTrue(diagnostics["hover_controlled_handoff_lost"])
        self.assertFalse(diagnostics.get("hover_controlled_handoff_attempted", False))
        session.page.mouse.move.assert_not_called()

    def test_post_move_handoff_loss_is_not_retried_by_later_poll(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        initial = hover_handoff_target_raw(opacity="0")
        changed = hover_handoff_target_raw(
            panel_id="mat-menu-panel-666",
            opacity="0",
        )
        parent.evaluate.side_effect = [initial, initial, changed]
        waiting = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-665",
            "relationship_basis": None,
            "controlled_panel_found": True,
            "visible_menu_panel_count": 0,
        }
        ready = {
            **waiting,
            "relationship_basis": "aria_controls",
            "visible_menu_panel_count": 1,
        }
        diagnostics = {
            "poll_count": 0,
            "hover_mouse_move_completed": True,
            "parent_activation_count": 1,
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_handoff_hover_opened_download_submenu",
            wraps=session._handoff_hover_opened_download_submenu,
        ) as handoff, patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([], waiting), ([submenu], ready)],
        ):
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_500,
            )

        self.assertIs(result, submenu)
        handoff.assert_called_once_with(parent, diagnostics)
        self.assertTrue(diagnostics["hover_controlled_handoff_attempted"])
        self.assertFalse(
            diagnostics["hover_controlled_handoff_post_move_revalidation"]["passed"]
        )
        self.assertTrue(diagnostics["hover_controlled_handoff_lost"])
        session.page.mouse.move.assert_called_once_with(1050.0, 352.0)

    def test_ambiguous_hover_controlled_submenu_fails_closed_without_pointer_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(
            panel_id="duplicate-panel-id",
            controlled_target_count=2,
            controlled_target_connected_count=2,
            controlled_target_visible_count=2,
            controlled_role_menu_count=2,
            controlled_role_menu_connected_count=2,
            controlled_role_menu_visible_count=2,
            handoff_point=None,
            handoff_hit_owned=False,
        )
        diagnostics = {}

        with self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_AMBIGUOUS",
        ):
            session._handoff_hover_opened_download_submenu(parent, diagnostics)

        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        parent.click.assert_not_called()
        self.assertTrue(diagnostics["hover_controlled_handoff_ambiguous"])

    def test_hover_controlled_panel_disappears_before_verification_without_stale_input(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.evaluate.return_value = hover_handoff_target_raw(
            panel_id="mat-menu-panel-221",
            handoff_point={"x": 976.0, "y": 330.0},
        )
        diagnostics = {}
        closed = {
            "aria_haspopup": "menu",
            "aria_expanded": "false",
            "aria_controls": None,
            "relationship_basis": None,
            "controlled_panel_found": False,
        }
        with patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([], closed),
        ):
            result = session._handoff_hover_opened_download_submenu(
                parent,
                diagnostics,
            )

        self.assertIsNone(result)
        page.mouse.move.assert_called_once_with(976.0, 330.0)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        parent.click.assert_not_called()
        self.assertTrue(diagnostics["aria_expanded_ever_true"])
        self.assertTrue(diagnostics["hover_controlled_handoff_lost"])

    def test_lost_hover_controlled_panel_is_not_reactivated_as_a_closed_parent(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0}

        def wait_for_submenu(_parent, diagnostics, *, timeout_ms):
            diagnostics["expanded_seen"] = True
            diagnostics["aria_expanded_ever_true"] = True
            diagnostics["hover_controlled_handoff_lost"] = True
            return None

        with patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
            return_value=(parent, parent_snapshot, current_box),
        ), patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=wait_for_submenu,
        ) as wait, patch.object(
            session,
            "_activate_download_control",
        ) as activate, self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_NOT_FOUND",
        ):
            session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertEqual(wait.call_count, 2)
        activate.assert_not_called()

    def test_hover_controlled_submenu_handoff_introduces_no_unsafe_input(self):
        source = "\n".join(
            inspect.getsource(method)
            for method in (
                FlowSession._capture_before_hover_handoff_decision,
                FlowSession._capture_after_hover_handoff_snapshot,
                FlowSession._current_hover_handoff_target,
                FlowSession._handoff_hover_opened_download_submenu,
            )
        )

        self.assertNotIn("force=", source)
        self.assertNotIn("dispatchEvent", source)
        self.assertNotIn(".click(", source)
        self.assertNotIn("bringToFront", source)
        self.assertNotIn(".bring_to_front(", source)
        self.assertNotIn(".focus(", source)
        self.assertNotIn("wait_for_timeout", source)

    def test_mouse_move_fallback_uses_exactly_one_parent_mouse_down_up(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        current_box = {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0}
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        with patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=[None, submenu],
        ), patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=parent_snapshot,
        ), patch.object(
            session,
            "_current_download_control_hit",
            return_value=current_box,
        ):
            result, diagnostics = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertIs(result, submenu)
        parent.hover.assert_not_called()
        self.assertEqual(session.page.mouse.move.call_count, 2)
        session.page.mouse.down.assert_called_once_with(button="left")
        session.page.mouse.up.assert_called_once_with(button="left")
        self.assertEqual(diagnostics["parent_activation_count"], 1)

    def test_expanded_parent_after_locator_probe_timeout_is_reacquired_without_reactivation(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        fresh_submenu = MagicMock()
        current_box = {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0}
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        with patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=[None, fresh_submenu],
        ) as wait, patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
            return_value=(parent, parent_snapshot, current_box),
        ), patch.object(
            session,
            "_activate_download_control",
            side_effect=TimeoutError("Locator.evaluate: Timeout 1000ms exceeded"),
        ) as activate:
            result, diagnostics = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertIs(result, fresh_submenu)
        self.assertEqual(wait.call_count, 2)
        activate.assert_called_once_with(
            parent,
            "mouse",
            prepared_snapshot=parent_snapshot,
        )
        self.assertEqual(diagnostics["parent_activation_count"], 1)
        self.assertTrue(diagnostics["parent_activation_probe_recovered"])
        session.page.mouse.down.assert_not_called()

    def test_download_parent_replacement_during_expansion_is_locally_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        stale_parent = MagicMock()
        fresh_parent = MagicMock()
        fresh_submenu = MagicMock()
        facts = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "has_aria_controls": True,
            "relationship_basis": "aria_controls",
            "live_menu_count": 2,
            "related_menu_count": 1,
            "structural_candidate_count": 0,
            "visible_related_menu_count": 1,
        }
        diagnostics = {"poll_count": 0}
        with patch.object(
            session,
            "_current_download_submenu_parents",
            side_effect=[
                ([stale_parent], {"semantic_download_parent_count": 1}),
                ([fresh_parent], {"semantic_download_parent_count": 1}),
            ],
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[
                TimeoutError("stale expanded Download parent"),
                ([fresh_submenu], facts),
            ],
        ) as relationship:
            result = session._wait_for_associated_download_submenu(
                stale_parent,
                diagnostics,
                timeout_ms=1_000,
            )

        self.assertIs(result, fresh_submenu)
        self.assertEqual(
            [call.args[0] for call in relationship.call_args_list],
            [stale_parent, fresh_parent],
        )
        session.page.mouse.down.assert_not_called()

    def test_recreated_submenu_panel_is_reacquired_before_1k_lookup(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        stale_submenu = MagicMock()
        fresh_submenu = MagicMock()
        option = MagicMock()
        snapshot = download_control_snapshot(visible_text="1K Original size")
        facts = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "has_aria_controls": True,
            "relationship_basis": "aria_controls",
            "visible_related_menu_count": 1,
        }
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([stale_submenu], facts), ([fresh_submenu], facts)],
        ), patch.object(
            session,
            "_exact_1k_original_size_option",
            side_effect=[
                TimeoutError("stale submenu locator"),
                (option, snapshot),
            ],
        ) as exact:
            selected, selected_snapshot = (
                session._wait_for_current_1k_original_size_option({}, timeout_ms=1_000)
            )

        self.assertIs(selected, option)
        self.assertIs(selected_snapshot, snapshot)
        self.assertEqual(
            [call.args[0] for call in exact.call_args_list],
            [stale_submenu, fresh_submenu],
        )

    def test_already_ready_exact_1k_returns_before_poll_sleep(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        submenu = MagicMock()
        option = MagicMock()
        snapshot = download_control_snapshot(visible_text="1K Original size")
        facts = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-ready",
            "has_aria_controls": True,
            "relationship_basis": "aria_controls",
            "visible_related_menu_count": 1,
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=([submenu], facts),
        ), patch.object(
            session,
            "_exact_1k_original_size_option",
            return_value=(option, snapshot),
        ):
            selected, selected_snapshot = (
                session._wait_for_current_1k_original_size_option({})
            )

        self.assertIs(selected, option)
        self.assertIs(selected_snapshot, snapshot)
        page.wait_for_timeout.assert_not_called()

    def test_1k_render_delay_retries_with_fresh_submenu_lookup(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        first_submenu = MagicMock()
        second_submenu = MagicMock()
        option = MagicMock()
        snapshot = download_control_snapshot(visible_text="1K Original size")
        facts = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "has_aria_controls": True,
            "relationship_basis": "aria_controls",
            "visible_related_menu_count": 1,
        }
        diagnostics = {}
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([first_submenu], facts), ([second_submenu], facts)],
        ), patch.object(
            session,
            "_exact_1k_original_size_option",
            side_effect=[
                FlowAutomationError("DOWNLOAD_1K_OPTION_NOT_FOUND"),
                (option, snapshot),
            ],
        ) as exact:
            selected, _ = session._wait_for_current_1k_original_size_option(
                diagnostics,
                timeout_ms=1_000,
            )

        self.assertIs(selected, option)
        self.assertEqual(
            [call.args[0] for call in exact.call_args_list],
            [first_submenu, second_submenu],
        )
        self.assertEqual(diagnostics["one_k_poll_count"], 2)

    def test_aria_controls_panel_appears_after_short_bounded_delay(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        parent_facts = {
            "semantic_download_parent_count": 1,
            "menu_visible": True,
        }
        waiting_facts = {
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-301",
            "has_aria_controls": True,
            "relationship_basis": None,
            "live_menu_count": 2,
            "related_menu_count": 0,
            "structural_candidate_count": 0,
            "visible_related_menu_count": 0,
            "controlled_panel_found": False,
        }
        ready_facts = {
            **waiting_facts,
            "relationship_basis": "aria_controls",
            "related_menu_count": 1,
            "visible_related_menu_count": 1,
            "controlled_panel_found": True,
            "controlled_panel_connected": True,
            "controlled_panel_visible": True,
        }
        diagnostics = {"poll_count": 0}
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], parent_facts),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([], waiting_facts), ([submenu], ready_facts)],
        ) as relationship:
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_000,
            )

        self.assertIs(result, submenu)
        self.assertEqual(relationship.call_count, 2)
        self.assertEqual(diagnostics["relationship_basis"], "aria_controls")

    def test_parent_remount_changed_aria_controls_is_reacquired(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        old_parent = MagicMock()
        new_parent = MagicMock()
        submenu = MagicMock()
        first = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-401",
            "relationship_basis": None,
            "visible_related_menu_count": 0,
        }
        second = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-402",
            "relationship_basis": "aria_controls",
            "visible_related_menu_count": 1,
        }
        diagnostics = {"poll_count": 0}
        with patch.object(
            session,
            "_current_download_submenu_parents",
            side_effect=[
                ([old_parent], {"semantic_download_parent_count": 1}),
                ([new_parent], {"semantic_download_parent_count": 1}),
            ],
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([], first), ([submenu], second)],
        ) as relationship:
            result = session._wait_for_associated_download_submenu(
                old_parent,
                diagnostics,
                timeout_ms=1_000,
            )

        self.assertIs(result, submenu)
        self.assertEqual(
            [call.args[0] for call in relationship.call_args_list],
            [old_parent, new_parent],
        )
        self.assertTrue(diagnostics["aria_controls_changed"])
        self.assertTrue(diagnostics["transition_or_remount_observed"])

    def test_old_hidden_panel_is_ignored_when_current_controlled_panel_appears(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "aria-haspopup": "menu",
            "aria-expanded": "true",
            "aria-controls": "mat-menu-panel-new",
        }.get(name)
        parent.evaluate.return_value = {
            "match_count": 1,
            "connected_count": 1,
            "visible_count": 1,
        }
        old_panel = MagicMock()
        old_panel.get_attribute.return_value = "mat-menu-panel-old"
        current_panel = MagicMock()
        current_panel.get_attribute.return_value = "mat-menu-panel-new"
        current_panel.get_by_role.return_value.all.return_value = []
        page.get_by_role.return_value.all.return_value = [old_panel, current_panel]

        def live(control):
            if control is old_panel:
                return {"connected": True, "visible": False, "enabled": True}
            return {"connected": True, "visible": True, "enabled": True}

        with patch.object(session, "_live_control_facts", side_effect=live):
            matches, facts = session._current_associated_download_submenu(parent)

        self.assertEqual(matches, [current_panel])
        self.assertEqual(facts["visible_menu_panel_count"], 1)
        self.assertEqual(facts["visible_related_menu_count"], 1)

    def test_temporary_controlled_panel_overlap_is_polled_until_unambiguous(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        old_panel = MagicMock()
        new_panel = MagicMock()
        overlap = {
            "aria_expanded": "true",
            "aria_controls": "mat-menu-panel-501",
            "relationship_basis": "aria_controls",
            "visible_menu_panel_count": 3,
            "visible_related_menu_count": 2,
        }
        settled = {
            **overlap,
            "visible_menu_panel_count": 2,
            "visible_related_menu_count": 1,
        }
        diagnostics = {"poll_count": 0}
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            side_effect=[([old_panel, new_panel], overlap), ([new_panel], settled)],
        ):
            result = session._wait_for_associated_download_submenu(
                parent,
                diagnostics,
                timeout_ms=1_000,
            )

        self.assertIs(result, new_panel)
        self.assertTrue(diagnostics["controlled_panel_overlap_observed"])
        self.assertTrue(diagnostics["multiple_visible_panels_observed"])

    def test_unrelated_visible_1k_panel_never_satisfies_current_aria_controls(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "aria-haspopup": "menu",
            "aria-expanded": "true",
            "aria-controls": "controlled-panel",
        }.get(name)
        parent.evaluate.return_value = {
            "match_count": 0,
            "connected_count": 0,
            "visible_count": 0,
        }
        unrelated = MagicMock()
        unrelated.get_attribute.return_value = "unrelated-panel"
        unrelated.evaluate.return_value = {"related": False}
        option = MagicMock()
        unrelated.get_by_role.return_value.all.return_value = [option]
        page.get_by_role.return_value.all.return_value = [unrelated]

        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ):
            matches, facts = session._current_associated_download_submenu(parent)

        self.assertEqual(matches, [])
        self.assertFalse(facts["controlled_panel_found"])
        self.assertEqual(facts["visible_1k_in_controlled_panel"], 0)

    def test_persistent_controlled_panel_ambiguity_fails_closed_after_bound(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        panels = [MagicMock(), MagicMock()]
        relationship = {
            "aria_expanded": "true",
            "aria_controls": "duplicate-panel-id",
            "relationship_basis": "aria_controls",
            "visible_related_menu_count": 2,
        }
        diagnostics = {}
        with patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.3],
        ), patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_associated_download_submenu",
            return_value=(panels, relationship),
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_1K_OPTION_NOT_READY",
        ):
            session._wait_for_current_1k_original_size_option(
                diagnostics,
                timeout_ms=250,
            )

        self.assertTrue(diagnostics["ambiguity_persisted"])

    def test_expanded_parent_is_not_reactivated_while_relationship_settles(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0}

        def waits(_parent, diagnostics, *, timeout_ms):
            if timeout_ms == 1_500:
                diagnostics["expanded_seen"] = True
                diagnostics["aria_expanded"] = "true"
                return None
            return submenu

        with patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
            return_value=(parent, parent_snapshot, current_box),
        ), patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=waits,
        ) as wait, patch.object(
            session,
            "_activate_download_control",
        ) as activate:
            result, diagnostics = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertIs(result, submenu)
        self.assertEqual(wait.call_count, 2)
        activate.assert_not_called()
        self.assertEqual(diagnostics["parent_activation_count"], 0)

    def test_download_ui_probe_stops_at_1k_readiness_without_start_or_download(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        session._page_cdp_session = MagicMock()
        session._browser = MagicMock()
        page.evaluate.return_value = {
            "hasFocus": True,
            "visibilityState": "visible",
        }
        candidate = CandidateRecord(
            variant=1,
            suggested_filename="0028__acceptance_0028_visual_001__v01.png",
            source_kind="rendered-image",
            candidate_id="00000000-0000-4000-8000-000000000002",
            download_status="failed",
        )
        image = MagicMock()
        tile = MagicMock()
        parent = MagicMock()
        submenu = MagicMock()
        option = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
        )
        option_snapshot = download_control_snapshot(
            visible_text="1K Original size",
        )
        action_order = []
        page.keyboard.press.side_effect = lambda key: action_order.append(
            f"keyboard:{key}"
        )

        def exact_candidate_result(_candidate):
            action_order.append("exact_candidate")
            return candidate.candidate_id, image, tile

        def open_menu(_candidate_id, _image, _tile, diagnostics):
            diagnostics["timing_ms"] = {
                "toolbar_reveal": 11.0,
                "more_options_activation": 2.0,
                "download_parent_acquisition": 13.0,
            }
            diagnostics["more_options_before_activation"] = {
                "reveal_attempt": 1,
                "prior_reveal_outcomes": [],
            }
            return parent, parent_snapshot

        submenu_diagnostics = {
            "parent_settle_poll_count": 1,
            "poll_count": 2,
            "pre_activation_poll_phase_entered": True,
            "pre_activation_poll_count": 2,
            "aria_controls": "mat-menu-panel-28",
            "aria_expanded_ever_true": True,
            "aria_controls_ever_non_null": True,
            "controlled_submenu_ever_appeared": True,
            "aria_controls_changed": False,
            "controlled_panel_overlap_observed": False,
        }

        def ready_1k(diagnostics, *, timeout_ms):
            self.assertEqual(timeout_ms, 2_000)
            diagnostics["one_k_poll_count"] = 1
            return option, option_snapshot

        with patch.object(session, "open", return_value=session), patch.object(
            session,
            "close",
        ) as close, patch.object(
            session,
            "_assert_download_context",
        ), patch.object(
            session,
            "_exact_candidate_tile",
            side_effect=exact_candidate_result,
        ) as exact_candidate, patch.object(
            session,
            "_open_candidate_menu",
            side_effect=open_menu,
        ) as open_menu_call, patch.object(
            session,
            "_expose_download_submenu",
            return_value=(submenu, submenu_diagnostics),
        ) as expose, patch.object(
            session,
            "_wait_for_current_1k_original_size_option",
            side_effect=ready_1k,
        ) as one_k, patch.object(
            session,
            "generate_one",
        ) as generate, patch.object(
            session,
            "_download_with_cdp",
        ) as native_download, patch.object(
            session,
            "download_candidate",
        ) as download_candidate:
            report = session.probe_download_ui(
                candidate=candidate,
                label="foreground-visible",
                samples=3,
            )

        self.assertEqual(len(report["samples"]), 3)
        self.assertEqual(report["summary"]["success_count"], 3)
        self.assertEqual(report["label"], "foreground-visible")
        self.assertTrue(report["ownership_verified_before_ui_action"])
        self.assertTrue(report["focus_emulation_active"])
        self.assertTrue(all(sample["status"] == "success" for sample in report["samples"]))
        self.assertTrue(all(sample["aria_controls"] == "mat-menu-panel-28" for sample in report["samples"]))
        self.assertTrue(all(sample["submenu_polls"] == 2 for sample in report["samples"]))
        self.assertTrue(
            all(
                sample["download_parent_reacquisitions"] == 3
                for sample in report["samples"]
            )
        )
        self.assertTrue(
            all(sample["pre_activation_poll_count"] == 2 for sample in report["samples"])
        )
        self.assertEqual(exact_candidate.call_count, 4)
        self.assertEqual(action_order[0], "exact_candidate")
        self.assertEqual(open_menu_call.call_count, 3)
        self.assertEqual(expose.call_count, 3)
        self.assertEqual(one_k.call_count, 3)
        self.assertEqual(page.keyboard.press.call_count, 12)
        self.assertTrue(all(call.args == ("Escape",) for call in page.keyboard.press.call_args_list))
        option.assert_not_called()
        generate.assert_not_called()
        native_download.assert_not_called()
        download_candidate.assert_not_called()
        session._browser.new_browser_cdp_session.assert_not_called()
        page.bring_to_front.assert_not_called()
        close.assert_called_once_with()
        self.assertFalse(report["safety"]["start_generation_activated"])
        self.assertFalse(report["safety"]["one_k_activated"])
        self.assertFalse(report["safety"]["native_download_tracking_configured"])

    def test_expose_submenu_preserves_poll_and_dispatch_diagnostics_on_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = {"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0}
        preserved = {}

        poll_counts = iter((3, 2))

        def wait_for_submenu(_parent, diagnostics, *, timeout_ms):
            count = next(poll_counts)
            diagnostics["poll_count"] += count
            diagnostics.update(
                {
                    "aria_expanded": "false",
                    "aria_controls": None,
                    "controlled_panel_found": False,
                    "expanded_seen": False,
                    "final_parent": {
                        "connected": True,
                        "visible": True,
                        "enabled": True,
                        "pointer_events": "auto",
                        "aria_expanded": "false",
                        "aria_controls": None,
                    },
                }
            )
            return None

        def activate_parent(
            _parent,
            activation,
            *,
            prepared_snapshot,
            diagnostic_trace,
        ):
            self.assertEqual(activation, "mouse")
            self.assertIs(prepared_snapshot, parent_snapshot)
            self.assertIs(diagnostic_trace, preserved)
            diagnostic_trace.update(
                {
                    "activation_dispatch_began": True,
                    "activation_mouse_move_completed": True,
                    "activation_mouse_down_completed": True,
                    "activation_mouse_up_completed": True,
                    "activation_dispatch_returned_normally": True,
                }
            )
            return {"input": "mouse_move_down_up"}

        with patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
            return_value=(parent, parent_snapshot, current_box),
        ) as settle, patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=wait_for_submenu,
        ) as wait, patch.object(
            session,
            "_activate_download_control",
            side_effect=activate_parent,
        ) as activate, self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu appeared",
        ):
            session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
                diagnostics=preserved,
            )

        self.assertEqual(settle.call_count, 2)
        self.assertEqual(wait.call_count, 2)
        activate.assert_called_once()
        self.assertEqual(preserved["poll_count"], 5)
        self.assertTrue(preserved["pre_activation_poll_phase_entered"])
        self.assertEqual(preserved["pre_activation_poll_count"], 3)
        self.assertTrue(preserved["post_activation_poll_phase_entered"])
        self.assertEqual(preserved["post_activation_poll_count"], 2)
        self.assertEqual(preserved["parent_activation_count"], 1)
        self.assertTrue(preserved["activation_dispatch_began"])
        self.assertTrue(preserved["activation_mouse_move_completed"])
        self.assertTrue(preserved["activation_mouse_down_completed"])
        self.assertTrue(preserved["activation_mouse_up_completed"])
        self.assertTrue(preserved["activation_dispatch_returned_normally"])
        self.assertFalse(preserved["aria_expanded_ever_true"])
        self.assertFalse(preserved["aria_controls_ever_non_null"])
        self.assertEqual(preserved["final_parent"]["aria_expanded"], "false")
        self.assertIsNone(preserved["final_parent"]["aria_controls"])
        self.assertEqual(
            preserved["final_safe_failure_classification"],
            "DOWNLOAD_SUBMENU_NOT_FOUND",
        )

    def test_download_ui_probe_preserves_helper_failure_diagnostics_and_safety(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        session._page_cdp_session = MagicMock()
        session._browser = MagicMock()
        page.evaluate.return_value = {
            "hasFocus": True,
            "visibilityState": "visible",
        }
        candidate = CandidateRecord(
            variant=1,
            suggested_filename="0028__acceptance_0028_visual_001__v01.png",
            source_kind="rendered-image",
            candidate_id="00000000-0000-4000-8000-000000000002",
            download_status="failed",
        )
        image = MagicMock()
        tile = MagicMock()
        parent = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        neutral = {
            "cleanup_neutral_state": True,
            "remaining_visible_menu_count": 0,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
            "cleanup_poll_count": 1,
        }

        def open_menu(_candidate_id, _image, _tile, diagnostics):
            diagnostics["timing_ms"] = {
                "toolbar_reveal": 1.0,
                "more_options_activation": 2.0,
                "download_parent_acquisition": 3.0,
            }
            diagnostics["more_options_before_activation"] = {
                "reveal_attempt": 1,
                "prior_reveal_outcomes": [],
            }
            return parent, parent_snapshot

        def fail_exposure(
            _candidate_id,
            _parent,
            _snapshot,
            *,
            diagnostics,
        ):
            diagnostics.update(
                {
                    "parent_settle_poll_count": 2,
                    "poll_count": 9,
                    "parent_activation_count": 1,
                    "activation_dispatch_began": True,
                    "activation_mouse_move_completed": True,
                    "activation_mouse_down_completed": True,
                    "activation_mouse_up_completed": True,
                    "activation_dispatch_returned_normally": True,
                    "pre_activation_poll_phase_entered": True,
                    "pre_activation_poll_count": 5,
                    "post_activation_poll_phase_entered": True,
                    "post_activation_poll_count": 4,
                    "aria_expanded": "false",
                    "aria_controls": None,
                    "aria_expanded_ever_true": False,
                    "aria_controls_ever_non_null": False,
                    "aria_controls_changed": False,
                    "controlled_submenu_ever_appeared": False,
                    "controlled_panel_seen_temporarily": False,
                    "final_parent": {
                        "connected": True,
                        "visible": True,
                        "enabled": True,
                        "pointer_events": "auto",
                        "aria_expanded": "false",
                        "aria_controls": None,
                    },
                    "final_safe_failure_classification": (
                        "DOWNLOAD_SUBMENU_NOT_FOUND"
                    ),
                }
            )
            raise FlowAutomationError(
                "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu appeared"
            )

        with patch.object(session, "open", return_value=session), patch.object(
            session, "close"
        ), patch.object(session, "_assert_download_context"), patch.object(
            session,
            "_exact_candidate_tile",
            return_value=(candidate.candidate_id, image, tile),
        ), patch.object(
            session,
            "_open_candidate_menu",
            side_effect=open_menu,
        ), patch.object(
            session,
            "_expose_download_submenu",
            side_effect=fail_exposure,
        ), patch.object(
            session,
            "_dismiss_download_ui_probe_menus",
            return_value=neutral,
        ), patch.object(
            session,
            "_wait_for_current_1k_original_size_option",
        ) as one_k, patch.object(
            session, "generate_one"
        ) as generate, patch.object(
            session, "_download_with_cdp"
        ) as native_download, patch.object(
            session, "download_candidate"
        ) as download_candidate:
            report = session.probe_download_ui(
                candidate=candidate,
                label="foreground-visible-v2",
                samples=1,
            )

        sample = report["samples"][0]
        self.assertEqual(report["schema"], "nano-banana-download-ui-probe/v2")
        self.assertEqual(sample["status"], "failure")
        self.assertEqual(sample["submenu_polls"], 9)
        self.assertEqual(sample["download_parent_reacquisitions"], 11)
        self.assertEqual(sample["pre_activation_poll_count"], 5)
        self.assertEqual(sample["post_activation_poll_count"], 4)
        self.assertEqual(sample["parent_activation_count"], 1)
        self.assertTrue(sample["activation_dispatch_returned_normally"])
        self.assertEqual(sample["final_parent"]["aria_expanded"], "false")
        self.assertIsNone(sample["final_parent"]["aria_controls"])
        self.assertEqual(
            sample["final_safe_failure_classification"],
            "DOWNLOAD_SUBMENU_NOT_FOUND",
        )
        self.assertTrue(sample["cleanup_neutral_state"])
        one_k.assert_not_called()
        generate.assert_not_called()
        native_download.assert_not_called()
        download_candidate.assert_not_called()
        session._browser.new_browser_cdp_session.assert_not_called()
        page.bring_to_front.assert_not_called()

    def test_download_parent_probe_trace_records_geometry_hits_and_trusted_events(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        parent_node = {
            "node_id": "download-node-1",
            "tag": "button",
            "role": "menuitem",
            "aria_haspopup": "menu",
            "aria_expanded": "false",
            "aria_controls": None,
            "connected": True,
            "hover": True,
            "bounding_box": {"x": 420.25, "y": 315.5, "width": 180.0, "height": 48.0},
            "class_summary": ["mat-mdc-menu-item", "safe_class"],
        }
        child_hit = {
            **parent_node,
            "node_id": "download-node-2",
            "tag": "span",
            "role": None,
            "class_summary": ["mdc-list-item__primary-text"],
        }
        unrelated_hit = {
            **parent_node,
            "node_id": "download-node-3",
            "tag": "div",
            "role": None,
            "class_summary": ["overlay"],
        }
        page.evaluate.return_value = {
            "installed": True,
            "cleaned_up": True,
            "cleanup_error_count": 0,
            "initial_node_id": "download-node-1",
            "events": [
                {
                    "event_type": "pointerdown",
                    "offset_ms": 12.3456,
                    "is_trusted": True,
                    "target": child_hit,
                    "current_target": parent_node,
                    "pointer": {"x": 510.25, "y": 339.5},
                    "button": 0,
                    "buttons": 1,
                    "hover": True,
                }
            ],
            "aria_timeline": [],
            "checkpoints": [
                {
                    "stage": "before_down",
                    "offset_ms": 11.0,
                    "pointer": {"x": 510.25, "y": 339.5},
                    "viewport": {"width": 1280, "height": 720},
                    "parent": parent_node,
                    "original_connected": True,
                    "reacquired_parent_count": 1,
                    "reacquired_same_node": True,
                    "node_replaced": False,
                    "hit_target": child_hit,
                    "hit_owned_by_parent": True,
                },
                {
                    "stage": "after_up",
                    "offset_ms": 14.0,
                    "pointer": {"x": 510.25, "y": 339.5},
                    "viewport": {"width": 1280, "height": 720},
                    "parent": parent_node,
                    "original_connected": True,
                    "reacquired_parent_count": 1,
                    "reacquired_same_node": True,
                    "node_replaced": False,
                    "hit_target": unrelated_hit,
                    "hit_owned_by_parent": False,
                },
            ],
            "lifecycle": [],
        }

        trace = session._remove_download_parent_probe_trace(page)

        self.assertTrue(trace["installed"])
        self.assertTrue(trace["cleaned_up"])
        self.assertEqual(
            trace["checkpoints"][0]["parent"]["bounding_box"],
            {"x": 420.25, "y": 315.5, "width": 180.0, "height": 48.0},
        )
        self.assertEqual(
            trace["checkpoints"][0]["pointer"],
            {"x": 510.25, "y": 339.5},
        )
        self.assertEqual(trace["checkpoints"][0]["viewport"], {"width": 1280, "height": 720})
        self.assertTrue(trace["summary"]["owned_hit_observed"])
        self.assertTrue(trace["summary"]["unowned_hit_observed"])
        self.assertEqual(trace["summary"]["trusted_event_count"], 1)
        event = trace["events"][0]
        self.assertEqual(event["event_type"], "pointerdown")
        self.assertTrue(event["is_trusted"])
        self.assertEqual(event["target"]["node_id"], "download-node-2")
        self.assertEqual(event["current_target"]["node_id"], "download-node-1")
        self.assertEqual(event["pointer"], {"x": 510.25, "y": 339.5})

    def test_download_parent_probe_trace_classifies_aria_expanded_timelines(self):
        cases = {
            "never_changes": ([
                {"attribute": "aria-expanded", "old_value": None, "new_value": "false", "reason": "initial"},
            ], ["false"], True),
            "opens": ([
                {"attribute": "aria-expanded", "old_value": None, "new_value": "false", "reason": "initial"},
                {"attribute": "aria-expanded", "old_value": "false", "new_value": "true", "reason": "mutation"},
            ], ["false", "true"], False),
            "opens_then_closes": ([
                {"attribute": "aria-expanded", "old_value": None, "new_value": "false", "reason": "initial"},
                {"attribute": "aria-expanded", "old_value": "false", "new_value": "true", "reason": "mutation"},
                {"attribute": "aria-expanded", "old_value": "true", "new_value": "false", "reason": "mutation"},
            ], ["false", "true", "false"], False),
        }
        for name, (timeline, expected, never_changed) in cases.items():
            with self.subTest(name=name):
                raw = {
                    "installed": True,
                    "cleaned_up": True,
                    "initial_node_id": "download-node-1",
                    "events": [],
                    "aria_timeline": [
                        {"offset_ms": index, "node_id": "download-node-1", **entry}
                        for index, entry in enumerate(timeline)
                    ],
                    "checkpoints": [],
                    "lifecycle": [],
                }
                trace = FlowSession._normalize_download_parent_probe_trace(raw)
                self.assertEqual(trace["summary"]["aria_expanded_sequence"], expected)
                self.assertEqual(
                    trace["summary"]["aria_expanded_never_changed"],
                    never_changed,
                )

    def test_download_parent_probe_trace_detects_replacement(self):
        raw = {
            "installed": True,
            "cleaned_up": True,
            "initial_node_id": "download-node-1",
            "events": [],
            "aria_timeline": [],
            "checkpoints": [
                {
                    "stage": "submenu_poll",
                    "offset_ms": 8,
                    "pointer": {"x": 1, "y": 2},
                    "viewport": {"width": 1000, "height": 700},
                    "parent": {
                        "node_id": "download-node-2",
                        "tag": "button",
                        "role": "menuitem",
                        "aria_haspopup": "menu",
                        "aria_expanded": "false",
                        "connected": True,
                    },
                    "original_connected": False,
                    "reacquired_parent_count": 1,
                    "reacquired_same_node": False,
                    "node_replaced": True,
                    "hit_target": None,
                    "hit_owned_by_parent": False,
                }
            ],
            "lifecycle": [
                {
                    "offset_ms": 7,
                    "reason": "mutation",
                    "original_connected": False,
                    "current_node_ids": ["download-node-2"],
                    "same_node": False,
                }
            ],
        }

        trace = FlowSession._normalize_download_parent_probe_trace(raw)

        self.assertTrue(trace["summary"]["node_replacement_observed"])
        self.assertFalse(trace["checkpoints"][0]["reacquired_same_node"])
        self.assertEqual(trace["lifecycle"][0]["current_node_ids"], ["download-node-2"])

    def test_download_ui_probe_always_removes_parent_trace_after_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        session._page_cdp_session = MagicMock()
        session._browser = MagicMock()
        candidate = CandidateRecord(
            variant=1,
            suggested_filename="0002__prompt_0002_visual_001__v01.png",
            source_kind="rendered-image",
            candidate_id="00000000-0000-4000-8000-000000000005",
            download_status="failed",
        )
        image = MagicMock()
        tile = MagicMock()
        parent = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        neutral = {
            "cleanup_neutral_state": True,
            "remaining_visible_menu_count": 0,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
            "cleanup_poll_count": 1,
        }
        trace = {
            "installed": True,
            "cleaned_up": True,
            "cleanup_error_count": 0,
            "events": [],
            "aria_timeline": [],
            "checkpoints": [],
            "lifecycle": [],
            "summary": {"node_replacement_observed": False},
        }

        def open_menu(_candidate_id, _image, _tile, diagnostics):
            diagnostics["timing_ms"] = {
                "toolbar_reveal": 1.0,
                "more_options_activation": 1.0,
                "download_parent_acquisition": 1.0,
            }
            diagnostics["more_options_before_activation"] = {
                "reveal_attempt": 1,
                "prior_reveal_outcomes": [],
            }
            return parent, parent_snapshot

        def fail_exposure(_candidate_id, _parent, _snapshot, *, diagnostics):
            self.assertTrue(diagnostics["_passive_parent_trace_enabled"])
            raise FlowAutomationError("DOWNLOAD_SUBMENU_NOT_FOUND: passive trace test")

        with patch.object(session, "open", return_value=session), patch.object(
            session, "close"
        ), patch.object(session, "_assert_download_context"), patch.object(
            session,
            "_download_ui_probe_page_state",
            return_value={"document_has_focus": False, "document_visibility_state": "hidden"},
        ), patch.object(
            session,
            "_exact_candidate_tile",
            return_value=(candidate.candidate_id, image, tile),
        ), patch.object(
            session, "_open_candidate_menu", side_effect=open_menu
        ), patch.object(
            session, "_install_download_parent_probe_trace"
        ) as install, patch.object(
            session, "_remove_download_parent_probe_trace", return_value=trace
        ) as remove, patch.object(
            session, "_expose_download_submenu", side_effect=fail_exposure
        ), patch.object(
            session, "_dismiss_download_ui_probe_menus", return_value=neutral
        ), patch.object(
            session, "_wait_for_current_1k_original_size_option"
        ) as one_k, patch.object(
            session, "generate_one"
        ) as generate, patch.object(
            session, "_download_with_cdp"
        ) as native_download, patch.object(
            session, "download_candidate"
        ) as download_candidate:
            report = session.probe_download_ui(
                candidate=candidate,
                label="x1-minimized-parent-trace-1",
                samples=1,
            )

        sample = report["samples"][0]
        self.assertEqual(sample["status"], "failure")
        self.assertIs(sample["download_parent_trace"], trace)
        install.assert_called_once_with(parent)
        remove.assert_called_once_with(page)
        one_k.assert_not_called()
        generate.assert_not_called()
        native_download.assert_not_called()
        download_candidate.assert_not_called()
        session._browser.new_browser_cdp_session.assert_not_called()
        page.bring_to_front.assert_not_called()

    def test_download_parent_probe_checkpoints_use_the_actual_activation_center(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        control = MagicMock()
        snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            bounding_box_samples=[
                {"x": 420.0, "y": 315.0, "width": 180.0, "height": 48.0},
                {"x": 420.0, "y": 315.0, "width": 180.0, "height": 48.0},
            ],
        )
        current = {"x": 420.0, "y": 315.0, "width": 180.0, "height": 48.0}
        diagnostics = {"_passive_parent_trace_enabled": True}

        with patch.object(
            session,
            "_current_download_control_hit",
            return_value=current,
        ), patch.object(
            session,
            "_download_parent_probe_trace_checkpoint",
        ) as checkpoint:
            result = session._activate_download_control(
                control,
                "mouse",
                prepared_snapshot=snapshot,
                diagnostic_trace=diagnostics,
            )

        expected_pointer = {"x": 510.0, "y": 339.0}
        self.assertEqual(result["center"], expected_pointer)
        self.assertEqual(page.mouse.method_calls[0].args, (510.0, 339.0))
        self.assertEqual(page.mouse.method_calls[1].kwargs, {"button": "left"})
        self.assertEqual(page.mouse.method_calls[2].kwargs, {"button": "left"})
        self.assertEqual(
            [call.args[1] for call in checkpoint.call_args_list],
            [
                "before_activation_move",
                "after_activation_move",
                "before_down",
                "after_down",
                "before_up",
                "after_up",
            ],
        )
        self.assertTrue(
            all(call.args[2] == expected_pointer for call in checkpoint.call_args_list)
        )

    def test_download_parent_probe_is_passive_and_production_path_does_not_enable_it(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with patch.object(
            session, "_download_parent_probe_trace_checkpoint"
        ) as checkpoint:
            session._maybe_checkpoint_download_parent_probe(
                MagicMock(), {}, "before_down", {"x": 1.0, "y": 2.0}
            )
        checkpoint.assert_not_called()

        production_source = inspect.getsource(FlowSession.download_candidate)
        self.assertNotIn("_install_download_parent_probe_trace", production_source)
        self.assertNotIn("_passive_parent_trace_enabled", production_source)
        diagnostic_source = "\n".join(
            inspect.getsource(member)
            for member in (
                FlowSession._install_download_parent_probe_trace,
                FlowSession._download_parent_probe_trace_checkpoint,
                FlowSession._remove_download_parent_probe_trace,
                FlowSession.probe_download_ui,
            )
        )
        for forbidden in (
            "force=",
            ".click(",
            "dispatchEvent",
            ".bring_to_front(",
            "bringToFront",
            ".focus(",
        ):
            self.assertNotIn(forbidden, diagnostic_source)
        self.assertIn("removeEventListener", diagnostic_source)
        self.assertIn("observer?.disconnect", diagnostic_source)

    def test_download_ui_probe_cleanup_positively_polls_for_neutral_state(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        visible = {
            "remaining_visible_menu_count": 1,
            "remaining_visible_submenu_count": 1,
            "cleanup_probe_error_count": 0,
        }
        neutral = {
            "remaining_visible_menu_count": 0,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
        }
        with patch.object(
            session,
            "_download_ui_probe_visible_menu_counts",
            side_effect=[visible, neutral],
        ) as counts:
            result = session._dismiss_download_ui_probe_menus(page)

        self.assertTrue(result["cleanup_neutral_state"])
        self.assertEqual(result["remaining_visible_menu_count"], 0)
        self.assertEqual(result["remaining_visible_submenu_count"], 0)
        self.assertEqual(result["cleanup_poll_count"], 2)
        self.assertEqual(counts.call_count, 2)
        self.assertEqual(page.keyboard.press.call_count, 2)

    def test_download_ui_probe_records_non_neutral_cleanup_without_download(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        session._page_cdp_session = MagicMock()
        session._browser = MagicMock()
        page.evaluate.return_value = {
            "hasFocus": True,
            "visibilityState": "visible",
        }
        candidate = CandidateRecord(
            variant=1,
            suggested_filename="0028__acceptance_0028_visual_001__v01.png",
            source_kind="rendered-image",
            candidate_id="00000000-0000-4000-8000-000000000002",
            download_status="failed",
        )
        control = MagicMock()
        snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
        )
        before = {
            "cleanup_neutral_state": True,
            "remaining_visible_menu_count": 0,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
            "cleanup_poll_count": 1,
        }
        after = {
            "cleanup_neutral_state": False,
            "remaining_visible_menu_count": 1,
            "remaining_visible_submenu_count": 0,
            "cleanup_probe_error_count": 0,
            "cleanup_poll_count": 10,
        }
        submenu_diagnostics = {
            "parent_settle_poll_count": 1,
            "poll_count": 1,
            "aria_controls": "mat-menu-panel-28",
        }

        def open_menu(_candidate_id, _image, _tile, diagnostics):
            diagnostics["timing_ms"] = {
                "toolbar_reveal": 1.0,
                "more_options_activation": 1.0,
                "download_parent_acquisition": 1.0,
            }
            diagnostics["more_options_before_activation"] = {
                "reveal_attempt": 1,
                "prior_reveal_outcomes": [],
            }
            return control, snapshot

        with patch.object(session, "open", return_value=session), patch.object(
            session, "close"
        ), patch.object(session, "_assert_download_context"), patch.object(
            session,
            "_exact_candidate_tile",
            return_value=(candidate.candidate_id, MagicMock(), MagicMock()),
        ), patch.object(
            session, "_open_candidate_menu", side_effect=open_menu
        ), patch.object(
            session,
            "_expose_download_submenu",
            return_value=(MagicMock(), submenu_diagnostics),
        ), patch.object(
            session,
            "_wait_for_current_1k_original_size_option",
            return_value=(MagicMock(), snapshot),
        ), patch.object(
            session,
            "_dismiss_download_ui_probe_menus",
            side_effect=[before, after],
        ), patch.object(
            session, "generate_one"
        ) as generate, patch.object(
            session, "_download_with_cdp"
        ) as native_download:
            report = session.probe_download_ui(
                candidate=candidate,
                label="covered-unfocused-v2",
                samples=1,
            )

        sample = report["samples"][0]
        self.assertEqual(sample["status"], "success")
        self.assertFalse(sample["cleanup_neutral_state"])
        self.assertEqual(sample["remaining_visible_menu_count"], 1)
        self.assertEqual(
            sample["menu_cleanup_warning"],
            "DIAGNOSTIC_MENU_CLEANUP_NOT_NEUTRAL",
        )
        self.assertFalse(report["safety"]["one_k_activated"])
        self.assertFalse(report["safety"]["native_download_tracking_configured"])
        generate.assert_not_called()
        native_download.assert_not_called()

    def test_unresolved_submenu_after_bounded_open_wait_fails_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = {"x": 100.1, "y": 200.0, "width": 140.0, "height": 24.0}
        with patch.object(
            session,
            "_wait_for_associated_download_submenu",
            side_effect=[None, None],
        ), patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=parent_snapshot,
        ), patch.object(
            session,
            "_current_download_control_hit",
            return_value=current_box,
        ), patch.object(
            session,
            "_activate_download_control",
        ) as activate, self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_NOT_FOUND: no single safely related visible submenu appeared",
        ):
            session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        activate.assert_called_once_with(
            parent,
            "mouse",
            prepared_snapshot=parent_snapshot,
        )

    def test_prepared_download_parent_skips_duplicate_settle_when_still_ready(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "role": "menuitem",
            "aria-haspopup": "menu",
            "aria-expanded": "false",
        }.get(name)
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = parent_snapshot["bounding_box_samples"][-1]
        submenu = MagicMock()
        diagnostics = {}
        current_facts = {
            "semantic_download_parent_count": 1,
            "current_download_parent": {
                "connected": True,
                "visible": True,
                "enabled": True,
                "pointer_events": "auto",
            },
        }

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], current_facts),
        ), patch.object(
            session,
            "_current_download_control_hit",
            return_value=current_box,
        ) as hit, patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
        ) as settle, patch.object(
            session,
            "_wait_for_associated_download_submenu",
            return_value=submenu,
        ):
            selected, _ = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
                diagnostics=diagnostics,
            )

        self.assertIs(selected, submenu)
        hit.assert_called_once_with(parent, current_box)
        settle.assert_not_called()
        page.mouse.move.assert_called_once_with(
            current_box["x"] + current_box["width"] / 2,
            current_box["y"] + current_box["height"] / 2,
        )

    def test_changed_prepared_download_parent_keeps_full_settle_fallback(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        parent = MagicMock()
        parent.get_attribute.side_effect = lambda name: {
            "role": "menuitem",
            "aria-haspopup": "menu",
            "aria-expanded": "false",
        }.get(name)
        parent_snapshot = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            has_aria_controls=False,
        )
        current_box = parent_snapshot["bounding_box_samples"][-1]
        submenu = MagicMock()

        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_current_download_control_hit",
            side_effect=FlowAutomationError("DOWNLOAD_CONTROL_BBOX_UNSTABLE"),
        ), patch.object(
            session,
            "_wait_for_closed_download_submenu_parent",
            return_value=(parent, parent_snapshot, current_box),
        ) as settle, patch.object(
            session,
            "_wait_for_associated_download_submenu",
            return_value=submenu,
        ):
            selected, _ = session._expose_download_submenu(
                "candidate-id",
                parent,
                parent_snapshot,
            )

        self.assertIs(selected, submenu)
        settle.assert_called_once_with(unittest.mock.ANY)
        self.assertEqual(
            inspect.signature(
                FlowSession._wait_for_closed_download_submenu_parent
            ).parameters["timeout_ms"].default,
            1_500,
        )

    def test_unstable_closed_parent_fails_after_bounded_settle_poll(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        unstable = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            bounding_box_samples=[
                {"x": 100.0, "y": 200.0, "width": 140.0, "height": 24.0},
                {"x": 112.0, "y": 200.0, "width": 140.0, "height": 24.0},
            ],
        )
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=unstable,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0, 0, 1],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE:.*DOWNLOAD_CONTROL_BBOX_UNSTABLE",
        ):
            session._wait_for_closed_download_submenu_parent({}, timeout_ms=250)

        session.page.mouse.move.assert_not_called()

    def test_obscured_closed_parent_fails_after_bounded_settle_poll(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        parent = MagicMock()
        obscured = download_control_snapshot(
            aria_haspopup="menu",
            aria_expanded="false",
            center_top_belongs_to_control=False,
        )
        with patch.object(
            session,
            "_current_download_submenu_parents",
            return_value=([parent], {"semantic_download_parent_count": 1}),
        ), patch.object(
            session,
            "_inspect_download_menu_item",
            return_value=obscured,
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0, 0, 1],
        ), self.assertRaisesRegex(
            FlowAutomationError,
            "DOWNLOAD_SUBMENU_PARENT_NOT_ACTIONABLE:.*DOWNLOAD_CONTROL_OVERLAID",
        ):
            session._wait_for_closed_download_submenu_parent({}, timeout_ms=250)

        session.page.mouse.move.assert_not_called()

    def test_exact_1k_original_size_requires_one_visible_enabled_stable_item(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        submenu = MagicMock()
        exact = MagicMock()
        submenu.get_by_role.return_value.all.return_value = [exact]
        snapshot = download_control_snapshot(visible_text="1K Original size")
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), patch.object(
            session, "_inspect_download_menu_item", return_value=snapshot
        ):
            control, result = session._exact_1k_original_size_option(submenu)

        self.assertIs(control, exact)
        self.assertEqual(result["visible_text"], "1K Original size")
        role_call = submenu.get_by_role.call_args
        self.assertEqual(role_call.args[0], "menuitem")
        self.assertRegex("1K Original size", role_call.kwargs["name"])

    def test_1k_original_size_missing_disabled_and_ambiguous_fail_closed(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        submenu = MagicMock()

        submenu.get_by_role.return_value.all.return_value = []
        with self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_1K_OPTION_NOT_FOUND"):
            session._exact_1k_original_size_option(submenu)

        disabled = MagicMock()
        submenu.get_by_role.return_value.all.return_value = [disabled]
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": False},
        ), self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_1K_OPTION_DISABLED"):
            session._exact_1k_original_size_option(submenu)

        submenu.get_by_role.return_value.all.return_value = [MagicMock(), MagicMock()]
        with patch.object(
            session,
            "_live_control_facts",
            return_value={"connected": True, "visible": True, "enabled": True},
        ), self.assertRaisesRegex(FlowAutomationError, "DOWNLOAD_1K_OPTION_AMBIGUOUS"):
            session._exact_1k_original_size_option(submenu)

    def test_multiple_native_downloads_from_one_final_activation_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)
            wait_count = 0

            def emit_duplicates(_milliseconds):
                nonlocal wait_count
                wait_count += 1
                if wait_count == 1:
                    first = Path(tmp) / "guid-one"
                    first.write_bytes(b"first download")
                    handlers["Browser.downloadWillBegin"](
                        {"guid": "guid-one", "suggestedFilename": "one.jpeg"}
                    )
                    handlers["Browser.downloadProgress"](
                        {
                            "guid": "guid-one",
                            "state": "completed",
                            "filePath": str(first),
                        }
                    )
                elif wait_count == 2:
                    handlers["Browser.downloadWillBegin"](
                        {"guid": "guid-two", "suggestedFilename": "two.jpeg"}
                    )

            page.wait_for_timeout.side_effect = emit_duplicates
            with patch.object(
                session, "_activate_download_control"
            ) as activate, self.assertRaisesRegex(
                FlowAutomationError, "MULTIPLE_DOWNLOADS_FOR_CANDIDATE"
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    prepared_snapshot=download_control_snapshot(
                        visible_text="1K Original size"
                    ),
                    timeout_seconds=1,
                )

            activate.assert_called_once()
            cdp.send.assert_any_call(
                "Browser.setDownloadBehavior",
                {"behavior": "default", "eventsEnabled": False},
            )
            cdp.detach.assert_called_once_with()

    def test_cdp_completion_queued_during_activation_is_read_before_poll_sleep(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def emit_completion(*_args, **_kwargs):
                guid_path = Path(tmp) / "guid-ready"
                guid_path.write_bytes(b"completed download")
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-ready", "suggestedFilename": "ready.jpeg"}
                )
                handlers["Browser.downloadProgress"](
                    {
                        "guid": "guid-ready",
                        "state": "completed",
                        "filePath": str(guid_path),
                    }
                )

            with patch.object(
                session,
                "_activate_download_control",
                side_effect=emit_completion,
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0.0, 0.0, 1.0],
            ):
                result = session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

            self.assertEqual(result.browser_download_guid, "guid-ready")
            self.assertEqual(destination.read_bytes(), b"completed download")
            page.wait_for_timeout.assert_not_called()

    def test_cdp_download_completion_moves_correlated_guid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            browser = MagicMock()
            session._browser = browser
            cdp = MagicMock()
            browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            timeline = []

            def register_handler(name, handler):
                timeline.append(f"on:{name}")
                handlers[name] = handler

            def send_command(name, parameters):
                timeline.append(f"send:{name}:{parameters.get('behavior', '')}")

            cdp.on.side_effect = register_handler
            cdp.send.side_effect = send_command
            emitted = False

            def emit_completion(_milliseconds):
                nonlocal emitted
                if emitted:
                    return
                emitted = True
                guid_path = Path(tmp) / "guid-one"
                guid_path.write_bytes(b"completed download")
                handlers["Browser.downloadWillBegin"](
                    {
                        "guid": "guid-one",
                        "suggestedFilename": "flow-output.png",
                        "url": "https://example.invalid/signed-value",
                    }
                )
                handlers["Browser.downloadProgress"](
                    {
                        "guid": "guid-one",
                        "state": "completed",
                        "filePath": str(guid_path),
                    }
                )

            page.wait_for_timeout.side_effect = emit_completion
            with patch.object(
                session,
                "_activate_download_control",
                side_effect=lambda *_args, **_kwargs: timeline.append("mouse_click"),
            ) as click:
                result = session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )
            self.assertEqual(destination.read_bytes(), b"completed download")
            self.assertEqual(result.browser_download_guid, "guid-one")
            self.assertEqual(result.browser_suggested_filename, "flow-output.png")
            self.assertEqual(result.mechanism, "cdp_download_events")
            click.assert_called_once()
            cdp.send.assert_any_call(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(Path(tmp).resolve()),
                    "eventsEnabled": True,
                },
            )
            cdp.send.assert_any_call(
                "Browser.setDownloadBehavior",
                {"behavior": "default", "eventsEnabled": False},
            )
            cdp.detach.assert_called_once_with()
            self.assertLess(
                timeline.index("on:Browser.downloadWillBegin"),
                timeline.index("send:Browser.setDownloadBehavior:allowAndName"),
            )
            self.assertLess(
                timeline.index("on:Browser.downloadProgress"),
                timeline.index("send:Browser.setDownloadBehavior:allowAndName"),
            )
            self.assertLess(
                timeline.index("send:Browser.setDownloadBehavior:allowAndName"),
                timeline.index("mouse_click"),
            )

    def test_cdp_event_registration_uses_wrappable_callbacks_and_browser_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            browser = MagicMock()
            session._browser = browser
            cdp = MagicMock()
            browser.new_browser_cdp_session.return_value = cdp
            handlers = {}

            def register_handler(event, handler):
                # Playwright's sync wrapper attaches bookkeeping to callbacks.
                # This reproduces the property that rejected ``list.append``
                # without depending on Playwright's private attribute name.
                setattr(handler, "_callback_registration_probe", True)
                handlers[event] = handler

            cdp.on.side_effect = register_handler
            with patch.object(
                session,
                "_activate_download_control",
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_WILL_BEGIN_TIMEOUT",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=0,
                )

            browser.new_browser_cdp_session.assert_called_once_with()
            page.context.new_cdp_session.assert_not_called()
            self.assertEqual(
                set(handlers),
                {"Browser.downloadWillBegin", "Browser.downloadProgress"},
            )
            self.assertTrue(
                all(
                    handler._callback_registration_probe
                    for handler in handlers.values()
                )
            )
            cdp.detach.assert_called_once_with()

    def test_cdp_missing_begin_event_never_commits_a_new_completed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            session.page = MagicMock()
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp

            def create_missed_file(*_args, **_kwargs):
                (Path(tmp) / "chrome-finished.png").write_bytes(b"missed event data")

            with patch.object(
                session,
                "_activate_download_control",
                side_effect=create_missed_file,
            ):
                with self.assertRaisesRegex(
                    FlowAutomationError,
                    "DOWNLOAD_WILL_BEGIN_TIMEOUT:.*download_will_begin_count=0;.*"
                    "staging_complete=chrome-finished.png",
                ):
                    session._download_with_cdp(
                        candidate_id="candidate-id",
                        download_action=MagicMock(),
                        destination=destination,
                        timeout_seconds=0,
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(
                (Path(tmp) / "chrome-finished.png").read_bytes(),
                b"missed event data",
            )

    def test_cdp_download_timeout_and_partial_file_fail_clearly(self):
        for partial, expected in (
            (False, "DOWNLOAD_WILL_BEGIN_TIMEOUT"),
            (True, "STAGING_DOWNLOAD_INCOMPLETE"),
        ):
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "v01.download"
                session = FlowSession(PROJECT_URL, Path("profile"))
                session.page = MagicMock()
                cdp = MagicMock()
                session._browser = MagicMock()
                session._browser.new_browser_cdp_session.return_value = cdp

                def click(*_args, **_kwargs):
                    if partial:
                        (Path(tmp) / "guid.crdownload").write_bytes(b"partial")

                with patch.object(
                    session,
                    "_activate_download_control",
                    side_effect=click,
                ), self.assertRaisesRegex(FlowAutomationError, expected):
                    session._download_with_cdp(
                        candidate_id="candidate-id",
                        download_action=MagicMock(),
                        destination=destination,
                        timeout_seconds=0,
                    )
                self.assertFalse(destination.exists())

    def test_cdp_download_cancelled_reports_correlated_event_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def emit_cancelled(_milliseconds):
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-cancel", "suggestedFilename": "candidate.png"}
                )
                handlers["Browser.downloadProgress"](
                    {"guid": "guid-cancel", "state": "canceled"}
                )

            page.wait_for_timeout.side_effect = emit_cancelled
            with patch.object(
                session,
                "_activate_download_control",
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_CANCELLED:.*download_will_begin_count=1;.*"
                "download_progress_count=1; final_cdp_state=canceled",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

    def test_cdp_download_progress_timeout_is_distinct_from_begin_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def emit_in_progress(_milliseconds):
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-progress", "suggestedFilename": "candidate.png"}
                )
                handlers["Browser.downloadProgress"](
                    {"guid": "guid-progress", "state": "inProgress"}
                )

            page.wait_for_timeout.side_effect = emit_in_progress
            with patch.object(
                session,
                "_activate_download_control",
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0, 0, 1],
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_PROGRESS_TIMEOUT:.*guid=guid-progress;.*"
                "download_progress_count=1; final_cdp_state=inProgress",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

    def test_cdp_completed_event_without_file_reports_staging_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def emit_completed_without_file(_milliseconds):
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-missing", "suggestedFilename": "candidate.png"}
                )
                handlers["Browser.downloadProgress"](
                    {"guid": "guid-missing", "state": "completed"}
                )

            page.wait_for_timeout.side_effect = emit_completed_without_file
            with patch.object(
                session,
                "_activate_download_control",
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0, 0, 1],
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "STAGING_FILE_MISSING:.*guid=guid-missing;.*final_cdp_state=completed",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

    def test_visible_download_menu_after_click_reports_action_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            download_action = MagicMock()
            download_action.is_visible.return_value = True
            with patch.object(
                session,
                "_activate_download_control",
            ) as activate, patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0, 0, 1],
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_ACTION_NOT_ACCEPTED:.*menu_item_visible_after_trigger=yes",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=download_action,
                    destination=destination,
                    timeout_seconds=1,
                )
            self.assertEqual(activate.call_count, 1)
            self.assertEqual(activate.call_args.args[1], "mouse")

    def test_minimized_semantic_1k_uses_one_keyboard_activation_and_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            source = Path(tmp) / "guid-minimized"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            page_cdp = MagicMock()
            page_cdp.send.return_value = {
                "bounds": {"windowState": "minimized"}
            }
            session._page_cdp_session = page_cdp
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def accepted(_control, method, **_kwargs):
                self.assertEqual(method, "keyboard")
                source.write_bytes(b"completed")
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-minimized", "suggestedFilename": "candidate.jpeg"}
                )
                handlers["Browser.downloadProgress"](
                    {
                        "guid": "guid-minimized",
                        "state": "completed",
                        "filePath": str(source),
                    }
                )

            with patch.object(
                session,
                "_activate_download_control",
                side_effect=accepted,
            ) as activate, patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0, 0, 1],
            ):
                result = session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    prepared_snapshot=download_control_snapshot(
                        visible_text="1K Original size"
                    ),
                )

            self.assertEqual(activate.call_count, 1)
            self.assertEqual(result.browser_download_guid, "guid-minimized")
            self.assertEqual(destination.read_bytes(), b"completed")

    def test_minimized_nonsemantic_mouse_rejection_is_specific_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            page_cdp = MagicMock()
            page_cdp.send.return_value = {
                "bounds": {"windowState": "minimized"}
            }
            session._page_cdp_session = page_cdp
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            download_action = MagicMock()
            download_action.is_visible.return_value = True

            with patch.object(
                session,
                "_activate_download_control",
            ) as activate, patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=[0, 0, 1],
            ), self.assertRaisesRegex(
                FlowAutomationError,
                "DOWNLOAD_ACTION_NOT_ACCEPTED_MINIMIZED:.*"
                "activation_method=mouse;.*activation_acknowledged=no;.*"
                "menu_item_visible_after_trigger=yes;.*window_state=minimized",
            ):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=download_action,
                    destination=destination,
                    prepared_snapshot=download_control_snapshot(
                        role="button",
                        visible_text="1K Original size",
                    ),
                    timeout_seconds=1,
                )

            self.assertEqual(activate.call_count, 1)
            self.assertEqual(activate.call_args.args[1], "mouse")

    def test_cdp_download_file_must_remain_in_project_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            download_dir = Path(tmp) / "staging"
            download_dir.mkdir()
            outside = Path(tmp) / "outside-guid"
            outside.write_bytes(b"outside")
            destination = download_dir / "v01.download"
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            session.page = page
            cdp = MagicMock()
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            handlers = {}
            cdp.on.side_effect = lambda name, handler: handlers.__setitem__(name, handler)

            def emit_outside(_milliseconds):
                handlers["Browser.downloadWillBegin"](
                    {"guid": "guid-outside", "suggestedFilename": "candidate.png"}
                )
                handlers["Browser.downloadProgress"](
                    {
                        "guid": "guid-outside",
                        "state": "completed",
                        "filePath": str(outside),
                    }
                )

            page.wait_for_timeout.side_effect = emit_outside
            with patch.object(
                session,
                "_activate_download_control",
            ), self.assertRaisesRegex(FlowAutomationError, "STAGING_PATH_ESCAPED"):
                session._download_with_cdp(
                    candidate_id="candidate-id",
                    download_action=MagicMock(),
                    destination=destination,
                    timeout_seconds=1,
                )

    def test_bounded_download_trigger_trace_captures_only_safe_structural_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate_id = "00000000-0000-4000-8000-000000000003"
            staging_dir = Path(tmp) / "trace-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            context = MagicMock()
            page.context = context
            main_frame = MagicMock()
            main_frame.url = PROJECT_URL
            page.main_frame = main_frame
            browser = MagicMock()
            cdp = MagicMock()
            browser.new_browser_cdp_session.return_value = cdp
            session.page = page
            session._browser = browser
            page_handlers = {}
            context_handlers = {}
            cdp_handlers = {}
            page.on.side_effect = lambda name, handler: page_handlers.__setitem__(name, handler)
            context.on.side_effect = lambda name, handler: context_handlers.__setitem__(name, handler)
            cdp.on.side_effect = lambda name, handler: cdp_handlers.__setitem__(name, handler)

            hook_events = [
                {
                    "kind": "fetch_call",
                    "offset_ms": 1,
                    "method": "GET",
                    "resource_type": "fetch",
                    "url": {
                        "scheme": "https",
                        "host": "flow.google.com",
                        "path_shape": "/asb/[opaque]",
                        "query_redacted": True,
                        "fragment_redacted": False,
                        "candidate_id_in_path": False,
                    },
                },
                {
                    "kind": "blob_created",
                    "offset_ms": 2,
                    "object_type": "image/png",
                    "object_size": 123,
                    "url": {
                        "scheme": "blob",
                        "host": "flow.google.com",
                        "path_shape": "/[blob]",
                        "query_redacted": False,
                        "fragment_redacted": False,
                        "candidate_id_in_path": False,
                    },
                },
                {
                    "kind": "anchor_programmatic_click",
                    "offset_ms": 3,
                    "tag": "a",
                    "download_attribute": "candidate.png",
                },
                {
                    "kind": "dom_status",
                    "offset_ms": 4,
                    "role": "status",
                    "matched_keyword": "saved",
                    "private_text": "must not survive normalization",
                },
            ]

            request = MagicMock()
            request.url = (
                f"https://flow-content.google/image/{candidate_id}"
                "?token=do-not-record#private"
            )
            request.method = "GET"
            request.resource_type = "image"
            request.failure = None
            response = MagicMock()
            response.request = request
            response.status = 200
            response.header_value.side_effect = lambda name: {
                "content-type": "image/png",
                "content-disposition": (
                    'attachment; filename="candidate.png"; token=do-not-record'
                ),
            }[name]
            popup = MagicMock()
            popup.url = "https://flow.google.com/asb/popup-opaque-value?token=do-not-record"
            new_page = MagicMock()
            new_page.url = "https://flow.google.com/asb/new-page-opaque-value?token=do-not-record"
            options = MagicMock()
            download_action = MagicMock()
            download_action.is_visible.return_value = False
            click_count = 0

            def mouse_click(control):
                nonlocal click_count
                click_count += 1

            def activate(control, activation, *, prepared_snapshot):
                self.assertIs(control, download_action)
                self.assertEqual(activation, "mouse")
                self.assertEqual(prepared_snapshot["role"], "menuitem")
                page_handlers["request"](request)
                page_handlers["response"](response)
                page_handlers["requestfinished"](request)
                page_handlers["requestfailed"](request)
                page_handlers["popup"](popup)
                page_handlers["framenavigated"](main_frame)
                context_handlers["page"](new_page)
                cdp_handlers["Browser.downloadWillBegin"](
                    {
                        "guid": "guid-one",
                        "suggestedFilename": "candidate.png",
                        "url": request.url,
                    }
                )
                cdp_handlers["Browser.downloadProgress"](
                    {
                        "guid": "guid-one",
                        "state": "completed",
                        "receivedBytes": 123,
                        "totalBytes": 123,
                        "filePath": str(staging_dir / "guid-one"),
                    }
                )
                (staging_dir / "guid-one").write_bytes(b"trace evidence")
                return {"strategy": "mouse", "input": "mouse_move_down_up"}

            def open_menu(_candidate_id, _image, _tile, diagnostics):
                diagnostics["more_options_before_activation"] = {
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                }
                diagnostics["menu_visible_after_options_activation"] = True
                mouse_click(options)
                return download_action, download_control_snapshot()

            clock = 0

            def monotonic():
                nonlocal clock
                clock += 1
                return float(clock)

            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=(candidate_id, MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=open_menu,
            ), patch.object(
                session,
                "_unique_download_menu_item",
                return_value=download_action,
            ), patch.object(
                session,
                "_inspect_download_menu_item",
                return_value=download_control_snapshot(),
            ), patch.object(
                session,
                "_mouse_click_control",
                side_effect=mouse_click,
            ), patch.object(
                session,
                "_install_download_trace_hooks",
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                return_value={
                    "events": [
                        normalized
                        for raw in hook_events
                        if (normalized := _normalize_trace_hook_event(raw)) is not None
                    ],
                    "final": {
                        "menu_connected": False,
                        "menu_visible": False,
                        "target_connected": False,
                        "target_visible": False,
                        "active_element": {"tag": "body", "inside_menu": False},
                    },
                },
            ), patch.object(
                session,
                "_activate_download_control",
                side_effect=activate,
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=monotonic,
            ):
                report = session.trace_download_trigger(
                    candidate=CandidateRecord(
                        variant=1,
                        suggested_filename="candidate.png",
                        source_kind="rendered-image",
                        candidate_id=candidate_id,
                    ),
                    staging_dir=staging_dir,
                    activation="mouse",
                    timeout_seconds=1,
                )

            self.assertEqual(click_count, 1)
            self.assertTrue(report["ui"]["download_click_dispatched"])
            self.assertTrue(report["ui"]["activation_dispatched"])
            self.assertEqual(report["outcome"], "DOWNLOAD_ACTION_ACCEPTED")
            self.assertEqual(report["correlation"]["outcome"], "exact_candidate_id")
            self.assertIn("native_browser_download", report["observed_mechanisms"])
            self.assertIn("fetch", report["observed_mechanisms"])
            self.assertIn("blob_url", report["observed_mechanisms"])
            self.assertIn("anchor", report["observed_mechanisms"])
            self.assertEqual(report["staging_files"][0]["name"], "guid-one")
            self.assertTrue(report["cleanup"]["hooks_restored"])
            self.assertTrue(report["cleanup"]["download_behavior_restored"])
            event_kinds = {event["kind"] for event in report["events"]}
            self.assertTrue(
                {
                    "request",
                    "response",
                    "requestfinished",
                    "requestfailed",
                    "popup",
                    "new_page",
                    "frame_navigation",
                    "fetch_call",
                    "blob_created",
                    "anchor_programmatic_click",
                    "dom_status",
                    "cdp_download_will_begin",
                    "cdp_download_progress",
                }.issubset(event_kinds)
            )
            response_event = next(
                event for event in report["events"] if event["kind"] == "response"
            )
            self.assertEqual(response_event["status"], 200)
            self.assertEqual(response_event["content_type"], "image/png")
            self.assertIn("token=[redacted]", response_event["content_disposition"])
            cdp.send.assert_any_call(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(staging_dir.resolve()),
                    "eventsEnabled": True,
                },
            )
            serialized = json.dumps(report)
            self.assertNotIn("do-not-record", serialized)
            self.assertNotIn("private_text", serialized)

    def test_bounded_download_trigger_trace_reports_no_meaningful_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "trace-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            session.page = page
            session._browser = MagicMock()
            download_action = MagicMock()
            download_action.is_visible.return_value = False
            clock = 0

            def monotonic():
                nonlocal clock
                clock += 1
                return float(clock)

            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=(
                    "00000000-0000-4000-8000-000000000003",
                    MagicMock(),
                    MagicMock(),
                ),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=lambda _candidate_id, _image, _tile, diagnostics: (
                    diagnostics.update(
                        {
                            "more_options_before_activation": {
                                "connected": True,
                                "visible": True,
                                "enabled": True,
                            },
                            "menu_visible_after_options_activation": True,
                        }
                    )
                    or (download_action, download_control_snapshot())
                ),
            ), patch.object(
                session,
                "_unique_download_menu_item",
                return_value=download_action,
            ), patch.object(
                session,
                "_inspect_download_menu_item",
                return_value=download_control_snapshot(),
            ), patch.object(
                session,
                "_mouse_click_control",
            ), patch.object(
                session,
                "_install_download_trace_hooks",
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                return_value={
                    "events": [],
                    "final": {
                        "menu_connected": True,
                        "menu_visible": True,
                        "target_connected": True,
                        "target_visible": True,
                        "active_element": {"tag": "button", "inside_menu": True},
                    },
                },
            ), patch.object(
                session,
                "_activate_download_control",
                return_value={"strategy": "keyboard", "input": "keyboard", "key": "Enter"},
            ), patch(
                "nano_batch.adapters.flow.time.monotonic",
                side_effect=monotonic,
            ):
                report = session.trace_download_trigger(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    activation="keyboard",
                    timeout_seconds=1,
                )
            self.assertEqual(report["status"], "trace_complete")
            self.assertEqual(report["outcome"], "DOWNLOAD_ACTION_NOT_ACCEPTED")
            self.assertEqual(report["events"], [])

    def test_early_candidate_menu_failure_still_restores_hooks_and_download_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "trace-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            browser = MagicMock()
            cdp = MagicMock()
            browser.new_browser_cdp_session.return_value = cdp
            session.page = page
            session._browser = browser
            hook_result = {
                "events": [],
                "found_keys": [],
                "restored": True,
                "final": {},
            }
            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=FlowAutomationError("primary candidate menu failure"),
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                return_value=hook_result,
            ) as restore_hooks, patch.object(
                session,
                "_activate_download_control",
            ) as activate:
                report = session.trace_download_trigger(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    activation="mouse",
                    timeout_seconds=1,
                )

            self.assertEqual(report["failure"]["message"], "primary candidate menu failure")
            self.assertEqual(report["failure"]["stage"], "open_candidate_menu")
            self.assertTrue(report["cleanup"]["hooks_restore_attempted"])
            self.assertTrue(report["cleanup"]["hooks_restored"])
            self.assertTrue(report["cleanup"]["download_behavior_restore_attempted"])
            self.assertTrue(report["cleanup"]["download_behavior_restored"])
            restore_hooks.assert_called_once_with(page)
            cdp.send.assert_called_once_with(
                "Browser.setDownloadBehavior",
                {"behavior": "default", "eventsEnabled": False},
            )
            activate.assert_not_called()

    def test_hook_cleanup_failure_does_not_prevent_download_restore_or_mask_primary_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "trace-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            cdp = MagicMock()
            session.page = page
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=FlowAutomationError("primary candidate menu failure"),
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                side_effect=RuntimeError("hook cleanup failed"),
            ):
                report = session.trace_download_trigger(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    activation="mouse",
                    timeout_seconds=1,
                )

            self.assertEqual(report["failure"]["message"], "primary candidate menu failure")
            self.assertFalse(report["cleanup"]["hooks_restored"])
            self.assertEqual(report["cleanup"]["hook_restore_error"], "RuntimeError")
            self.assertTrue(report["cleanup"]["download_behavior_restored"])
            cdp.send.assert_called_once()

    def test_download_cleanup_failure_does_not_prevent_hook_restore_or_mask_primary_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "trace-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            cdp = MagicMock()
            cdp.send.side_effect = RuntimeError("download cleanup failed")
            session.page = page
            session._browser = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_open_candidate_menu",
                side_effect=FlowAutomationError("primary candidate menu failure"),
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                return_value={"events": [], "found_keys": [], "restored": True, "final": {}},
            ) as restore_hooks:
                report = session.trace_download_trigger(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    activation="mouse",
                    timeout_seconds=1,
                )

            self.assertEqual(report["failure"]["message"], "primary candidate menu failure")
            self.assertTrue(report["cleanup"]["hooks_restored"])
            restore_hooks.assert_called_once_with(page)
            self.assertFalse(report["cleanup"]["download_behavior_restored"])
            self.assertEqual(
                report["cleanup"]["download_behavior_restore_error"],
                "RuntimeError",
            )

    def test_manual_download_trace_is_passive_and_reports_idle_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "manual-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            session.page = page
            session._browser = MagicMock()
            cdp = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            clock = 0.0

            def monotonic():
                nonlocal clock
                clock += 0.25
                return clock

            idle_hooks = {
                "events": [],
                "found_keys": ["manual_v1"],
                "restored": True,
                "final": {
                    "menu_ever_opened": False,
                    "download_control_seen": False,
                    "download_activation_observed": False,
                    "menu_open_before_activation": None,
                    "menu_visible": False,
                    "target_visible": False,
                    "active_element": {"tag": "body", "inside_menu": False},
                },
            }
            ready = MagicMock()
            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session, "_install_manual_download_trace_hooks"
            ) as install_hooks, patch.object(
                session, "_remove_download_trace_hooks", return_value=idle_hooks
            ) as remove_hooks, patch.object(
                session, "_open_candidate_menu"
            ) as open_menu, patch.object(
                session, "_reveal_candidate_options"
            ) as reveal_options, patch.object(
                session, "_activate_download_control"
            ) as activate, patch.object(
                session, "_mouse_click_control"
            ) as mouse_click, patch(
                "nano_batch.adapters.flow.time.monotonic", side_effect=monotonic
            ):
                report = session.trace_download_manual(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    timeout_seconds=1,
                    ready_callback=ready,
                )

            ready.assert_called_once_with()
            install_hooks.assert_called_once_with(
                page, "00000000-0000-4000-8000-000000000003"
            )
            remove_hooks.assert_called_once_with(page)
            open_menu.assert_not_called()
            reveal_options.assert_not_called()
            activate.assert_not_called()
            mouse_click.assert_not_called()
            page.mouse.move.assert_not_called()
            page.mouse.down.assert_not_called()
            page.mouse.up.assert_not_called()
            page.keyboard.press.assert_not_called()
            self.assertGreaterEqual(page.wait_for_timeout.call_count, 1)
            self.assertEqual(report["outcome"], "MANUAL_TRACE_NO_DOWNLOAD_ACTIVATION")
            self.assertFalse(report["ui"]["automated_ui_actions_dispatched"])
            self.assertEqual(report["comparison"]["download_input_sequence"], [])
            self.assertTrue(report["cleanup"]["hooks_restored"])
            self.assertTrue(report["cleanup"]["download_behavior_restored"])

    def test_manual_download_trace_records_physical_sequence_and_native_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "manual-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            session.page = page
            session._browser = MagicMock()
            cdp = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            cdp_handlers = {}
            cdp.on.side_effect = lambda name, handler: cdp_handlers.__setitem__(name, handler)
            clock = 0.0

            def monotonic():
                nonlocal clock
                clock += 0.25
                return clock

            raw_events = [
                {
                    "kind": "candidate_menu_request",
                    "offset_ms": 1,
                    "tag": "button",
                    "is_trusted": True,
                },
                {
                    "kind": "menu_state",
                    "offset_ms": 2,
                    "menu_visible": True,
                    "menu_connected": True,
                    "live_menu_count": 1,
                    "download_match_count": 1,
                },
                {
                    "kind": "download_control_seen",
                    "offset_ms": 3,
                    "tag": "button",
                    "role": "menuitem",
                    "visible_text": "downloadDownload",
                    "disabled": False,
                    "target_connected": True,
                    "target_visible": True,
                    "pointer_events": "auto",
                    "center_top_belongs_to_control": True,
                    "bounding_box": {"x": 100, "y": 200, "width": 140, "height": 24},
                },
            ]
            for index, event_type in enumerate(
                ("pointerdown", "mousedown", "pointerup", "mouseup", "click"), start=4
            ):
                raw_events.append(
                    {
                        "kind": "input_event",
                        "offset_ms": index,
                        "event_type": event_type,
                        "target_scope": "download",
                        "is_trusted": True,
                        "tag": "span",
                        "role": "menuitem",
                        "target_label": "Download",
                        "default_prevented": False,
                        "button": 0,
                        "buttons": 1 if event_type in {"pointerdown", "mousedown"} else 0,
                    }
                )
            raw_events.extend(
                [
                    {
                        "kind": "download_control_activation",
                        "offset_ms": 9,
                        "tag": "button",
                        "role": "menuitem",
                        "is_trusted": True,
                    },
                    {
                        "kind": "menu_state",
                        "offset_ms": 10,
                        "menu_visible": False,
                        "menu_connected": False,
                        "live_menu_count": 0,
                        "download_match_count": 0,
                    },
                ]
            )
            normalized_events = [
                normalized
                for event in raw_events
                if (normalized := _normalize_trace_hook_event(event)) is not None
            ]
            hook_result = {
                "events": normalized_events,
                "found_keys": ["manual_v1"],
                "restored": True,
                "final": {
                    "menu_ever_opened": True,
                    "download_control_seen": True,
                    "download_activation_observed": True,
                    "menu_open_before_activation": True,
                    "menu_visible": False,
                    "target_visible": False,
                    "active_element_before": {"tag": "button", "inside_menu": True},
                    "active_element": {"tag": "body", "inside_menu": False},
                },
            }

            def user_window_opened():
                cdp_handlers["Browser.downloadWillBegin"](
                    {
                        "guid": "manual-guid",
                        "suggestedFilename": "candidate.png",
                        "url": "https://flow-content.google/image/00000000-0000-4000-8000-000000000003?secret=no",
                    }
                )
                cdp_handlers["Browser.downloadProgress"](
                    {
                        "guid": "manual-guid",
                        "state": "completed",
                        "receivedBytes": 14,
                        "totalBytes": 14,
                        "filePath": str(staging_dir / "manual-guid"),
                    }
                )
                (staging_dir / "manual-guid").write_bytes(b"diagnostic only")

            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session, "_install_manual_download_trace_hooks"
            ), patch.object(
                session, "_remove_download_trace_hooks", return_value=hook_result
            ), patch.object(
                session, "_open_candidate_menu"
            ) as open_menu, patch.object(
                session, "_activate_download_control"
            ) as activate, patch(
                "nano_batch.adapters.flow.time.monotonic", side_effect=monotonic
            ):
                report = session.trace_download_manual(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    timeout_seconds=1,
                    ready_callback=user_window_opened,
                )

            open_menu.assert_not_called()
            activate.assert_not_called()
            self.assertEqual(report["outcome"], "DOWNLOAD_ACTION_ACCEPTED")
            self.assertEqual(
                report["comparison"]["download_input_sequence"],
                ["pointerdown", "mousedown", "pointerup", "mouseup", "click"],
            )
            self.assertTrue(report["comparison"]["all_download_inputs_trusted"])
            self.assertFalse(report["comparison"]["any_download_input_default_prevented"])
            self.assertTrue(report["ui"]["menu_ever_opened"])
            self.assertFalse(report["ui"]["menu_open_after_activation"])
            self.assertEqual(report["staging_files"][0]["name"], "manual-guid")
            self.assertIn("native_browser_download", report["observed_mechanisms"])
            self.assertFalse((staging_dir.parent / "assets").exists())
            serialized = json.dumps(report)
            self.assertNotIn("secret=no", serialized)

    def test_download_submenu_inspection_waits_for_user_and_dispatches_zero_ui_actions(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        session._browser = MagicMock()
        empty = _normalize_download_submenu_snapshot({})
        detected = _normalize_download_submenu_snapshot(
            {
                "menus": [
                    {
                        "index": 0,
                        "tag": "div",
                        "role": "menu",
                        "connected": True,
                        "visible": True,
                        "items": [
                            {
                                "index": 0,
                                "tag": "button",
                                "role": "menuitem",
                                "visible_text": "downloadDownloadchevron_right",
                                "aria_haspopup": "menu",
                                "aria_expanded": "true",
                                "connected": True,
                                "visible": True,
                            }
                        ],
                    },
                    {
                        "index": 1,
                        "tag": "div",
                        "role": "menu",
                        "connected": True,
                        "visible": True,
                        "items": [
                            {
                                "index": 0,
                                "tag": "button",
                                "role": "menuitem",
                                "visible_text": "Full size",
                                "connected": True,
                                "visible": True,
                            }
                        ],
                    },
                ],
                "download_parent": {
                    "match_count": 1,
                    "parent_menu_index": 0,
                    "parent_item_index": 0,
                    "aria_haspopup": "menu",
                    "aria_expanded": "true",
                    "relationship": {
                        "status": "inferred_visible_sibling",
                        "basis": ["visible_sibling_with_submenu_indicator"],
                        "submenu_menu_indices": [1],
                    },
                },
                "submenu_detected": True,
            }
        )
        ready = MagicMock()
        clock = 0.0

        def monotonic():
            nonlocal clock
            clock += 0.05
            return clock

        with patch.object(
            session,
            "_exact_candidate_tile",
            return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
        ), patch.object(
            session,
            "_inspect_download_submenu_dom",
            side_effect=[empty, detected, detected, detected],
        ) as inspect_dom, patch.object(
            session, "_open_candidate_menu"
        ) as open_menu, patch.object(
            session, "_reveal_candidate_options"
        ) as reveal_options, patch.object(
            session, "_activate_download_control"
        ) as activate, patch.object(
            session, "_mouse_click_control"
        ) as mouse_click, patch.object(
            session, "generate_one"
        ) as generate, patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=monotonic
        ):
            report = session.inspect_download_submenu(
                candidate=self.download_candidate,
                timeout_seconds=2,
                ready_callback=ready,
            )

        ready.assert_called_once_with()
        self.assertEqual(inspect_dom.call_count, 4)
        open_menu.assert_not_called()
        reveal_options.assert_not_called()
        activate.assert_not_called()
        mouse_click.assert_not_called()
        generate.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.keyboard.press.assert_not_called()
        self.assertFalse(report["automated_ui_actions_dispatched"])
        self.assertEqual(report["outcome"], "DOWNLOAD_SUBMENU_OBSERVED")
        self.assertEqual(report["snapshot"]["visible_menu_count"], 2)
        self.assertEqual(report["submenu_stable_polls"], 3)
        self.assertTrue(report["cleanup"]["session_closed"])

    def test_download_submenu_inspection_idle_timeout_is_bounded(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        session._browser = MagicMock()
        clock = 0.0

        def monotonic():
            nonlocal clock
            clock += 0.25
            return clock

        with patch.object(
            session,
            "_exact_candidate_tile",
            return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
        ), patch.object(
            session,
            "_inspect_download_submenu_dom",
            return_value=_normalize_download_submenu_snapshot({}),
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=monotonic
        ):
            report = session.inspect_download_submenu(
                candidate=self.download_candidate,
                timeout_seconds=1,
            )

        self.assertEqual(report["outcome"], "DOWNLOAD_SUBMENU_NOT_OBSERVED")
        self.assertGreaterEqual(report["sample_count"], 1)
        self.assertGreaterEqual(page.wait_for_timeout.call_count, 1)
        page.keyboard.press.assert_not_called()

    def test_download_submenu_inspection_preserves_parent_to_replacement_menu_transition(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.url = PROJECT_URL
        session.page = page
        session._browser = MagicMock()
        parent = _normalize_download_submenu_snapshot(
            {
                "menus": [
                    {
                        "index": 0,
                        "tag": "div",
                        "role": "menu",
                        "connected": True,
                        "visible": True,
                        "items": [
                            {
                                "index": 0,
                                "tag": "button",
                                "role": "menuitem",
                                "visible_text": "downloadDownloadchevron_right",
                                "aria_haspopup": "menu",
                                "connected": True,
                                "visible": True,
                                "icons": [{"tag": "mat-icon", "text": "chevron_right"}],
                            }
                        ],
                    }
                ],
                "download_parent": {
                    "match_count": 1,
                    "parent_menu_index": 0,
                    "parent_item_index": 0,
                    "aria_haspopup": "menu",
                    "submenu_indicator": [{"tag": "mat-icon", "text": "chevron_right"}],
                    "relationship": {"status": "unresolved"},
                },
            }
        )
        replacement = _normalize_download_submenu_snapshot(
            {
                "menus": [
                    {
                        "index": 0,
                        "tag": "div",
                        "role": "menu",
                        "connected": True,
                        "visible": True,
                        "items": [
                            {
                                "index": 0,
                                "tag": "button",
                                "role": "menuitem",
                                "visible_text": "Full size",
                                "connected": True,
                                "visible": True,
                            }
                        ],
                    }
                ],
                "download_parent": {
                    "match_count": 0,
                    "relationship": {"status": "absent"},
                },
            }
        )
        clock = 0.0
        calls = 0

        def monotonic():
            nonlocal clock
            clock += 0.2
            return clock

        def inspect(_page):
            nonlocal calls
            calls += 1
            return parent if calls == 1 else replacement

        with patch.object(
            session,
            "_exact_candidate_tile",
            return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
        ), patch.object(
            session, "_inspect_download_submenu_dom", side_effect=inspect
        ), patch(
            "nano_batch.adapters.flow.time.monotonic", side_effect=monotonic
        ):
            report = session.inspect_download_submenu(
                candidate=self.download_candidate,
                timeout_seconds=1,
            )

        self.assertEqual(
            report["outcome"], "DOWNLOAD_SUBMENU_CANDIDATE_TRANSITION_OBSERVED"
        )
        self.assertTrue(report["temporal_menu_transition"]["observed"])
        self.assertEqual(len(report["observations"]), 2)
        self.assertEqual(
            report["observations"][1]["snapshot"]["menus"][0]["items"][0][
                "visible_text"
            ],
            "Full size",
        )

    def test_manual_trace_hook_failure_still_restores_download_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "manual-staging"
            staging_dir.mkdir()
            session = FlowSession(PROJECT_URL, Path("profile"))
            page = MagicMock()
            page.url = PROJECT_URL
            page.context = MagicMock()
            session.page = page
            session._browser = MagicMock()
            cdp = MagicMock()
            session._browser.new_browser_cdp_session.return_value = cdp
            with patch.object(
                session,
                "_exact_candidate_tile",
                return_value=("00000000-0000-4000-8000-000000000003", MagicMock(), MagicMock()),
            ), patch.object(
                session,
                "_install_manual_download_trace_hooks",
                side_effect=RuntimeError("hook install failed"),
            ), patch.object(
                session,
                "_remove_download_trace_hooks",
                return_value={"events": [], "found_keys": [], "restored": True, "final": {}},
            ) as remove_hooks:
                report = session.trace_download_manual(
                    candidate=self.download_candidate,
                    staging_dir=staging_dir,
                    timeout_seconds=1,
                )

            self.assertEqual(report["outcome"], "TRACE_FAILED")
            self.assertEqual(report["failure"]["stage"], "arm_passive_observers")
            remove_hooks.assert_called_once_with(page)
            cdp.send.assert_called_once_with(
                "Browser.setDownloadBehavior",
                {"behavior": "default", "eventsEnabled": False},
            )
            self.assertTrue(report["cleanup"]["download_behavior_restored"])
            page.keyboard.press.assert_not_called()

    @staticmethod
    def _readiness_project():
        project_id = PROJECT_URL.rsplit("/", 1)[-1]
        return FlowProjectReference(project_id=project_id, project_url=PROJECT_URL)

    def _readiness_session(self, observations, *, page_url=PROJECT_URL):
        page = MagicMock()
        page.url = page_url
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page
        inspector = MagicMock(side_effect=observations)
        session.inspect_environment = inspector
        return session, page, inspector

    def test_editor_readiness_immediate_success_continues_without_wait(self):
        ready = flow_inspection()
        session, page, inspector = self._readiness_session([ready])

        observed = session._wait_for_authenticated_image_editor(
            self._readiness_project(), timeout_seconds=1, poll_interval_ms=250
        )

        self.assertIs(observed, ready)
        inspector.assert_called_once_with(stabilize=False)
        page.wait_for_timeout.assert_not_called()
        self.assertEqual(session._editor_readiness_observation["outcome"], "ready")
        self.assertTrue(session._editor_readiness_observation["exact_project"])

    def test_editor_readiness_reacquires_prompt_editor_until_it_appears(self):
        absent = flow_inspection(prompt_control_candidates=(), editable_controls=())
        ready = flow_inspection()
        session, page, inspector = self._readiness_session([absent, ready])

        observed = session._wait_for_authenticated_image_editor(
            self._readiness_project(), timeout_seconds=1, poll_interval_ms=250
        )

        self.assertIs(observed, ready)
        self.assertEqual(inspector.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(150)

    def test_editor_readiness_reacquires_generation_control_until_it_appears(self):
        absent = flow_inspection(generation_action_controls=())
        ready = flow_inspection()
        session, page, inspector = self._readiness_session([absent, ready])

        observed = session._wait_for_authenticated_image_editor(
            self._readiness_project(), timeout_seconds=1, poll_interval_ms=400
        )

        self.assertIs(observed, ready)
        self.assertEqual(inspector.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(150)

    def test_editor_readiness_tolerates_angular_remount_and_reacquires(self):
        ready = flow_inspection()
        session, page, inspector = self._readiness_session(
            [RuntimeError("detached during Angular remount"), ready]
        )

        observed = session._wait_for_authenticated_image_editor(
            self._readiness_project(), timeout_seconds=1, poll_interval_ms=300
        )

        self.assertIs(observed, ready)
        self.assertEqual(inspector.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(300)

    def test_editor_readiness_timeout_fails_closed_with_safe_diagnostics(self):
        loading = flow_inspection(
            current_url=PROJECT_URL + "?token=never-log#private",
            authentication_state="authenticated_or_unknown",
            document_ready_state="loading",
            visible_body_text_length=0,
            prompt_control_candidates=(),
            editable_controls=(),
            generation_action_controls=(),
        )
        session, page, inspector = self._readiness_session([loading, loading])

        with self.assertRaisesRegex(
            FlowAutomationError, "authenticated Flow editor was not ready"
        ) as raised:
            session._wait_for_authenticated_image_editor(
                self._readiness_project(),
                timeout_seconds=0.7,
                poll_interval_ms=350,
            )

        self.assertEqual(inspector.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(350)
        diagnostic = session._editor_readiness_observation
        self.assertEqual(diagnostic["outcome"], "timeout")
        self.assertEqual(diagnostic["current_url"], PROJECT_URL)
        self.assertEqual(diagnostic["prompt_editor_count"], 0)
        self.assertEqual(diagnostic["generation_control_count"], 0)
        self.assertEqual(diagnostic["poll_count"], 2)
        self.assertNotIn("never-log", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_editor_readiness_wrong_project_fails_before_dom_poll(self):
        wrong_url = "https://flow.google.com/project/00000000-0000-4000-8000-000000000006"
        session, page, inspector = self._readiness_session([], page_url=wrong_url)

        with self.assertRaisesRegex(FlowAutomationError, "left saved Flow project"):
            session._wait_for_authenticated_image_editor(self._readiness_project())

        inspector.assert_not_called()
        page.wait_for_timeout.assert_not_called()

    def test_editor_readiness_login_and_access_failures_are_terminal(self):
        cases = (
            (
                flow_inspection(authentication_state="sign_in_required"),
                "sign-in is required",
            ),
            (
                flow_inspection(loading_or_error_states=("access_denied",)),
                "access to the saved project was denied",
            ),
            (
                flow_inspection(loading_or_error_states=("project_unavailable",)),
                "saved Flow project is unavailable",
            ),
        )
        for inspection, message in cases:
            with self.subTest(message=message):
                session, page, inspector = self._readiness_session([inspection])
                with self.assertRaisesRegex(FlowAutomationError, message):
                    session._wait_for_authenticated_image_editor(
                        self._readiness_project()
                    )
                inspector.assert_called_once_with(stabilize=False)
                page.wait_for_timeout.assert_not_called()

    def test_editor_readiness_is_passive_and_never_consumes_retry_state(self):
        session, page, _inspector = self._readiness_session([flow_inspection()])

        session._wait_for_authenticated_image_editor(self._readiness_project())

        page.click.assert_not_called()
        page.hover.assert_not_called()
        page.goto.assert_not_called()
        page.keyboard.press.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        source = inspect.getsource(FlowSession._wait_for_authenticated_image_editor)
        self.assertNotIn("submit_prompt", source)
        self.assertNotIn("attempt_count", source)
        self.assertNotIn("RunStateStore", source)
        self.assertNotIn("click(", source)
        self.assertNotIn("fill(", source)
        self.assertNotIn("press(", source)

    def test_editor_readiness_accepts_pro_and_lite_without_model_interaction(self):
        for model in (
            "Nano Banana 2",
            "Nano Banana 2 Lite",
            "Nano Banana Lite",
            "Nano Banana Pro",
        ):
            with self.subTest(model=model):
                inspection = flow_inspection(
                    model_labels=(model,),
                    model_related_text=(model,),
                )
                session, page, _inspector = self._readiness_session([inspection])
                ready = session._wait_for_authenticated_image_editor(
                    self._readiness_project()
                )
                configuration = session.configure_generation(
                    self.settings,
                    inspection=ready,
                )

                self.assertEqual(configuration["model"], model)
                self.assertEqual(session.flow_url, PROJECT_URL)
                page.click.assert_not_called()
                page.hover.assert_not_called()
                page.keyboard.press.assert_not_called()

    def test_informational_model_preserves_exact_full_specific_name(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        for model in (
            "Nano Banana 2",
            "Nano Banana 2 Lite",
            "Nano Banana Lite",
            "Nano Banana Pro",
        ):
            with self.subTest(model=model):
                labels = _composer_model_labels(
                    [composer_model_control(innerText=f"Current model: {model}")]
                )
                detection = session.detect_image_mode(
                    self.settings,
                    inspection=flow_inspection(
                        model_labels=labels,
                        model_related_text=labels,
                    ),
                )

                self.assertEqual(detection.mode, "image")
                self.assertEqual(detection.confidence, "inferred")
                self.assertEqual(detection.evidence["model"], model)
                self.assertEqual(detection.evidence["aspect_ratio"], "crop_16_9")

    def test_shorter_model_name_cannot_steal_unknown_longer_name(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(
                model_labels=("Nano Banana 2 Experimental",),
                model_related_text=("Nano Banana 2 Experimental",),
            ),
        )

        self.assertEqual(detection.mode, "image")
        self.assertEqual(detection.evidence["model"], "unknown")
        self.assertNotEqual(detection.evidence["model"], "Nano Banana 2")

    def test_explicit_selected_image_mode_detection(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(selected_controls=("Image",)),
        )
        self.assertEqual(detection.mode, "image")
        self.assertEqual(detection.confidence, "explicit")
        self.assertEqual(detection.evidence["image_control"], "Image")
        self.assertEqual(detection.evidence["model"], "Nano Banana 2")
        self.assertEqual(detection.evidence["generation_control"], "Start generation")

    def test_inferred_image_mode_requires_all_positive_dom_evidence(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(),
        )
        self.assertEqual(detection.mode, "image")
        self.assertEqual(detection.confidence, "inferred")
        self.assertEqual(detection.evidence["model"], "Nano Banana 2")
        self.assertEqual(detection.evidence["aspect_ratio"], "crop_16_9")
        self.assertEqual(
            detection.evidence["prompt_control"],
            'div[contenteditable="true"] (What do you want to create?)',
        )
        self.assertEqual(detection.evidence["generation_control"], "Start generation")
        self.assertEqual(detection.evidence["variants"], "x2")

    def test_visible_composer_inner_text_recognizes_nano_banana_2(self):
        labels = _composer_model_labels(
            [composer_model_control(innerText="Model Nano Banana 2")]
        )
        detection = FlowSession(PROJECT_URL, Path("profile")).detect_image_mode(
            self.settings,
            inspection=flow_inspection(model_labels=labels, model_related_text=labels),
        )
        self.assertEqual(labels, ("Model Nano Banana 2",))
        self.assertEqual(detection.evidence["model"], "Nano Banana 2")

    def test_nested_visible_span_and_icon_text_recognizes_nano_banana_2(self):
        labels = _composer_model_labels(
            [
                composer_model_control(
                    innerText="",
                    nestedVisibleText=["Model icon", "Nano Banana 2", "Expand"],
                )
            ]
        )
        self.assertEqual(labels, ("Nano Banana 2",))

    def test_accessible_composer_label_recognizes_nano_banana_2(self):
        aria_labels = _composer_model_labels(
            [composer_model_control(ariaLabel="Current model: Nano Banana 2")]
        )
        labelled_by = _composer_model_labels(
            [composer_model_control(labelledByText="Nano Banana 2")]
        )
        self.assertEqual(aria_labels, ("Current model: Nano Banana 2",))
        self.assertEqual(labelled_by, ("Nano Banana 2",))

    def test_hidden_or_disconnected_model_controls_are_ignored(self):
        labels = _composer_model_labels(
            [
                composer_model_control(innerText="Nano Banana 2", visible=False),
                composer_model_control(innerText="Nano Banana Pro", connected=False),
            ]
        )
        self.assertEqual(labels, ())

    def test_historical_model_text_is_not_recorded_as_current_model(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(
                selected_controls=("Nano Banana 2",),
                model_labels=(),
                model_related_text=("Historical card: Nano Banana 2",),
            ),
        )
        self.assertEqual(detection.mode, "image")
        self.assertNotIn("model", detection.evidence)

    def test_inferred_mode_rejects_selected_video_evidence(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaisesRegex(FlowAutomationError, "video evidence"):
            session.detect_image_mode(
                self.settings,
                inspection=flow_inspection(selected_controls=("Video",)),
            )

    def test_nano_banana_pro_is_accepted_and_recorded_informationally(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        inspection = flow_inspection(
            model_labels=("Nano Banana Pro",),
            model_related_text=("Nano Banana Pro",),
        )
        detection = session.detect_image_mode(self.settings, inspection=inspection)
        self.assertEqual(detection.mode, "image")
        self.assertEqual(detection.evidence["model"], "Nano Banana Pro")

    def test_nano_banana_lite_is_accepted_and_recorded_informationally(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(
                model_labels=("Nano Banana Lite",),
                model_related_text=("Nano Banana Lite",),
            ),
        )
        self.assertEqual(detection.mode, "image")
        self.assertEqual(detection.evidence["model"], "Nano Banana Lite")
        self.assertIn(
            "lite|pro|flash",
            inspect.getsource(FlowSession.inspect_environment).casefold(),
        )

    def test_other_visible_image_model_is_accepted(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(
                model_labels=("Nano Banana Flash",),
                model_related_text=("Nano Banana Flash",),
            ),
        )
        self.assertEqual(detection.evidence["model"], "Nano Banana Flash")

    def test_unreadable_selected_model_is_nonfatal_and_not_recorded(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(model_labels=(), model_related_text=()),
        )
        self.assertEqual(detection.mode, "image")
        self.assertNotIn("model", detection.evidence)

    def test_informational_model_normalizes_only_harmless_case_and_whitespace(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        settings = GenerationSettings(preferred_model_hint="  nAnO   BaNaNa 2  ")
        detection = session.detect_image_mode(
            settings,
            inspection=flow_inspection(
                model_labels=("NANO     banana 2 crop_16_9 x2",),
                model_related_text=("NANO     banana 2 crop_16_9 x2",),
            ),
        )
        self.assertEqual(_normalize_model_name(detection.evidence["model"]), "nano banana 2")

    def test_inferred_mode_rejects_ambiguous_prompt_controls(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        prompt = flow_inspection().prompt_control_candidates[0]
        with self.assertRaisesRegex(FlowAutomationError, "exactly one active prompt editor"):
            session.detect_image_mode(
                self.settings,
                inspection=flow_inspection(prompt_control_candidates=(prompt, dict(prompt))),
            )

    def test_inferred_mode_rejects_ambiguous_generation_controls(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaisesRegex(
            FlowAutomationError, "structurally owned by the active prompt composer"
        ):
            session.detect_image_mode(
                self.settings,
                inspection=flow_inspection(
                    generation_action_controls=("Start generation", "Generate")
                ),
            )

    def test_inferred_mode_rejects_missing_aspect_ratio(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            session.detect_image_mode(
                self.settings,
                inspection=flow_inspection(aspect_ratio_controls=()),
            )

    def test_inferred_mode_rejects_unready_editor(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaisesRegex(FlowAutomationError, "editor is not ready"):
            session.detect_image_mode(
                self.settings,
                inspection=flow_inspection(document_ready_state="loading"),
            )

    def test_prompt_start_and_aspect_evidence_do_not_require_readable_model_name(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        detection = session.detect_image_mode(
            self.settings,
            inspection=flow_inspection(model_labels=(), model_related_text=()),
        )
        self.assertEqual(detection.confidence, "inferred")
        self.assertNotIn("model", detection.evidence)

    def test_inferred_configuration_reuses_visible_settings_without_clicking(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with patch.object(session, "inspect_environment", return_value=flow_inspection()), patch.object(
            session,
            "_choose_named_setting",
            side_effect=AssertionError("inferred current setting must not be changed"),
        ), patch.object(
            session,
            "_configure_variants_if_exposed",
            side_effect=AssertionError("inferred current variants must not be changed"),
        ):
            configuration = session.configure_generation(self.settings)
        self.assertEqual(configuration["mode_detection"]["confidence"], "inferred")
        self.assertEqual(configuration["model"], "Nano Banana 2")
        self.assertEqual(configuration["aspect_ratio"], "crop_16_9")
        self.assertEqual(configuration["configured_variants"], 2)

    def test_explicit_image_control_still_never_opens_or_changes_settings(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with patch.object(
            session,
            "inspect_environment",
            return_value=flow_inspection(selected_controls=("Image",)),
        ), patch.object(
            session,
            "_choose_named_setting",
            side_effect=AssertionError("aspect ratio must remain read-only"),
        ) as choose_setting, patch.object(
            session,
            "_configure_variants_if_exposed",
            side_effect=AssertionError("variant count must remain read-only"),
        ) as configure_variants:
            configuration = session.configure_generation(self.settings)

        self.assertEqual(configuration["mode_detection"]["confidence"], "explicit")
        self.assertEqual(configuration["model"], "Nano Banana 2")
        self.assertEqual(configuration["aspect_ratio"], "crop_16_9")
        self.assertEqual(configuration["configured_variants"], 2)
        choose_setting.assert_not_called()
        configure_variants.assert_not_called()

    def test_one_variant_configuration_requires_visible_x1_without_changing_settings(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        inspection = flow_inspection(
            generation_controls=("Nano Banana 2 crop_16_9 x1", "Start generation"),
            model_labels=("Nano Banana 2 crop_16_9 x1",),
            model_related_text=("Nano Banana 2 crop_16_9 x1",),
            aspect_ratio_controls=("Nano Banana 2 crop_16_9 x1",),
        )
        with patch.object(
            session,
            "inspect_environment",
            return_value=inspection,
        ), patch.object(
            session,
            "_configure_variants_if_exposed",
            side_effect=AssertionError("variant count must remain read-only"),
        ):
            configuration = session.configure_generation(
                GenerationSettings(variants=1)
            )

        self.assertEqual(configuration["requested_variants"], 1)
        self.assertEqual(configuration["configured_variants"], 1)

    def test_one_variant_configuration_rejects_visible_x2_without_changing_settings(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with patch.object(
            session,
            "inspect_environment",
            return_value=flow_inspection(),
        ), patch.object(
            session,
            "_configure_variants_if_exposed",
            side_effect=AssertionError("variant count must remain read-only"),
        ), self.assertRaisesRegex(FlowAutomationError, "match x1"):
            session.configure_generation(GenerationSettings(variants=1))

    def test_nano_banana_pro_reaches_normal_generation_path_without_model_change(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page
        with patch.object(
            FlowSession, "__enter__", return_value=session
        ), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session, "ensure_production_window_usable"
        ), patch.object(
            session,
            "inspect_environment",
            return_value=flow_inspection(
                generation_controls=("Nano Banana Pro crop_16_9 x2", "Start generation"),
                model_labels=("Nano Banana Pro crop_16_9 x2",),
                model_related_text=("Nano Banana Pro crop_16_9 x2",),
                aspect_ratio_controls=("Nano Banana Pro crop_16_9 x2",),
            ),
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session,
            "submit_prompt",
            return_value={"acknowledgement": {"signal": "progress"}},
        ) as submit, patch.object(
            session,
            "wait_for_result",
            return_value=(
                DiscoveredCandidate(source_kind="rendered-image", candidate_id="candidate-1"),
                DiscoveredCandidate(source_kind="rendered-image", candidate_id="candidate-2"),
            ),
        ) as wait, patch.object(
            session, "_stop_current_generation_attempt_observation"
        ):
            result = session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=self.settings,
            )

        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(json.loads(result.details)["model"], "Nano Banana Pro")
        submit.assert_called_once()
        wait.assert_called_once()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_generate_one_persists_bounded_observation_on_ordinary_generation_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(FlowSession, "__enter__", return_value=session), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session, "ensure_production_window_usable"
        ), patch.object(
            session, "configure_generation", return_value={"configured_variants": 1}
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session, "submit_prompt", return_value={
                "acknowledgement": {"signal": "new_generation_progress"},
                "ack_elapsed_ms": 140,
            }
        ), patch.object(
            session, "wait_for_result", side_effect=FlowAutomationError("No stable terminal image result")
        ) as wait, patch.object(
            session, "_generation_failure_observation", return_value={
                "phase": "result_wait", "current_candidate_count": 0
            }
        ) as diagnostic, patch.object(
            session, "_stop_current_generation_attempt_observation"
        ) as stop, self.assertRaises(FlowAutomationError) as raised:
            session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="private prompt"),
                settings=GenerationSettings(variants=1),
            )

        self.assertIn("No stable terminal image result", str(raised.exception))
        self.assertIn('generation_observation={"current_candidate_count":0', str(raised.exception))
        self.assertNotIn("private prompt", str(raised.exception))
        diagnostic.assert_called_once()
        wait.assert_called_once()
        stop.assert_called_once()
        session.page.mouse.down.assert_not_called()

    def test_generate_one_diagnostic_does_not_change_usage_limit_or_mask_failure(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        shared = [
            patch.object(FlowSession, "__enter__", return_value=session),
            patch.object(FlowSession, "__exit__", return_value=None),
            patch.object(session, "ensure_production_window_usable"),
            patch.object(session, "configure_generation", return_value={"configured_variants": 1}),
            patch.object(session, "_asset_snapshot", return_value=[]),
            patch.object(session, "_progress_signals", return_value=()),
            patch.object(session, "_stop_current_generation_attempt_observation"),
        ]
        with shared[0], shared[1], shared[2], shared[3], shared[4], shared[5], shared[6], patch.object(
            session, "submit_prompt", side_effect=FlowUsageLimitReached()
        ), patch.object(session, "_generation_failure_observation") as diagnostic, self.assertRaises(
            FlowUsageLimitReached
        ):
            session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )
        diagnostic.assert_not_called()

        with shared[0], shared[1], shared[2], shared[3], shared[4], shared[5], shared[6], patch.object(
            session, "submit_prompt", side_effect=FlowAutomationError("GENERATION_START_UNCONFIRMED")
        ), patch.object(
            session, "_generation_failure_observation", side_effect=RuntimeError("diagnostic unavailable")
        ), self.assertRaisesRegex(FlowAutomationError, "GENERATION_START_UNCONFIRMED") as raised:
            session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )
        self.assertNotIn("diagnostic unavailable", str(raised.exception))

    def test_generate_one_connectivity_exception_is_diagnosed_once_without_retry(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        with patch.object(FlowSession, "__enter__", return_value=session), patch.object(
            FlowSession, "__exit__", return_value=None
        ), patch.object(
            session, "ensure_production_window_usable"
        ), patch.object(
            session, "configure_generation", return_value={"configured_variants": 1}
        ), patch.object(
            session, "_asset_snapshot", return_value=[]
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session, "submit_prompt", return_value={
                "acknowledgement": {"signal": "new_generation_progress"},
                "ack_elapsed_ms": 100,
            }
        ) as submit, patch.object(
            session, "wait_for_result", side_effect=RuntimeError("page connection closed")
        ) as wait, patch.object(
            session, "_generation_failure_observation", return_value={
                "failure_category": "connectivity_or_page_failure"
            }
        ) as diagnostic, patch.object(
            session, "_stop_current_generation_attempt_observation"
        ), self.assertRaises(RuntimeError) as raised:
            session.generate_one(
                item=BatchItem(order=1, visual_id="v1", prompt="safe prompt"),
                settings=GenerationSettings(variants=1),
            )
        self.assertIn("connectivity_or_page_failure", str(raised.exception))
        submit.assert_called_once()
        wait.assert_called_once()
        diagnostic.assert_called_once()

    def test_production_configuration_has_no_automatic_model_selector(self):
        source = inspect.getsource(FlowSession.configure_generation)
        self.assertNotIn("_discover_or_select_model", source)
        self.assertNotIn("inspect_flow_model_menu", source)
        self.assertNotIn("_choose_named_setting", source)
        self.assertFalse(hasattr(FlowSession, "_discover_or_select_model"))

    def test_visible_url_strips_query_and_fragment(self):
        self.assertEqual(
            _safe_visible_url("https://assets.example/image.png?token=secret#fragment"),
            "https://assets.example/image.png",
        )

    def test_non_persistable_urls_are_omitted(self):
        self.assertIsNone(_safe_visible_url("blob:https://flow.google/secret"))
        self.assertIsNone(_safe_visible_url("data:image/png;base64,secret"))

    def test_download_trace_url_and_headers_redact_secrets(self):
        candidate_id = "00000000-0000-4000-8000-000000000003"
        safe_url = _sanitize_trace_url(
            "https://flow.google.com/asb/very-long-opaque-sensitive-value"
            "?token=do-not-record#private-fragment",
            candidate_id,
        )
        self.assertEqual(safe_url["scheme"], "https")
        self.assertEqual(safe_url["host"], "flow.google.com")
        self.assertEqual(safe_url["path_shape"], "/asb/[opaque]")
        self.assertTrue(safe_url["query_redacted"])
        self.assertTrue(safe_url["fragment_redacted"])
        self.assertNotIn("do-not-record", json.dumps(safe_url))
        self.assertIsNone(
            _sanitize_trace_url("https://unrelated.example/private?token=x", candidate_id)
        )
        safe_header = _safe_trace_header(
            'attachment; filename="candidate.png"; token=do-not-record'
        )
        self.assertIn('filename="candidate.png"', safe_header)
        self.assertIn("token=[redacted]", safe_header)
        self.assertNotIn("do-not-record", safe_header)

    def test_download_trace_correlation_is_exact_or_ambiguous(self):
        candidate_id = "00000000-0000-4000-8000-000000000003"
        exact_event = {
            "kind": "response",
            "candidate_bytes": True,
            "url": {"candidate_id_in_path": True},
        }
        exact = _correlate_download_trace(candidate_id, [exact_event])
        self.assertEqual(exact["outcome"], "exact_candidate_id")
        ambiguous = _correlate_download_trace(
            candidate_id,
            [exact_event, {"kind": "response", "candidate_bytes": True, "url": {}}],
        )
        self.assertEqual(ambiguous["outcome"], "ambiguous_multiple_qualifying_responses")

    def test_account_labels_and_email_addresses_are_redacted(self):
        self.assertEqual(
            _safe_label("Google Account: Person person@example.com"),
            "[redacted account control]",
        )
        self.assertEqual(
            _safe_label("Signed in as person@example.com"),
            "Signed in as [redacted-email]",
        )

    def test_debug_address_must_be_loopback(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1 only"):
            FlowSession(PROJECT_URL, Path("profile"), debug_address="0.0.0.0")

    def test_debug_launch_uses_normal_chrome_loopback_profile_and_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".flow_chrome_profile"
            chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            session = FlowSession(PROJECT_URL, profile)
            with patch.object(session, "_debug_endpoint_available", return_value=False), patch(
                "nano_batch.adapters.flow.find_installed_chrome", return_value=chrome
            ), patch("nano_batch.adapters.flow.subprocess.Popen") as popen:
                session.open_for_debugging()
            popen.assert_called_once_with(
                [
                    str(chrome),
                    f"--user-data-dir={profile.resolve()}",
                    "--remote-debugging-port=9222",
                    "--remote-debugging-address=127.0.0.1",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-features=CalculateNativeWinOcclusion",
                    PROJECT_URL,
                ],
                shell=False,
            )
            command = popen.call_args.args[0]
            self.assertNotIn("--enable-automation", command)
            self.assertFalse(any("headless" in argument.lower() for argument in command))
            self.assertFalse(any("stealth" in argument.lower() for argument in command))
            self.assertFalse(
                any("automationcontrolled" in argument.lower() for argument in command)
            )
            self.assertFalse(
                any(argument.lower().startswith("--user-agent") for argument in command)
            )

    def test_debug_launch_does_not_start_duplicate_when_endpoint_is_available(self):
        session = FlowSession(PROJECT_URL, Path(".flow_chrome_profile"))
        with patch.object(session, "_debug_endpoint_available", return_value=True), patch(
            "nano_batch.adapters.flow.find_installed_chrome"
        ) as find_chrome, patch("nano_batch.adapters.flow.subprocess.Popen") as popen:
            session.open_for_debugging()

        find_chrome.assert_not_called()
        popen.assert_not_called()

    def test_live_chrome_missing_background_flag_is_rejected_without_termination(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        cdp = MagicMock()
        cdp.send.return_value = {
            "arguments": [
                "chrome.exe",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ]
        }
        session._page_cdp_session = cdp

        with self.assertRaisesRegex(
            FlowAutomationError,
            "--disable-features=CalculateNativeWinOcclusion.*Close.*relaunch",
        ):
            session._verify_live_background_flags()

        self.assertFalse(session._required_background_flags_present)
        self.assertEqual(
            session._missing_background_flags,
            ("--disable-features=CalculateNativeWinOcclusion",),
        )

    def test_live_chrome_unknown_command_line_remains_compatible_and_unknown(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        cdp = MagicMock()
        cdp.send.side_effect = RuntimeError("method unavailable")
        session._page_cdp_session = cdp

        with self.assertWarnsRegex(RuntimeWarning, "flags are unknown"):
            session._verify_live_background_flags()

        self.assertIsNone(session._required_background_flags_present)
        self.assertEqual(session._missing_background_flags, ())

    def test_background_flag_parser_accepts_combined_disable_features(self):
        presence = FlowSession._background_flag_presence(
            [
                "chrome.exe",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=OtherFeature,CalculateNativeWinOcclusion",
            ]
        )

        self.assertIsNotNone(presence)
        self.assertTrue(all(presence.values()))

    def test_page_lifecycle_keepalive_is_target_scoped_and_never_focuses_window(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        cdp = MagicMock()
        session._page_cdp_session = cdp
        page = MagicMock()
        session.page = page

        session._enable_page_lifecycle_keepalive()

        cdp.send.assert_called_once_with(
            "Page.setWebLifecycleState", {"state": "active"}
        )
        self.assertTrue(session._page_lifecycle_keepalive_applied)
        page.bring_to_front.assert_not_called()

    def test_unsupported_page_lifecycle_keepalive_degrades_without_focus(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        cdp = MagicMock()
        cdp.send.side_effect = RuntimeError("unsupported")
        session._page_cdp_session = cdp
        page = MagicMock()
        session.page = page

        session._enable_page_lifecycle_keepalive()

        self.assertEqual(session._page_lifecycle_keepalive_applied, "unsupported")
        page.bring_to_front.assert_not_called()

    def test_debug_launch_accepts_canonical_flow_home_without_fixed_project(self):
        session = FlowSession("https://flow.google.com/", Path("profile"))

        command = session._debug_launch_command(Path("chrome.exe"))

        self.assertEqual(command[-1], "https://flow.google.com/")
        self.assertFalse(any("/project/" in argument for argument in command))

    def test_debug_launch_rejects_noncanonical_nonproject_destination(self):
        session = FlowSession("https://flow.google.com", Path("profile"))
        with self.assertRaisesRegex(Exception, "canonical https://flow.google.com/"):
            session._debug_launch_command(Path("chrome.exe"))

    def test_generic_debug_launch_preserves_existing_debug_chrome_tab(self):
        session = FlowSession("https://flow.google.com/", Path(".flow_chrome_profile"))
        with patch.object(session, "_debug_endpoint_available", return_value=True), patch(
            "nano_batch.adapters.flow.find_installed_chrome"
        ) as find_chrome, patch("nano_batch.adapters.flow.subprocess.Popen") as popen:
            session.open_for_debugging()

        find_chrome.assert_not_called()
        popen.assert_not_called()

    def test_locked_non_debug_profile_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            (profile / "SingletonLock").write_text("", encoding="utf-8")
            session = FlowSession(PROJECT_URL, profile)
            with patch.object(session, "_debug_endpoint_available", return_value=False), self.assertRaisesRegex(
                FlowProfileLockedError, "open-flow-debug"
            ):
                session.open_for_debugging()

    def test_cdp_connection_selects_configured_existing_tab_without_navigation(self):
        configured_page = PageStub(PROJECT_URL)
        other_page = PageStub("https://example.com")
        configured_page.context = MagicMock()
        page_cdp = configured_page.context.new_cdp_session.return_value
        page_cdp.send.side_effect = lambda method, params=None: {
            "Browser.getBrowserCommandLine": {
                "arguments": ["chrome.exe", *list(_REQUIRED_BACKGROUND_FLAGS)]
            },
            "Emulation.setFocusEmulationEnabled": {},
            "Page.setWebLifecycleState": {},
        }[method]
        configured_page.bring_to_front = MagicMock()
        browser = MagicMock()
        browser.contexts = [type("Context", (), {"pages": [other_page, configured_page]})()]
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        factory = MagicMock(return_value=starter)
        session = FlowSession(PROJECT_URL, Path(".flow_chrome_profile"))
        with patch.object(session, "_playwright_api", return_value=factory):
            session.open()
            self.assertIs(session.page, configured_page)
            playwright.chromium.connect_over_cdp.assert_called_once_with(
                "http://127.0.0.1:9222",
                timeout=10_000,
                is_local=True,
                no_defaults=True,
            )
            playwright.chromium.launch_persistent_context.assert_not_called()
            configured_page.context.new_cdp_session.assert_called_once_with(configured_page)
            page_cdp.send.assert_has_calls(
                [
                    call("Browser.getBrowserCommandLine"),
                    call("Emulation.setFocusEmulationEnabled", {"enabled": True}),
                    call("Page.setWebLifecycleState", {"state": "active"}),
                ]
            )
            configured_page.bring_to_front.assert_not_called()
            session.close()
        self.assertEqual(
            page_cdp.send.call_args_list[-1].args,
            ("Emulation.setFocusEmulationEnabled", {"enabled": False}),
        )
        page_cdp.detach.assert_called_once_with()
        browser.close.assert_called_once_with()

    def test_focus_emulation_failure_closes_connection_without_foregrounding(self):
        configured_page = PageStub(PROJECT_URL)
        configured_page.context = MagicMock()
        configured_page.bring_to_front = MagicMock()
        page_cdp = configured_page.context.new_cdp_session.return_value
        page_cdp.send.side_effect = RuntimeError("unsupported")
        browser = MagicMock()
        browser.contexts = [type("Context", (), {"pages": [configured_page]})()]
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        session = FlowSession(PROJECT_URL, Path(".flow_chrome_profile"))

        with patch.object(
            session,
            "_playwright_api",
            return_value=MagicMock(return_value=starter),
        ), self.assertRaisesRegex(FlowAutomationError, "focus emulation"):
            session.open()

        page_cdp.detach.assert_called_once_with()
        configured_page.bring_to_front.assert_not_called()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

    def test_unavailable_cdp_endpoint_fails_clearly(self):
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.side_effect = RuntimeError("connection refused")
        starter = MagicMock()
        starter.start.return_value = playwright
        factory = MagicMock(return_value=starter)
        session = FlowSession(PROJECT_URL, Path(".flow_chrome_profile"))
        with patch.object(session, "_playwright_api", return_value=factory), self.assertRaisesRegex(
            FlowDebugUnavailableError, "open-flow-debug"
        ):
            session.open()

    def test_no_matching_flow_project_tab_fails(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaises(FlowTabNotFoundError):
            session._select_flow_project_page(
                [PageStub("https://flow.google.com/"), PageStub("https://example.com")]
            )

    def test_duplicate_matching_flow_project_tabs_are_ambiguous(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        with self.assertRaises(FlowTabAmbiguousError):
            session._select_flow_project_page([PageStub(PROJECT_URL), PageStub(PROJECT_URL)])

    def test_configured_project_disambiguates_other_flow_projects(self):
        expected = PageStub(PROJECT_URL)
        session = FlowSession(PROJECT_URL, Path("profile"))
        selected = session._select_flow_project_page(
            [expected, PageStub("https://flow.google.com/project/another-project")]
        )
        self.assertIs(selected, expected)

    def test_project_reconnaissance_selects_one_current_flow_tab_without_old_id(self):
        current = PageStub("https://flow.google.com/project/new-manual-project")
        selected = FlowSession._select_flow_page_for_reconnaissance(
            [PageStub("https://example.com"), current]
        )
        self.assertIs(selected, current)
        with self.assertRaises(FlowTabAmbiguousError):
            FlowSession._select_flow_page_for_reconnaissance(
                [current, PageStub(PROJECT_URL)]
            )

    def test_read_only_project_reconnaissance_attach_has_no_focus_or_lifecycle_commands(self):
        page = PageStub("https://flow.google.com/project/new-manual-project")
        page.context = MagicMock()
        page.bring_to_front = MagicMock()
        page.goto = MagicMock()
        page.reload = MagicMock()
        browser = MagicMock()
        browser.contexts = [type("Context", (), {"pages": [page]})()]
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        session = FlowSession(PROJECT_URL, Path("profile"))

        with patch.object(
            session,
            "_playwright_api",
            return_value=MagicMock(return_value=starter),
        ):
            session.open_read_only_project_reconnaissance()
            self.assertIs(session.page, page)
            session.close()

        page.context.new_cdp_session.assert_not_called()
        page.bring_to_front.assert_not_called()
        page.goto.assert_not_called()
        page.reload.assert_not_called()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

    def test_project_identity_normalizes_query_and_fragment_and_rejects_non_project(self):
        identity = _flow_project_identity(
            PROJECT_URL + "?token=private#fragment"
        )
        self.assertEqual(identity["current_url"], PROJECT_URL)
        self.assertTrue(identity["is_flow_url"])
        self.assertTrue(identity["is_project_url"])
        self.assertEqual(
            identity["project_id"],
            "00000000-0000-4000-8000-000000000001",
        )

        home = _flow_project_identity("https://flow.google.com/?token=private")
        self.assertEqual(home["current_url"], "https://flow.google.com/")
        self.assertTrue(home["is_flow_url"])
        self.assertFalse(home["is_project_url"])
        self.assertIsNone(home["project_id"])

    def test_project_control_inventory_is_bounded_and_omits_private_evidence(self):
        safe = [
            {
                "tag": "button",
                "role": "button",
                "accessible_name": "New project",
                "aria_label": "New project",
                "title": None,
                "visible_text": None,
                "connected": True,
                "visible": True,
                "enabled": True,
                "bounding_box": {"x": i, "y": 2, "width": 20, "height": 20},
                "semantic_ancestor": {"role": "navigation", "name": "Projects"},
                "region": "header",
                "href": None,
                "reasons": ["creation_label"],
            }
            for i in range(60)
        ]
        private = [
            {
                "tag": "button",
                "role": "button",
                "accessible_name": "Manage your Google Account person@example.com",
                "visible_text": "person@example.com",
                "sensitive_context": True,
            },
            {
                "tag": "button",
                "role": "button",
                "accessible_name": "Create a private cinematic project with secret prompt",
                "visible_text": "Create a private cinematic project with secret prompt",
            },
            {
                "tag": "a",
                "role": "link",
                "href": "https://flow.google.com/asb/candidate-uuid?token=secret",
                "accessible_name": "candidate-uuid",
            },
        ]

        controls = _bounded_project_ui_controls(private + safe, 50)
        rendered = json.dumps(controls)
        self.assertEqual(len(controls), 50)
        self.assertNotIn("person@example.com", rendered)
        self.assertNotIn("secret prompt", rendered)
        self.assertNotIn("candidate-uuid", rendered)
        self.assertNotIn("token=secret", rendered)

    def test_project_ui_report_is_read_only_and_normalizes_current_url(self):
        page = MagicMock()
        page.url = PROJECT_URL + "?token=private#fragment"
        page.evaluate.return_value = {
            "candidates": [
                {
                    "tag": "button",
                    "role": "button",
                    "accessible_name": "New project",
                    "aria_label": "New project",
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                    "bounding_box": {"x": 10, "y": 20, "width": 30, "height": 40},
                    "region": "header",
                    "reasons": ["creation_label"],
                }
            ],
            "truncated": False,
        }
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        report = session.inspect_project_ui_controls(limit=50)

        self.assertEqual(report["current_url"], PROJECT_URL)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["aria_label"], "New project")
        page.click.assert_not_called()
        page.hover.assert_not_called()
        page.goto.assert_not_called()
        page.reload.assert_not_called()
        page.keyboard.press.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.screenshot.assert_not_called()

    def test_current_project_report_bounds_dialog_controls_without_values(self):
        page = MagicMock()
        page.url = PROJECT_URL + "?token=private#fragment"
        page.evaluate.return_value = {
            "ready_state": "complete",
            "prompt_surface_count": 1,
            "visible_dialog_count": 1,
            "naming_dialog_present": True,
            "modal_controls": [
                {
                    "tag": "button",
                    "role": "button",
                    "accessible_name": "Create project",
                    "aria_label": "Create project",
                    "connected": True,
                    "visible": True,
                    "enabled": True,
                    "bounding_box": {"x": i, "y": 2, "width": 20, "height": 20},
                    "semantic_ancestor": {"role": "dialog", "name": "New project"},
                    "region": "dialog",
                    "reasons": ["dialog_control"],
                    "private_value": "never emit this project title",
                }
                for i in range(30)
            ],
            "modal_controls_truncated": True,
        }
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        report = session.inspect_current_flow_project(dialog_limit=25)
        rendered = json.dumps(report)

        self.assertTrue(report["editor_ready"])
        self.assertTrue(report["naming_dialog_present"])
        self.assertEqual(report["dialog_control_count"], 25)
        self.assertNotIn("never emit", rendered)
        self.assertNotIn("token=private", rendered)
        page.click.assert_not_called()
        page.hover.assert_not_called()
        page.goto.assert_not_called()
        page.keyboard.press.assert_not_called()
        page.screenshot.assert_not_called()

    def test_current_flow_home_is_reported_as_non_project_even_with_prompt_surface(self):
        page = MagicMock()
        page.url = "https://flow.google.com/?source=private"
        page.evaluate.return_value = {
            "ready_state": "complete",
            "prompt_surface_count": 1,
            "visible_dialog_count": 0,
            "naming_dialog_present": False,
            "modal_controls": [],
            "modal_controls_truncated": False,
        }
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        report = session.inspect_current_flow_project()

        self.assertEqual(report["current_url"], "https://flow.google.com/")
        self.assertFalse(report["is_project_url"])
        self.assertIsNone(report["project_id"])
        self.assertFalse(report["editor_ready"])

    def test_environment_inspection_classifies_project_login_without_exposing_values(self):
        page = MagicMock()
        page.url = PROJECT_URL
        page.title.return_value = "Flow"
        page.evaluate.return_value = {
            "readyState": "complete",
            "bodyTextLength": 120,
            "controls": [
                {
                    "tag": "button",
                    "role": "button",
                    "label": "Sign in",
                    "selected": False,
                }
            ],
            "editables": [
                {
                    "tag": "input",
                    "role": "textbox",
                    "label": "Email",
                    "placeholder": "Email or phone",
                    "name": "identifier",
                    "contenteditable": None,
                }
            ],
            "composerModelControls": [],
            "media": [],
            "iframes": [],
            "diagnosticText": [],
            "terminalStates": ["access_denied", "private_raw_state"],
        }
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        inspection = session.inspect_environment(stabilize=False)

        self.assertEqual(inspection.authentication_state, "sign_in_required")
        self.assertEqual(inspection.loading_or_error_states, ("access_denied",))
        self.assertNotIn("private_raw_state", json.dumps(inspection.to_dict()))

    def test_inspection_is_read_only_and_captures_diagnostic_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            screenshot = Path(tmp) / "diagnostics" / "flow_inspect.png"
            page = MagicMock()
            page.url = PROJECT_URL
            page.title.return_value = "Flow project"
            page.evaluate.return_value = {
                "readyState": "complete",
                "bodyTextLength": 420,
                "controls": [
                    {"tag": "button", "role": "button", "label": "Generate", "selected": False},
                    {"tag": "button", "role": "combobox", "label": "16:9", "selected": True},
                    {"tag": "button", "role": "tab", "label": "Image", "selected": True},
                    {
                        "tag": "button",
                        "role": "button",
                        "label": "Historical card: Nano Banana 2",
                        "selected": False,
                    },
                ],
                "composerModelControls": [
                    composer_model_control(innerText="Nano Banana Pro")
                ],
                "editables": [
                    {
                        "tag": "textarea",
                        "role": "textbox",
                        "label": "Prompt",
                        "placeholder": "Describe an image",
                        "name": "prompt",
                        "contenteditable": None,
                    }
                ],
                "media": [
                    {"kind": "img", "label": "Preview", "src": "https://asset.test/a.png?secret=x", "width": 512, "height": 512}
                ],
                "iframes": [{"title": "tool", "name": "", "src": "https://frame.test/app?token=x"}],
                "diagnosticText": ["Historical card: Nano Banana 2", "Aspect ratio 16:9"],
            }
            page.click = MagicMock()
            page.fill = MagicMock()
            page.type = MagicMock()
            page.press = MagicMock()
            session = FlowSession(PROJECT_URL, Path("profile"), diagnostics_path=screenshot)
            session.page = page
            with patch.object(session, "_stabilize_page"):
                inspection = session.inspect_environment(capture_screenshot=True)
            self.assertEqual(inspection.authentication_state, "editor_visible")
            self.assertEqual(inspection.document_ready_state, "complete")
            self.assertEqual(inspection.visible_body_text_length, 420)
            self.assertEqual(inspection.generation_action_controls, ("Generate",))
            self.assertEqual(inspection.model_labels, ("Nano Banana Pro",))
            self.assertIn("16:9", inspection.aspect_ratio_controls)
            self.assertEqual(inspection.screenshot_path, str(screenshot.resolve()))
            self.assertEqual(inspection.screenshot_method, "playwright")
            self.assertEqual(
                inspection.prompt_control_candidates,
                (
                    {
                        "tag": "textarea",
                        "role": "textbox",
                        "label": "Prompt",
                        "placeholder": "Describe an image",
                        "name": "prompt",
                        "contenteditable": None,
                    },
                ),
            )
            page.screenshot.assert_called_once_with(
                path=str(screenshot.resolve()),
                full_page=False,
                timeout=5_000,
            )
            page.click.assert_not_called()
            page.fill.assert_not_called()
            page.type.assert_not_called()
            page.press.assert_not_called()
            page.get_by_role.assert_not_called()
            page.mouse.move.assert_not_called()
            page.mouse.down.assert_not_called()
            page.mouse.up.assert_not_called()

    def test_diagnostic_screenshot_timeout_retains_existing_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            screenshot = Path(tmp) / "diagnostics" / "flow_inspect.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"prior PNG")
            page = MagicMock()
            page.screenshot.side_effect = RuntimeError("timed out")
            session = FlowSession(PROJECT_URL, Path("profile"), diagnostics_path=screenshot)
            session.page = page
            path, method, error = session._capture_diagnostic_screenshot()
            self.assertEqual(path, str(screenshot.resolve()))
            self.assertEqual(method, "existing")
            self.assertIn("prior diagnostic was retained", error)
            self.assertEqual(screenshot.read_bytes(), b"prior PNG")
            page.context.new_cdp_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
