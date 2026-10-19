import signal
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from nano_batch.adapters.flow import FlowSession
from nano_batch.batch import load_prompts_file
from nano_batch.cancellation import authoritative_cancellation_scope
from nano_batch.launcher import _run_batch_interactively
from nano_batch.models import BrowserDownloadResult, CandidateRecord, GenerationResult, GenerationSettings
from nano_batch.runner import _download_staging_directory, execute_sequential_batch, initialize_run_state
from nano_batch.state import RunStateStore


class PostGenerationCancellationTests(unittest.TestCase):
    def signal_once(self, mode="translated"):
        try:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        except KeyboardInterrupt:
            if mode == "direct":
                raise
            if mode == "translated":
                raise RuntimeError("synthetic transport translation")
        return None

    def test_one_signal_in_each_download_phase_reaches_resume_menu(self):
        targets = (
            "_exact_candidate_tile",
            "_open_candidate_menu",
            "_expose_download_submenu",
            "_wait_for_current_1k_original_size_option",
            "_download_with_cdp",
            "pillow",
            "commit",
            "cleanup_escape",
            "cleanup_history",
        )
        for target in targets:
            for mode in ("direct", "translated"):
                with self.subTest(target=target, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    prompts = root / "prompts.txt"
                    prompts.write_text("one\ntwo\n", encoding="utf-8")
                    batch = load_prompts_file(prompts)
                    run_dir = root / "run"
                    state = initialize_run_state(batch, run_dir)
                    item = state.items[0]
                    item.status = "completed"
                    item.attempt_count = 1
                    item.completed_at = "2026-01-01T00:00:00+00:00"
                    item.candidates = [CandidateRecord(
                        variant=1,
                        source_kind="rendered-image",
                        candidate_id="candidate-safe-id",
                        suggested_filename="0001__scene_0001_visual_001__v01.png",
                    )]
                    # Use the fixture's actual visual identity.
                    item.candidates[0].suggested_filename = f"0001__{item.visual_id}__v01.png"
                    RunStateStore(run_dir / "run_state.json").save(state)
                    session = FlowSession("https://flow.google.com/project/test", root / "profile")
                    page, browser, driver = MagicMock(), MagicMock(), MagicMock()
                    session.page, session._browser, session._playwright = page, browser, driver
                    generated = MagicMock(side_effect=AssertionError("unexpected Start"))
                    session.generate_one = generated

                    def download(**kwargs):
                        Image.new("RGB", (8, 8)).save(kwargs["destination"], format="PNG")
                        return BrowserDownloadResult(
                            candidate_id="candidate-safe-id",
                            staged_path=str(kwargs["destination"]),
                            mechanism="cdp_download_events",
                        )

                    captured = {}
                    def execute(*_args):
                        with authoritative_cancellation_scope() as cancellation:
                            captured["state"] = cancellation
                            execute_sequential_batch(batch, run_dir, session, GenerationSettings(variants=1))
                        return 0

                    with ExitStack() as stack:
                        replacements = {
                            "open": session,
                            "ensure_production_window_usable": None,
                            "_assert_download_context": None,
                            "_exact_candidate_tile": ("candidate-safe-id", MagicMock(), MagicMock()),
                            "_open_candidate_menu": (MagicMock(), {}),
                            "_expose_download_submenu": (MagicMock(), {}),
                            "_wait_for_current_1k_original_size_option": (MagicMock(), {}),
                            "_restore_candidate_history_view": True,
                        }
                        for name, value in replacements.items():
                            mocked = stack.enter_context(patch.object(session, name, return_value=value))
                            if name == target or (target == "cleanup_history" and name == "_restore_candidate_history_view"):
                                mocked.side_effect = lambda *a, **k: self.signal_once(mode)
                        mocked_download = stack.enter_context(patch.object(session, "_download_with_cdp", side_effect=download))
                        if target == "_download_with_cdp":
                            mocked_download.side_effect = lambda **k: self.signal_once(mode)
                        if target == "pillow":
                            stack.enter_context(patch("nano_batch.runner.validate_image_file", side_effect=lambda *a, **k: self.signal_once(mode)))
                        if target == "commit":
                            stack.enter_context(patch("nano_batch.runner.commit_staged_file", side_effect=lambda *a, **k: self.signal_once(mode)))
                        if target == "cleanup_escape":
                            page.keyboard.press.side_effect = lambda *a, **k: self.signal_once(mode)
                        output = []
                        result = _run_batch_interactively(
                            prompts, run_dir, execute,
                            input_fn=lambda _: "2", output=output.append,
                        )

                    self.assertIsNone(result)
                    cancellation = captured["state"]
                    self.assertEqual(cancellation.interrupt_signal_count, 1)
                    self.assertTrue(cancellation.requested)
                    self.assertTrue(cancellation.recovery_menu_reached)
                    self.assertTrue(cancellation.blocking_operation_exited)
                    generated.assert_not_called()
                    if target in {"pillow", "commit"}:
                        # The successful attached session was already disconnected
                        # before local validation/commit began.
                        browser.close.assert_called_once()
                    else:
                        browser.close.assert_not_called()
                    driver.stop.assert_called_once()
                    persisted = RunStateStore(run_dir / "run_state.json").load()
                    self.assertEqual(persisted.items[0].status, "completed")
                    self.assertEqual(persisted.items[0].attempt_count, 1)
                    self.assertEqual(persisted.items[0].candidates[0].candidate_id, "candidate-safe-id")
                    self.assertEqual(persisted.items[0].candidates[0].download_status, "running")
                    self.assertEqual(persisted.items[1].status, "pending")
                    self.assertIn("1. Resume current run", output)

    def test_signal_during_result_discovery_skips_progress_and_cleanup_rpc(self):
        session = FlowSession("https://flow.google.com/project/test", Path("profile"))
        session.page = MagicMock()
        with authoritative_cancellation_scope() as state, patch.object(
            session, "_raise_for_current_generation_attempt_failure"
        ), patch.object(session, "discover_candidates", side_effect=lambda *a, **k: self.signal_once()), patch.object(
            session, "_progress_signals"
        ) as progress, self.assertRaises(KeyboardInterrupt):
            session.wait_for_result(set(), 1, expected_count=1)
        self.assertEqual(state.interrupt_signal_count, 1)
        progress.assert_not_called()

    def test_cancelled_observer_cleanup_performs_no_browser_read(self):
        session = FlowSession("https://flow.google.com/project/test", Path("profile"))
        session.page = MagicMock()
        with authoritative_cancellation_scope() as state:
            state.request()
            session._stop_current_generation_attempt_observation()
        session.page.evaluate.assert_not_called()

    def test_translated_signal_inside_candidate_and_submenu_reads_unwinds_before_poll_wait(self):
        for target in ("candidate", "tile", "submenu", "1k", "history"):
            with self.subTest(target=target), authoritative_cancellation_scope() as state:
                session = FlowSession("https://flow.google.com/project/test", Path("profile"))
                session.page = MagicMock()
                interrupted = lambda *a, **k: self.signal_once()
                ready = dict(image_match_count=1, image_connected=True,
                             image_visible=True, tile_found=True, tile_connected=True,
                             tile_visible=True, tile_owns_exact_image=True)
                with ExitStack() as stack, self.assertRaises(KeyboardInterrupt):
                    if target in {"candidate", "tile"}:
                        probe = stack.enter_context(patch.object(session, "_candidate_image_tile_probe", return_value=ready))
                        if target == "candidate":
                            probe.side_effect = interrupted
                        else:
                            session.page.locator.return_value.all.side_effect = interrupted
                        session._exact_candidate_image_tile("candidate-safe-id")
                    elif target in {"submenu", "1k"}:
                        stack.enter_context(patch.object(session, "_current_download_submenu_parents", side_effect=interrupted))
                        if target == "submenu":
                            session._wait_for_associated_download_submenu(MagicMock(), {}, timeout_ms=1500)
                        else:
                            session._wait_for_current_1k_original_size_option({})
                    else:
                        stack.enter_context(patch.object(session, "ensure_production_window_usable"))
                        snapshot = {"surface_status": "ready", "surface": {
                            "scroll_top": 0, "max_scroll_top": 1000, "client_height": 500,
                        }, "exact_match_count": 0}
                        stack.enter_context(patch.object(session, "_candidate_history_search_positions", return_value=[500]))
                        reads = 0
                        def snapshot_read(*a, **k):
                            nonlocal reads
                            reads += 1
                            if reads == 2:
                                return interrupted()
                            return snapshot
                        stack.enter_context(patch.object(session, "_candidate_history_scroll_snapshot", side_effect=snapshot_read))
                        session._reveal_virtualized_candidate_history("candidate-safe-id")
                self.assertEqual(state.interrupt_signal_count, 1)
                session.page.wait_for_timeout.assert_not_called()

    def test_one_signal_inside_candidate_correlation_stops_before_download(self):
        class Candidate:
            @property
            def candidate_id(inner):
                return self.signal_once()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts.txt"
            prompts.write_text("one\ntwo\n", encoding="utf-8")
            batch = load_prompts_file(prompts)
            adapter = MagicMock(spec=["generate_one", "download_candidate"])
            adapter.generate_one.return_value = GenerationResult(candidates=(Candidate(),))
            with authoritative_cancellation_scope() as state, self.assertRaises(KeyboardInterrupt):
                execute_sequential_batch(batch, root / "run", adapter, GenerationSettings(variants=1))
            persisted = RunStateStore(root / "run" / "run_state.json").load()
            self.assertEqual(state.interrupt_signal_count, 1)
            adapter.generate_one.assert_called_once()
            adapter.download_candidate.assert_not_called()
            self.assertEqual(persisted.items[0].attempt_count, 1)
            self.assertEqual(persisted.items[1].status, "pending")

    def test_one_signal_inside_cdp_event_and_file_poll_skips_cleanup_rpc(self):
        for target in ("configuration", "event", "file", "cleanup"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                session = FlowSession("https://flow.google.com/project/test", Path("profile"))
                session.page = MagicMock()
                session._browser = MagicMock()
                cdp = session._browser.new_browser_cdp_session.return_value
                callbacks = {}
                cdp.on.side_effect = lambda name, callback: callbacks.update({name: callback})
                destination = Path(tmp) / "candidate.download"
                source = Path(tmp) / "browser-guid"
                source.write_bytes(b"test")
                def wait(*_):
                    if target == "event":
                        return self.signal_once()
                    callbacks["Browser.downloadWillBegin"]({"guid": "browser-guid"})
                    callbacks["Browser.downloadProgress"]({"guid": "browser-guid", "state": "completed", "filePath": str(source)})
                session.page.wait_for_timeout.side_effect = wait
                if target == "configuration":
                    cdp.send.side_effect = lambda *a, **k: self.signal_once()
                elif target == "cleanup":
                    def send(_method, payload):
                        if payload["behavior"] == "default":
                            return self.signal_once()
                    cdp.send.side_effect = send
                with authoritative_cancellation_scope() as state, ExitStack() as stack, self.assertRaises(KeyboardInterrupt):
                    stack.enter_context(patch.object(session, "_exact_window_state", return_value="maximized"))
                    stack.enter_context(patch.object(session, "_activate_download_control"))
                    if target == "file":
                        stack.enter_context(patch.object(Path, "is_file", side_effect=lambda *a, **k: self.signal_once()))
                    session._download_with_cdp(candidate_id="candidate-safe-id", download_action=MagicMock(), destination=destination, timeout_seconds=1)
                self.assertEqual(state.interrupt_signal_count, 1)
                self.assertTrue(state.blocking_operation_exited)
                cdp.detach.assert_not_called()
                self.assertEqual(cdp.send.call_count, 2 if target == "cleanup" else 1)

    def test_interrupted_staging_is_preserved_without_a_second_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp)
            with authoritative_cancellation_scope() as state, self.assertRaises(KeyboardInterrupt):
                with _download_staging_directory(asset_dir) as directory:
                    staging = Path(directory)
                    partial = staging / "partial.download"
                    partial.write_bytes(b"partial browser download")
                    self.signal_once("direct")
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(partial.read_bytes(), b"partial browser download")

    def test_normal_staging_cleanup_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with authoritative_cancellation_scope():
                with _download_staging_directory(Path(tmp)) as directory:
                    staging = Path(directory)
                    (staging / "file").write_bytes(b"test")
            self.assertFalse(staging.exists())

    def test_signal_received_during_normal_staging_cleanup_propagates_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tempfile.TemporaryDirectory, "cleanup", side_effect=lambda *a, **k: self.signal_once()):
                with authoritative_cancellation_scope() as state, self.assertRaises(KeyboardInterrupt):
                    with _download_staging_directory(Path(tmp)):
                        pass
            self.assertEqual(state.interrupt_signal_count, 1)
            self.assertEqual(state.received_phase, "download_cleanup")


if __name__ == "__main__":
    unittest.main()
