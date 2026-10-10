import inspect
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import FlowSession


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"


def trigger(**overrides):
    values = {
        "tag": "button",
        "role": "button",
        "visible_text": "Nano Banana Lite",
        "accessible_name": "Current model: Nano Banana Lite",
        "aria_label": "Current model: Nano Banana Lite",
        "connected": True,
        "visible": True,
        "enabled": True,
        "bounding_box": {"x": 900, "y": 700, "width": 180, "height": 40},
        "aria_haspopup": "listbox",
        "aria_expanded": "true",
        "aria_controls": "model-menu-panel",
        "aria_owns": None,
        "selected": False,
        "aria_selected": None,
        "aria_checked": None,
        "checked": None,
        "stable_attributes": {
            "data-testid": "model-selector",
            "data-session": "private-session-value",
        },
        "semantic_ancestor": {
            "tag": "div",
            "role": "group",
            "accessible_name": None,
            "is_composer_root": True,
        },
    }
    values.update(overrides)
    return values


def popup(**overrides):
    values = {
        "tag": "div",
        "role": "listbox",
        "id": "model-menu-panel",
        "connected": True,
        "visible": True,
        "bounding_box": {"x": 880, "y": 460, "width": 220, "height": 220},
        "ownership_relationship": "aria-controls",
        "stable_attributes": {"data-testid": "model-menu"},
    }
    values.update(overrides)
    return values


def option(name, **overrides):
    values = {
        "tag": "div",
        "role": "option",
        "visible_text": name,
        "accessible_name": name,
        "aria_label": name,
        "connected": True,
        "visible": True,
        "enabled": True,
        "bounding_box": {"x": 890, "y": 480, "width": 200, "height": 40},
        "aria_haspopup": None,
        "aria_expanded": None,
        "aria_controls": None,
        "aria_owns": None,
        "selected": name == "Nano Banana Lite",
        "aria_selected": "true" if name == "Nano Banana Lite" else "false",
        "aria_checked": None,
        "checked": None,
        "stable_attributes": {"data-value": name.lower().replace(" ", "-")},
        "semantic_ancestor": None,
        "popup_id": "model-menu-panel",
        "ownership_relationship": "aria-controls",
    }
    values.update(overrides)
    return values


def snapshot(**overrides):
    values = {
        "trigger_candidates": [trigger()],
        "popups": [popup()],
        "model_options": [
            option("Nano Banana Lite"),
            option("Nano Banana 2"),
        ],
        "truncated": False,
    }
    values.update(overrides)
    return values


class FlowModelMenuReconTests(unittest.TestCase):
    def inspect(self, raw=None, *, limit=30):
        page = MagicMock()
        page.evaluate.return_value = snapshot() if raw is None else raw
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page
        report = session.inspect_flow_model_menu(control_limit=limit)
        return report, page

    def test_trigger_metadata_and_relationship_attributes_are_preserved(self):
        report, _ = self.inspect(
            snapshot(trigger_candidates=[trigger(aria_owns="owned-model-popup")])
        )

        current = report["current_model_trigger"]
        self.assertEqual(report["current_model_trigger_count"], 1)
        self.assertEqual(current["tag"], "button")
        self.assertEqual(current["role"], "button")
        self.assertEqual(current["visible_text"], "Nano Banana Lite")
        self.assertEqual(
            current["accessible_name"], "Current model: Nano Banana Lite"
        )
        self.assertEqual(current["aria_haspopup"], "listbox")
        self.assertEqual(current["aria_expanded"], "true")
        self.assertEqual(current["aria_controls"], "model-menu-panel")
        self.assertEqual(current["aria_owns"], "owned-model-popup")
        self.assertTrue(current["connected"])
        self.assertTrue(current["visible"])
        self.assertTrue(current["enabled"])
        self.assertEqual(current["bounding_box"]["width"], 180)
        self.assertTrue(current["semantic_ancestor"]["is_composer_root"])
        self.assertEqual(
            current["stable_attributes"], {"data-testid": "model-selector"}
        )

    def test_owned_popup_and_exact_lite_and_target_options_are_reported(self):
        report, _ = self.inspect()

        self.assertEqual(report["status"], "owned_popup_found")
        self.assertEqual(report["open_popup_count"], 1)
        self.assertEqual(report["open_popups"][0]["id"], "model-menu-panel")
        self.assertEqual(
            report["open_popups"][0]["ownership_relationship"], "aria-controls"
        )
        self.assertEqual(
            [item["visible_text"] for item in report["model_options"]],
            ["Nano Banana Lite", "Nano Banana 2"],
        )
        lite, target = report["model_options"]
        self.assertTrue(lite["selected"])
        self.assertEqual(lite["aria_selected"], "true")
        self.assertFalse(target["selected"])
        self.assertTrue(target["enabled"])
        self.assertEqual(
            target["popup_ownership"],
            {"popup_id": "model-menu-panel", "relationship": "aria-controls"},
        )
        self.assertEqual(
            report["target_evidence"]["exact_visible_enabled_target_count"], 1
        )
        self.assertEqual(
            report["target_evidence"]["exact_visible_enabled_lite_count"], 1
        )
        self.assertTrue(report["target_evidence"]["lite_and_target_distinguishable"])
        self.assertTrue(report["target_evidence"]["exact_target_proven"])

    def test_unrelated_popup_and_option_are_ignored(self):
        raw = snapshot(
            popups=[
                popup(),
                popup(
                    id="account-menu",
                    ownership_relationship="unrelated-visible-menu",
                ),
            ],
            model_options=[
                option("Nano Banana Lite"),
                option("Nano Banana 2"),
                option(
                    "Nano Banana 2",
                    popup_id="account-menu",
                    ownership_relationship="unrelated-visible-menu",
                ),
            ],
        )

        report, _ = self.inspect(raw)

        self.assertEqual(report["open_popup_count"], 1)
        self.assertEqual(report["model_option_count"], 2)
        self.assertNotIn("account-menu", json.dumps(report))

    def test_output_is_bounded_and_private_fields_are_not_emitted(self):
        many_options = [
            option(f"Nano Banana {number}") for number in range(1, 36)
        ]
        many_options.append(
            option(
                "Nano Banana 2 user@example.com",
                accessible_name="private prompt Nano Banana 2 token=secret",
                aria_label="Google Account user@example.com",
            )
        )
        raw = snapshot(model_options=many_options)
        raw["prompt"] = "private prompt contents"
        raw["cookies"] = "private-cookie"
        raw["candidate_uuid"] = "00000000-0000-4000-8000-000000000009"

        report, _ = self.inspect(raw)
        rendered = json.dumps(report)

        self.assertEqual(report["model_option_count"], 30)
        self.assertTrue(report["truncated"])
        self.assertNotIn("private prompt", rendered)
        self.assertNotIn("user@example.com", rendered)
        self.assertNotIn("private-cookie", rendered)
        self.assertNotIn("00000000-0000-4000-8000-000000000009", rendered)
        self.assertNotIn("data-session", rendered)

    def test_menu_not_open_is_reported_without_opening_it(self):
        raw = snapshot(
            trigger_candidates=[trigger(aria_expanded="false")],
            popups=[],
            model_options=[],
        )

        report, page = self.inspect(raw)

        self.assertEqual(report["status"], "model_menu_not_open")
        self.assertEqual(report["open_popup_count"], 0)
        self.assertFalse(report["target_evidence"]["exact_target_proven"])
        page.click.assert_not_called()

    def test_inspection_dispatches_only_one_read_only_dom_evaluation(self):
        report, page = self.inspect()

        page.evaluate.assert_called_once()
        script = page.evaluate.call_args.args[0].casefold()
        for forbidden in (
            ".click(",
            ".hover(",
            "dispatchEvent",
            "keyboard",
            ".goto(",
            "screenshot",
            "bringtofront",
            ".focus(",
        ):
            self.assertNotIn(forbidden.casefold(), script)
        page.click.assert_not_called()
        page.hover.assert_not_called()
        page.goto.assert_not_called()
        page.keyboard.press.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.screenshot.assert_not_called()
        self.assertEqual(
            report["safety"],
            {
                "clicks": 0,
                "hovers": 0,
                "keyboard_inputs": 0,
                "navigations": 0,
                "screenshots": 0,
                "focus_or_window_actions": 0,
                "flow_mutations": 0,
            },
        )

    def test_cli_uses_read_only_attachment_and_no_production_path(self):
        fake_session = MagicMock()
        fake_session.inspect_flow_model_menu.return_value = {
            "schema": "nano-banana-flow-model-menu-inspection/v1",
            "inspection_mode": "read_only_dom",
            "status": "model_menu_not_open",
        }
        argv = ["app.py", "inspect-flow-model-menu", "--limit", "12"]

        with patch("sys.argv", argv), patch.object(
            app,
            "_flow_session_from_args",
            return_value=fake_session,
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(app.main(), 0)

        self.assertIn("READ-ONLY MODEL MENU INSPECTION", output.getvalue())
        fake_session.open_read_only_project_reconnaissance.assert_called_once_with()
        fake_session.inspect_flow_model_menu.assert_called_once_with(control_limit=12)
        fake_session.close.assert_called_once_with()
        fake_session.open.assert_not_called()
        fake_session.ensure_production_window_usable.assert_not_called()
        fake_session.configure_generation.assert_not_called()
        fake_session.generate_one.assert_not_called()
        fake_session.submit_prompt.assert_not_called()
        fake_session.download_candidate.assert_not_called()

    def test_production_configuration_is_not_coupled_to_reconnaissance(self):
        source = inspect.getsource(FlowSession.configure_generation)
        self.assertNotIn("inspect_flow_model_menu", source)
        parsed = app.build_parser().parse_args(
            ["inspect-flow-model-menu", "--limit", "30"]
        )
        self.assertIs(parsed.func, app.cmd_inspect_flow_model_menu)
        self.assertEqual(parsed.limit, 30)

    def test_invalid_limit_fails_before_dom_read(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page

        for limit in (0, 31):
            with self.subTest(limit=limit), self.assertRaisesRegex(
                ValueError, "1 to 30"
            ):
                session.inspect_flow_model_menu(control_limit=limit)
        page.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
