import inspect
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowSession,
    _normalize_download_layout_snapshot,
)


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000007"


def candidate_snapshot(*, visible=True, with_more_options=True, candidate_id=CANDIDATE_ID):
    controls = (
        [
            {
                "connected": True,
                "visible": True,
                "enabled": True,
                "bounding_box": {"x": 901, "y": 132, "width": 40, "height": 40},
            }
        ]
        if with_more_options
        else []
    )
    return {
        "candidate_id": candidate_id,
        "data_media_id_count": 1,
        "connected": True,
        "visible": visible,
        "bounding_box": {"x": 100, "y": 120, "width": 842, "height": 470},
        "more_options_count": len(controls),
        "more_options_controls": controls,
    }


def layout_snapshot(*, candidates=None, target_candidate=None, **overrides):
    candidates = list(candidates if candidates is not None else [candidate_snapshot()])
    values = {
        "outer_width": 1440,
        "outer_height": 900,
        "inner_width": 1424,
        "inner_height": 811,
        "viewport_width": 1424,
        "viewport_height": 811,
        "devicePixelRatio": 1.25,
        "visible_candidate_tile_count": sum(bool(item.get("visible")) for item in candidates),
        "candidate_tiles_with_data_media_id_count": len(candidates),
        "candidates": candidates,
        "truncated": False,
        "target_candidate": target_candidate,
    }
    values.update(overrides)
    return values


class FlowDownloadLayoutTests(unittest.TestCase):
    def test_inspection_reports_dimensions_state_route_and_more_options_presence(self):
        page = MagicMock()
        page.url = PROJECT_URL + "?token=private#fragment"
        page.evaluate.return_value = layout_snapshot()
        cdp = MagicMock()
        cdp.send.return_value = {"bounds": {"windowState": "maximized"}}
        page.context.new_cdp_session.return_value = cdp
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        report = session.inspect_flow_download_layout()

        self.assertEqual(
            report["browser_window"],
            {
                "window_state": "maximized",
                "outer_width": 1440,
                "outer_height": 900,
                "inner_width": 1424,
                "inner_height": 811,
                "viewport_width": 1424,
                "viewport_height": 811,
                "devicePixelRatio": 1.25,
            },
        )
        self.assertEqual(report["page"]["current_url"], PROJECT_URL)
        self.assertTrue(report["page"]["is_project_url"])
        self.assertTrue(report["page"]["is_project_uuid_url"])
        self.assertEqual(
            report["page"]["project_id"],
            "00000000-0000-4000-8000-000000000001",
        )
        candidate = report["candidate_summary"]["candidates"][0]
        self.assertEqual(candidate["candidate_id"], CANDIDATE_ID)
        self.assertEqual(candidate["more_options_count"], 1)
        self.assertTrue(candidate["more_options_connected"])
        self.assertTrue(candidate["more_options_visible"])
        self.assertTrue(candidate["more_options_enabled"])
        self.assertEqual(candidate["more_options_bounding_box"]["width"], 40)
        self.assertTrue(
            report["layout_evidence"]["more_options_present_on_visible_candidates"]
        )
        self.assertIsNone(report["responsive_layout_signal"])
        cdp.send.assert_called_once_with("Browser.getWindowForTarget")
        cdp.detach.assert_called_once_with()

    def test_visible_candidate_without_more_options_reports_exact_zero(self):
        normalized = _normalize_download_layout_snapshot(
            layout_snapshot(candidates=[candidate_snapshot(with_more_options=False)]),
            10,
        )
        candidate = normalized["candidate_summary"]["candidates"][0]
        self.assertEqual(candidate["more_options_count"], 0)
        self.assertFalse(candidate["more_options_connected"])
        self.assertFalse(candidate["more_options_visible"])
        self.assertFalse(candidate["more_options_enabled"])
        self.assertIsNone(candidate["more_options_bounding_box"])

    def test_hidden_candidate_does_not_create_false_visible_presence(self):
        hidden = candidate_snapshot(visible=False, with_more_options=True)
        normalized = _normalize_download_layout_snapshot(
            layout_snapshot(
                candidates=[hidden],
                visible_candidate_tile_count=0,
                candidate_tiles_with_data_media_id_count=1,
            ),
            10,
        )
        self.assertEqual(normalized["candidate_summary"]["candidate_count"], 0)
        self.assertEqual(
            normalized["candidate_summary"]["visible_candidate_tile_count"], 0
        )
        self.assertEqual(
            normalized["candidate_summary"]["candidate_tiles_with_data_media_id_count"],
            1,
        )

    def test_candidate_output_is_strictly_bounded(self):
        candidates = [
            candidate_snapshot(candidate_id=f"candidate-{index:02d}")
            for index in range(15)
        ]
        normalized = _normalize_download_layout_snapshot(
            layout_snapshot(candidates=candidates, truncated=True),
            10,
        )
        self.assertEqual(normalized["candidate_summary"]["candidate_count"], 10)
        self.assertEqual(len(normalized["candidate_summary"]["candidates"]), 10)
        self.assertTrue(normalized["candidate_summary"]["truncated"])
        with self.assertRaisesRegex(ValueError, "1 to 10"):
            _normalize_download_layout_snapshot({}, 11)

    def test_snapshot_whitelist_omits_prompts_urls_accounts_and_unknown_dom(self):
        raw_candidate = candidate_snapshot()
        raw_candidate.update(
            {
                "prompt_text": "PRIVATE PROMPT",
                "image_url": "https://private.invalid/signed-image?token=secret",
                "account_email": "private@example.com",
                "raw_dom": "<body>PRIVATE DOM</body>",
            }
        )
        raw = layout_snapshot(candidates=[raw_candidate])
        raw.update({"cookies": "SECRET COOKIE", "profile": "PRIVATE PROFILE"})
        encoded = json.dumps(_normalize_download_layout_snapshot(raw, 10))
        for secret in (
            "PRIVATE PROMPT",
            "signed-image",
            "private@example.com",
            "PRIVATE DOM",
            "SECRET COOKIE",
            "PRIVATE PROFILE",
        ):
            self.assertNotIn(secret, encoded)

    def test_inspection_uses_only_dom_read_and_exact_cdp_window_query(self):
        page = MagicMock()
        page.url = PROJECT_URL
        page.evaluate.return_value = layout_snapshot(candidates=[])
        cdp = MagicMock()
        cdp.send.return_value = {"bounds": {"windowState": "normal"}}
        page.context.new_cdp_session.return_value = cdp
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = page

        session.inspect_flow_download_layout()

        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        page.keyboard.press.assert_not_called()
        page.goto.assert_not_called()
        page.reload.assert_not_called()
        page.bring_to_front.assert_not_called()
        cdp.send.assert_called_once_with("Browser.getWindowForTarget")
        source = "\n".join(
            (
                inspect.getsource(FlowSession.inspect_flow_download_layout),
                inspect.getsource(FlowSession._download_layout_dom_snapshot),
                inspect.getsource(FlowSession._read_only_exact_window_state),
            )
        )
        for forbidden in (
            ".click(",
            ".hover(",
            ".press(",
            ".goto(",
            "bring_to_front",
            "Browser.setWindowBounds",
            "Emulation.setFocusEmulationEnabled",
        ):
            self.assertNotIn(forbidden, source)

    def test_existing_production_error_keeps_code_and_adds_layout_evidence(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        tile = MagicMock()
        tile.get_by_role.return_value.all.return_value = []
        page.evaluate.return_value = layout_snapshot(
            candidates=[candidate_snapshot(with_more_options=False)],
            target_candidate=candidate_snapshot(with_more_options=False),
        )
        session.page = page
        cdp = MagicMock()
        cdp.send.return_value = {"bounds": {"windowState": "normal"}}
        session._page_cdp_session = cdp

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
        ), self.assertRaises(FlowAutomationError) as raised:
            session._reveal_candidate_options(CANDIDATE_ID, image, tile)

        message = str(raised.exception)
        self.assertTrue(message.startswith("CANDIDATE_MORE_OPTIONS_NOT_PRESENT:"))
        self.assertIn("bounded reveal/reacquisition attempts=3", message)
        self.assertIn("window_state=normal", message)
        self.assertIn("outer=1440x900", message)
        self.assertIn("inner=1424x811", message)
        self.assertIn("viewport=1424x811", message)
        self.assertIn("candidate_bbox=100,120,842,470", message)
        self.assertIn("more_options_count=0", message)
        self.assertEqual(page.mouse.move.call_count, 3)
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()

    def test_non_missing_more_options_failure_does_not_collect_layout_evidence(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        image = MagicMock()
        control = MagicMock()
        tile = MagicMock()
        tile.get_by_role.return_value.all.return_value = [control]
        session.page = page
        hidden = {
            "connected": True,
            "visible": False,
            "opacity": "0",
            "visibility": "visible",
            "pointer_events": "auto",
        }
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
            return_value=hidden,
        ), patch.object(
            session,
            "_candidate_more_options_failure_layout_detail",
            side_effect=AssertionError("wrong failure branch"),
        ), self.assertRaisesRegex(
            FlowAutomationError, "^CANDIDATE_MORE_OPTIONS_HIDDEN:"
        ):
            session._reveal_candidate_options(CANDIDATE_ID, image, tile)

    def test_cli_delegates_to_read_only_inspection_and_closes_session(self):
        report = {
            "schema": "nano-banana-flow-download-layout-inspection/v1",
            "responsive_layout_signal": None,
        }
        fake_session = MagicMock()
        fake_session.inspect_flow_download_layout.return_value = report
        output = StringIO()
        argv = ["app.py", "inspect-flow-download-layout", "--limit", "7"]
        with patch("sys.argv", argv), patch.object(
            app, "_flow_session_from_args", return_value=fake_session
        ), redirect_stdout(output):
            self.assertEqual(app.main(), 0)

        fake_session.open_read_only_project_reconnaissance.assert_called_once_with()
        fake_session.inspect_flow_download_layout.assert_called_once_with(candidate_limit=7)
        fake_session.close.assert_called_once_with()
        self.assertIn('"responsive_layout_signal": null', output.getvalue())
        self.assertIn("no click, hover, keyboard input", output.getvalue())
        self.assertIn("window manipulation", output.getvalue())


if __name__ == "__main__":
    unittest.main()
