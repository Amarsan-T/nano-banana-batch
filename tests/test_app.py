import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image

import app
from nano_batch.errors import (
    FLOW_USAGE_LIMIT_REACHED,
    WAITING_FOR_FLOW_LIMIT_RESET,
    TemporaryGenerationBlock,
)
from nano_batch.models import (
    BrowserDownloadResult,
    CandidateRecord,
    DiscoveredCandidate,
    GenerationResult,
)
from nano_batch.state import RunStateStore


class OfflinePromptAdapter:
    def __init__(self):
        self.events = []

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        self.events.append(("generate", item.order, item.prompt, settings.variants))
        return GenerationResult(
            candidates=tuple(
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id=str(UUID(int=item.order * 100 + variant)),
                )
                for variant in range(1, settings.variants + 1)
            )
        )

    def download_candidate(self, *, candidate, destination):
        order = int(candidate.suggested_filename[:4])
        self.events.append(("download", order, candidate.variant))
        Image.new("RGB", (8, 6), (20 * candidate.variant, 30, 40)).save(
            destination,
            format="JPEG",
        )
        return BrowserDownloadResult(
            candidate_id=str(candidate.candidate_id),
            staged_path=str(destination),
            browser_suggested_filename=f"candidate-v{candidate.variant:02d}.jpeg",
            browser_download_guid=f"offline-guid-{order}-{candidate.variant}",
            mechanism="offline-test",
        )


class AppTests(unittest.TestCase):
    def test_default_runtime_settings_do_not_require_a_model_name(self):
        settings = app._generation_settings({})

        self.assertIsNone(settings.preferred_model_hint)
        self.assertEqual(settings.mode, "image")
        self.assertEqual(settings.aspect_ratio, "16:9")

    def test_policy_retry_terminal_messages_are_explicit(self):
        with redirect_stdout(StringIO()) as output:
            app._report_policy_retry_event(94, "automatic_retry_started")
            app._report_policy_retry_event(94, "automatic_retry_succeeded")
            app._report_policy_retry_event(94, "automatic_retry_exhausted")

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Order 94: Flow policy rejection.",
                "Automatic retry 1/1...",
                "POLICY RECOVERY: order=94 event=policy_rejection_detected automatic_policy_retry_count=1",
                "POLICY RECOVERY: order=94 event=automatic_policy_retry_attempted automatic_policy_retry_count=1",
                "Order 94: retry succeeded.",
                "Order 94: Flow policy rejection repeated.",
                "Automatic policy retry exhausted.",
                "Manual retry required.",
                "POLICY RECOVERY: order=94 event=policy_rejection_detected automatic_policy_retry_count=1",
            ],
        )

    def test_run_prompts_file_requires_run_directory(self):
        argv = ["app.py", "run-prompts-file", "prompts.txt", "--preview"]
        with patch("sys.argv", argv), redirect_stderr(StringIO()), self.assertRaises(
            SystemExit
        ) as raised:
            app.main()

        self.assertEqual(raised.exception.code, 2)

    def test_run_prompt_rejects_empty_and_whitespace_only_cli_values(self):
        for prompt in ("", "   \t"):
            with self.subTest(prompt=repr(prompt)):
                argv = ["app.py", "run-prompt", prompt, "--preview"]
                with patch("sys.argv", argv), patch.object(
                    app,
                    "_flow_session_from_args",
                    side_effect=AssertionError("invalid prompt must not create an adapter"),
                ), redirect_stderr(StringIO()) as errors:
                    self.assertEqual(app.main(), 2)
                self.assertIn("Prompt must be a non-empty string", errors.getvalue())

    def test_run_prompt_delegates_to_sequential_path_and_resumes_same_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "direct-prompt-run"
            config = root / "missing-config.json"
            prompt = "東京の夜景  —  café ☕"
            argv = [
                "app.py",
                "run-prompt",
                prompt,
                "--run-dir",
                str(run_dir),
                "--config",
                str(config),
            ]
            adapter = OfflinePromptAdapter()

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=adapter,
            ), redirect_stdout(StringIO()) as first_output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(
                adapter.events,
                [
                    ("generate", 1, prompt, 1),
                    ("download", 1, 1),
                ],
            )
            first_report = first_output.getvalue()
            self.assertIn("Order: 1", first_report)
            self.assertIn(f"Prompt: {prompt}", first_report)
            self.assertIn(f"Run directory: {run_dir.resolve()}", first_report)
            self.assertIn("generation=generated, downloads=downloaded", first_report)
            self.assertIn("Canonical assets:", first_report)
            self.assertIn("v01:", first_report)
            self.assertNotIn("v02:", first_report)

            events_after_first_run = list(adapter.events)
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=adapter,
            ), redirect_stdout(StringIO()) as resumed_output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(adapter.events, events_after_first_run)
            self.assertIn(
                "generation=skipped_completed, downloads=skipped_valid",
                resumed_output.getvalue(),
            )

    def test_run_prompt_preserves_run_directory_batch_mismatch_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "direct-prompt-run"
            original = app.single_prompt_batch("original prompt")
            app.initialize_run_state(original, run_dir)
            state_path = run_dir / "run_state.json"
            before = state_path.read_bytes()
            argv = [
                "app.py",
                "run-prompt",
                "different prompt",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("mismatch must fail before adapter creation"),
            ), redirect_stderr(StringIO()) as errors:
                self.assertEqual(app.main(), 2)

            self.assertEqual(state_path.read_bytes(), before)
            self.assertIn("different semantic batch", errors.getvalue())

    def test_run_prompts_file_delegates_in_order_and_resumes_same_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts_path = root / "prompts.txt"
            prompts_path.write_text(
                "first prompt\n\n# note\n東京の夜景\nthird  prompt\n",
                encoding="utf-8",
            )
            run_dir = root / "prompts-file-run"
            argv = [
                "app.py",
                "run-prompts-file",
                str(prompts_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            adapter = OfflinePromptAdapter()

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=adapter,
            ), redirect_stdout(StringIO()) as first_output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(
                adapter.events,
                [
                    ("generate", 1, "first prompt", 1),
                    ("download", 1, 1),
                    ("generate", 2, "東京の夜景", 1),
                    ("download", 2, 1),
                    ("generate", 3, "third  prompt", 1),
                    ("download", 3, 1),
                ],
            )
            report = first_output.getvalue()
            self.assertIn(f"Prompts file: {prompts_path.resolve()}", report)
            self.assertIn("Accepted prompts/orders: 3", report)
            self.assertIn(f"Run directory: {run_dir.resolve()}", report)
            self.assertIn("Expected Flow variants: x1 (set manually)", report)
            self.assertIn("Sequential batch scope: 3 order(s) (1 -> 2 -> 3)", report)
            self.assertEqual(report.count("generation=generated, downloads=downloaded"), 3)
            self.assertEqual(report.count(" v01:"), 3)
            self.assertIn("Completed: 3", report)
            self.assertIn("Failed: 0", report)

            events_after_first_run = list(adapter.events)
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=adapter,
            ), redirect_stdout(StringIO()) as resumed_output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(adapter.events, events_after_first_run)
            self.assertEqual(
                resumed_output.getvalue().count(
                    "generation=skipped_completed, downloads=skipped_valid"
                ),
                3,
            )

    def test_run_prompts_file_rejects_all_ignored_content_before_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts_path = root / "prompts.txt"
            prompts_path.write_text("\n # note\n\t\n", encoding="utf-8")
            run_dir = root / "run"
            argv = [
                "app.py",
                "run-prompts-file",
                str(prompts_path),
                "--run-dir",
                str(run_dir),
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("invalid file must not create an adapter"),
            ), redirect_stderr(StringIO()) as errors:
                self.assertEqual(app.main(), 2)

            self.assertIn("contains no prompts", errors.getvalue())
            self.assertFalse(run_dir.exists())

    def test_changed_prompts_file_content_preserves_batch_mismatch_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts_path = root / "prompts.txt"
            prompts_path.write_text("one\ntwo\n", encoding="utf-8")
            run_dir = root / "run"
            original = app.load_prompts_file(prompts_path)
            app.initialize_run_state(original, run_dir)
            state_path = run_dir / "run_state.json"
            before = state_path.read_bytes()
            prompts_path.write_text("one\nchanged\n", encoding="utf-8")
            argv = [
                "app.py",
                "run-prompts-file",
                str(prompts_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("mismatch must fail before adapter creation"),
            ), redirect_stderr(StringIO()) as errors:
                self.assertEqual(app.main(), 2)

            self.assertEqual(state_path.read_bytes(), before)
            self.assertIn("different semantic batch", errors.getvalue())

    def test_default_flow_session_uses_dedicated_chrome_profile(self):
        args = type("Args", (), {"url": None, "profile_dir": None})()
        session = app._flow_session_from_args(args, {})
        self.assertEqual(session.flow_url, "https://flow.google.com")
        self.assertEqual(session.profile_dir, Path(".flow_chrome_profile"))
        self.assertEqual(session.cdp_endpoint, "http://127.0.0.1:9222")
        self.assertEqual(session.diagnostics_path, Path("diagnostics/flow_inspect.png"))
        self.assertFalse(session.prestart_timing_debug)

    def test_configured_flow_session_preserves_project_and_loopback_debugging(self):
        args = type("Args", (), {"url": None, "profile_dir": None})()
        project_url = "https://flow.google.com/project/configured-project"
        session = app._flow_session_from_args(
            args,
            {
                "flow_url": project_url,
                "browser_profile_dir": ".flow_chrome_profile",
                "debug_address": "127.0.0.1",
                "debug_port": 9333,
                "prestart_timing_debug": True,
            },
        )
        self.assertEqual(session.flow_url, project_url)
        self.assertEqual(session.cdp_endpoint, "http://127.0.0.1:9333")
        self.assertTrue(session.prestart_timing_debug)

    def test_prestart_timing_debug_config_requires_boolean(self):
        args = type("Args", (), {"url": None, "profile_dir": None})()
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            app._flow_session_from_args(
                args,
                {"prestart_timing_debug": "true"},
            )

    def test_generate_one_preview_has_no_browser_or_state_interaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {"order": 1, "visual_id": "scene_0001_visual_001", "prompt": "safe test"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "app.py",
                "generate-one",
                str(batch_path),
                "--order",
                "1",
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app, "FlowSession", side_effect=AssertionError("browser adapter must not be created")
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)
            self.assertIn("PREVIEW ONLY", output.getvalue())
            self.assertFalse(run_dir.exists())

    def test_repair_order_candidates_preview_is_read_only_and_browser_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": order,
                                "visual_id": f"scene_{order:04d}_visual_001",
                                "prompt": f"safe test {order}",
                            }
                            for order in (1, 2)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].attempt_count = 1
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="conflicted-candidate",
                ),
                CandidateRecord(
                    variant=2,
                    suggested_filename="0001__scene_0001_visual_001__v02.png",
                    source_kind="rendered-image",
                    candidate_id="target-only-candidate",
                ),
            ]
            state.items[1].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0002__scene_0002_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="conflicted-candidate",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            argv = [
                "app.py",
                "repair-order-candidates",
                str(batch_path),
                "--order",
                "1",
                "--run-dir",
                str(run_dir),
                "--preview",
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("repair preview must not create a browser adapter"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            self.assertIn("PREVIEW ONLY", output.getvalue())
            self.assertIn("conflicted-candidate -> cleared", output.getvalue())
            self.assertFalse((run_dir / "assets").exists())

    def test_run_batch_preview_keeps_three_order_invocation_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": order,
                                "visual_id": f"scene_{order:04d}_visual_001",
                                "prompt": f"safe test {order}",
                            }
                            for order in (1, 2, 3)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("browser adapter must not be created"),
            ), patch.object(
                app,
                "execute_sequential_batch",
                side_effect=AssertionError("preview must not execute the batch"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertFalse(run_dir.exists())
            report = output.getvalue()
            self.assertIn("Sequential batch scope: 3 order(s) (1 -> 2 -> 3)", report)
            self.assertIn("Order 1", report)
            self.assertIn("Order 2", report)
            self.assertIn("Order 3", report)
            self.assertEqual(report.count("pending -> generate"), 3)
            self.assertEqual(report.count("download quality: 1K Original size"), 3)
            self.assertIn("no browser interaction, prompt submission, generation", report)

    def test_real_flow_batch_scope_uses_windows_execution_state_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            final_state = app.initialize_run_state(batch, run_dir)
            adapter = app.FlowSession(
                "https://flow.google.com/project/offline-test",
                root / "profile",
            )
            guard = MagicMock()
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=adapter
            ), patch.object(
                app,
                "execute_sequential_batch",
                return_value=(final_state, ()),
            ) as execute_batch, patch.object(
                app,
                "windows_execution_state_guard",
                guard,
            ), redirect_stdout(StringIO()):
                self.assertEqual(app.main(), 0)

            guard.assert_called_once_with(enabled=True)
            guard.return_value.__enter__.assert_called_once_with()
            guard.return_value.__exit__.assert_called_once()
            self.assertTrue(execute_batch.call_args.kwargs["manage_flow_project"])

    def test_direct_cli_keyboard_interrupt_cleans_power_guard_without_launcher_recursion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = app.FlowSession(
                "https://flow.google.com/project/offline-test",
                root / "profile",
            )
            guard = MagicMock()
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=adapter
            ), patch.object(
                app, "execute_sequential_batch", side_effect=KeyboardInterrupt
            ) as execute_batch, patch.object(
                app, "windows_execution_state_guard", guard
            ), patch.object(
                app,
                "run_launcher",
                side_effect=AssertionError("direct CLI must not recurse into launcher"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 130)

            self.assertIn("Interrupted.", output.getvalue())
            guard.assert_called_once_with(enabled=True)
            guard.return_value.__enter__.assert_called_once_with()
            guard.return_value.__exit__.assert_called_once()
            execute_batch.assert_called_once()

    def test_ten_item_preview_is_complete_and_has_no_browser_or_state_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": order,
                                "visual_id": f"scene_{order:04d}_visual_001",
                                "prompt": f"safe test {order}",
                            }
                            for order in range(10, 0, -1)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("browser adapter must not be created"),
            ), patch.object(
                app,
                "execute_sequential_batch",
                side_effect=AssertionError("preview must not execute the batch"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertFalse(run_dir.exists())
            report = output.getvalue()
            self.assertIn(
                "Sequential batch scope: 10 order(s) "
                "(1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10)",
                report,
            )
            for order in range(1, 11):
                self.assertIn(f"Order {order} — scene_{order:04d}_visual_001", report)
            self.assertEqual(report.count("pending -> generate"), 10)
            self.assertEqual(report.count("download quality: 1K Original size"), 10)
            self.assertIn("PREVIEW ONLY", report)

    def test_waiting_flow_limit_preview_is_zero_interaction_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = WAITING_FOR_FLOW_LIMIT_RESET
            state.items[0].error = f"{FLOW_USAGE_LIMIT_REACHED}: visible Flow usage limit"
            state.items[0].generation_error_code = FLOW_USAGE_LIMIT_REACHED
            store = RunStateStore(run_dir / "run_state.json")
            store.save(state)
            before = store.path.read_bytes()
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("preview must not create a browser adapter"),
            ), patch.object(
                app,
                "execute_sequential_batch",
                side_effect=AssertionError("preview must not execute the batch"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(store.path.read_bytes(), before)
            self.assertIn("Flow limit wait -> resume when available", output.getvalue())
            self.assertIn("PREVIEW ONLY", output.getvalue())

    def test_run_batch_prints_clear_usage_limit_pause_summary(self):
        class UsageLimitedAdapter:
            def __init__(self):
                self.generation_calls = 0
                self.download_calls = 0

            def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
                self.generation_calls += 1
                raise TemporaryGenerationBlock(
                    "visible Flow usage limit",
                    code=FLOW_USAGE_LIMIT_REACHED,
                    state_status=WAITING_FOR_FLOW_LIMIT_RESET,
                    attempt_activated=True,
                )

            def download_candidate(self, *, candidate, destination):
                self.download_calls += 1
                raise AssertionError("usage-limited generation must not download")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": order,
                                "visual_id": f"scene_{order:04d}_visual_001",
                                "prompt": f"safe test {order}",
                            }
                            for order in (1, 2, 3)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = UsageLimitedAdapter()
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=adapter
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 3)

            report = output.getvalue()
            self.assertIn(f"Batch paused: {FLOW_USAGE_LIMIT_REACHED}", report)
            self.assertIn("Order: 1", report)
            self.assertIn("Completed: 0/3", report)
            self.assertIn("No later orders were started.", report)
            self.assertIn("No automatic Flow retry was attempted.", report)
            self.assertEqual(adapter.generation_calls, 1)
            self.assertEqual(adapter.download_calls, 0)
            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(state.items[0].status, WAITING_FOR_FLOW_LIMIT_RESET)
            self.assertEqual(state.items[0].attempt_count, 1)
            self.assertEqual(state.items[1].status, "pending")

    def test_run_batch_prints_full_specific_current_model_informationally(self):
        class LiteAdapter(OfflinePromptAdapter):
            def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
                result = super().generate_one(
                    item=item,
                    settings=settings,
                    forbidden_candidate_ids=forbidden_candidate_ids,
                )
                return GenerationResult(
                    candidates=result.candidates,
                    details=json.dumps({"model": "Nano Banana 2 Lite"}),
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = LiteAdapter()
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=adapter
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertIn("Current Flow model: Nano Banana 2 Lite", output.getvalue())
            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(state.items[0].status, "completed")
            self.assertEqual(
                json.loads(state.items[0].result_details)["model"],
                "Nano Banana 2 Lite",
            )

    def test_nb2_51_item_preview_has_zero_browser_and_state_interaction(self):
        batch_path = Path(__file__).resolve().parents[1] / "data" / "sample_batch_51.json"
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "example_51_order_run"
            argv = [
                "app.py",
                "run-batch",
                str(batch_path),
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(Path(tmp) / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("preview must not create a browser adapter"),
            ), patch.object(
                app,
                "execute_sequential_batch",
                side_effect=AssertionError("preview must not execute the batch"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertFalse(run_dir.exists())
            self.assertIn("Sequential batch scope: 51 order(s)", output.getvalue())
            self.assertIn("PREVIEW ONLY", output.getvalue())

    def test_generate_one_preview_reports_and_preserves_prior_failed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "failed"
            state.items[0].attempt_count = 1
            state.items[0].error = "FlowAutomationError: prior safe preflight failure"
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()

            argv = [
                "app.py",
                "generate-one",
                str(batch_path),
                "--order",
                "1",
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app, "FlowSession", side_effect=AssertionError("browser adapter must not be created")
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            report = output.getvalue()
            self.assertIn("Persisted item status: failed", report)
            self.assertIn("Previous attempts: 1", report)
            self.assertIn("prior safe preflight failure", report)
            self.assertIn("Future real execution requires --retry", report)

    def test_download_one_preview_lists_candidates_without_interaction_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":2,"requested_variants":2}'
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    asset_url="https://flow-content.google/image/candidate-one",
                ),
                CandidateRecord(
                    variant=2,
                    suggested_filename="0001__scene_0001_visual_001__v02.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-two",
                    asset_url="https://flow-content.google/image/candidate-two",
                ),
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            argv = [
                "app.py",
                "download-one",
                str(batch_path),
                "--order",
                "1",
                "--preview",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "FlowSession",
                side_effect=AssertionError("browser adapter must not be created"),
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)
            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            self.assertFalse((run_dir / "assets").exists())
            report = output.getvalue()
            self.assertIn("Candidate v01:", report)
            self.assertIn(
                "persisted planned name: 0001__scene_0001_visual_001__v01.png",
                report,
            )
            self.assertIn("persisted identity: candidate-one", report)
            self.assertIn("Download submenu", report)
            self.assertIn("1K Original size", report)
            self.assertIn("v01.<actual-extension>", report)
            self.assertIn("v02.<actual-extension>", report)
            self.assertIn(str(run_dir / "assets"), report)
            self.assertIn("no browser interaction, clicks, downloads", report)

    def test_download_trigger_trace_requires_explicit_click_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].download_status = "failed"
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            argv = [
                "app.py",
                "trace-download-trigger",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--activation",
                "keyboard",
                "--run-dir",
                str(run_dir),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("browser session must not be created"),
            ), redirect_stderr(StringIO()):
                self.assertEqual(app.main(), 2)
            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            self.assertFalse((run_dir / "diagnostics").exists())

    def test_download_trigger_trace_is_restricted_to_candidate_v01(self):
        args = type("Args", (), {"variant": 2})()
        with self.assertRaisesRegex(ValueError, "restricted to candidate v01"):
            app.cmd_trace_download_trigger(args)

    def test_download_ui_probe_requires_confirmation_before_browser_or_files(self):
        args = type(
            "Args",
            (),
            {
                "variant": 1,
                "label": "foreground-visible",
                "samples": 3,
                "confirm_ui_probe": False,
            },
        )()
        with patch.object(
            app,
            "_flow_session_from_args",
            side_effect=AssertionError("browser session must not be created"),
        ), self.assertRaisesRegex(ValueError, "--confirm-ui-probe"):
            app.cmd_probe_download_ui(args)

    def test_download_ui_probe_can_load_the_existing_prompts_file_batch_shape(self):
        args = type(
            "Args",
            (),
            {
                "variant": 1,
                "label": "x1-minimized-parent-trace-1",
                "samples": 1,
                "confirm_ui_probe": True,
                "start_delay": 0,
                "batch": "prompts.txt",
                "prompts_file": True,
            },
        )()
        with patch.object(
            app,
            "load_prompts_file",
            side_effect=RuntimeError("prompt-wrapper-loader-selected"),
        ) as load_prompts, patch.object(app, "load_batch") as load_json, self.assertRaisesRegex(
            RuntimeError,
            "prompt-wrapper-loader-selected",
        ):
            app.cmd_probe_download_ui(args)

        load_prompts.assert_called_once_with(Path("prompts.txt"))
        load_json.assert_not_called()
        parsed = app.build_parser().parse_args(
            [
                "probe-download-ui",
                "prompts.txt",
                "--prompts-file",
                "--order",
                "2",
                "--variant",
                "1",
                "--candidate-id",
                "candidate-id",
                "--label",
                "x1-minimized-parent-trace-1",
                "--confirm-ui-probe",
            ]
        )
        self.assertTrue(parsed.prompts_file)

    def test_download_ui_probe_requires_exact_persisted_candidate_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":1,"requested_variants":1}'
            state.items[0].download_status = "failed"
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="persisted-candidate",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            argv = [
                "app.py",
                "probe-download-ui",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--candidate-id",
                "different-candidate",
                "--label",
                "foreground-visible",
                "--samples",
                "3",
                "--start-delay",
                "0",
                "--run-dir",
                str(run_dir),
                "--confirm-ui-probe",
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("browser session must not be created"),
            ), redirect_stderr(StringIO()) as error:
                self.assertEqual(app.main(), 2)
            self.assertIn("does not match", error.getvalue())
            self.assertFalse((run_dir / "diagnostics").exists())

    def test_download_ui_probe_preserves_label_state_assets_and_safety_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":1,"requested_variants":1}'
            state.items[0].download_status = "failed"
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="persisted-candidate",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            asset_dir = run_dir / "assets"
            asset_dir.mkdir()
            existing_asset = asset_dir / "existing.jpeg"
            existing_asset.write_bytes(b"existing canonical bytes")
            state_before = (run_dir / "run_state.json").read_bytes()
            asset_before = existing_asset.read_bytes()
            fake_session = MagicMock()
            fake_session.probe_download_ui.return_value = {
                "schema": "nano-banana-download-ui-probe/v1",
                "label": "covered-unfocused",
                "candidate_id": "persisted-candidate",
                "samples": [
                    {
                        "condition": "covered-unfocused",
                        "sample": number,
                        "toolbar_ms": 10.0,
                        "more_options_ms": 2.0,
                        "download_parent_ms": 12.0,
                        "submenu_ms": 20.0,
                        "one_k_ready_ms": 3.0,
                        "reveal_attempts": 1,
                        "submenu_polls": 2,
                        "candidate_tile_remounted": False,
                        "more_options_remounted": False,
                        "download_parent_remounted": False,
                        "status": "success",
                    }
                    for number in range(1, 4)
                ],
                "summary": {
                    "condition": "covered-unfocused",
                    "success_count": 3,
                    "sample_count": 3,
                    "success_rate": 1.0,
                    "median_more_options_ms": 10.0,
                    "median_submenu_ms": 20.0,
                    "median_total_1k_ready_ms": 47.0,
                    "total_reveal_retries": 0,
                    "candidate_tile_remounts": 0,
                    "more_options_remounts": 0,
                    "download_parent_remounts": 0,
                },
                "focus_emulation_active": True,
                "final_document_has_focus": True,
                "final_document_visibility_state": "visible",
                "safety": {
                    "start_generation_activated": False,
                    "one_k_activated": False,
                    "native_download_tracking_configured": False,
                    "canonical_asset_committed": False,
                    "bring_to_front_called": False,
                },
            }
            failed_sample = fake_session.probe_download_ui.return_value["samples"][0]
            failed_sample.update(
                {
                    "status": "failure",
                    "parent_activation_count": 1,
                    "pre_activation_poll_count": 5,
                    "post_activation_poll_count": 4,
                    "activation_dispatch_returned_normally": True,
                    "failure_classification": "DOWNLOAD_SUBMENU_NOT_FOUND",
                    "final_safe_failure_classification": (
                        "DOWNLOAD_SUBMENU_NOT_FOUND"
                    ),
                    "final_parent": {
                        "connected": True,
                        "visible": True,
                        "enabled": True,
                        "pointer_events": "auto",
                        "aria_expanded": "false",
                        "aria_controls": None,
                    },
                    "cleanup_neutral_state": False,
                    "remaining_visible_menu_count": 1,
                    "remaining_visible_submenu_count": 0,
                }
            )
            fake_session.probe_download_ui.return_value["summary"]["success_count"] = 2
            fake_session.probe_download_ui.return_value["summary"]["success_rate"] = 2 / 3
            argv = [
                "app.py",
                "probe-download-ui",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--candidate-id",
                "persisted-candidate",
                "--label",
                "covered-unfocused",
                "--samples",
                "3",
                "--start-delay",
                "0",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
                "--confirm-ui-probe",
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=fake_session,
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), state_before)
            self.assertEqual(existing_asset.read_bytes(), asset_before)
            report_path = (
                run_dir
                / "diagnostics"
                / "download_ui_probe_order_0001_v01_covered-unfocused.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["label"], "covered-unfocused")
            self.assertTrue(report["run_state_unchanged"])
            self.assertTrue(report["canonical_assets_unchanged"])
            fake_session.probe_download_ui.assert_called_once()
            called = fake_session.probe_download_ui.call_args.kwargs
            self.assertEqual(called["candidate"].candidate_id, "persisted-candidate")
            self.assertEqual(called["label"], "covered-unfocused")
            self.assertEqual(called["samples"], 3)
            fake_session.generate_one.assert_not_called()
            fake_session.download_candidate.assert_not_called()
            fake_session.trace_download_trigger.assert_not_called()
            self.assertIn("failure_detail", output.getvalue())
            self.assertIn("DOWNLOAD_SUBMENU_NOT_FOUND", output.getvalue())
            self.assertIn("Stopped at exact 1K readiness", output.getvalue())

    def test_confirmed_download_trigger_trace_writes_report_without_state_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":1,"requested_variants":1}'
            state.items[0].download_status = "failed"
            state.items[0].download_attempt_count = 5
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            fake_session = MagicMock()
            fake_session.trace_download_trigger.return_value = {
                "schema": "nano-banana-download-trigger-trace/v2",
                "candidate_id": "candidate-one",
                "status": "trace_complete",
                "outcome": "post_click_activity_observed",
                "events": [{"kind": "fetch_call"}],
                "staging_files": [],
            }
            argv = [
                "app.py",
                "trace-download-trigger",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--activation",
                "keyboard",
                "--trace-label",
                "regression",
                "--timeout",
                "8",
                "--confirm-download-activation",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=fake_session,
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            report_path = (
                run_dir
                / "diagnostics"
                / "download_trigger_order_0001_v01_keyboard_regression.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["run_state_unchanged"])
            self.assertEqual(report["order"], 1)
            self.assertEqual(report["variant"], 1)
            self.assertEqual(report["activation"], "keyboard")
            self.assertEqual(report["trace_label"], "regression")
            self.assertIn("Diagnostic report:", output.getvalue())
            fake_session.trace_download_trigger.assert_called_once()
            called_candidate = fake_session.trace_download_trigger.call_args.kwargs["candidate"]
            self.assertEqual(called_candidate.candidate_id, "candidate-one")
            self.assertEqual(
                fake_session.trace_download_trigger.call_args.kwargs["timeout_seconds"],
                8,
            )
            self.assertEqual(
                fake_session.trace_download_trigger.call_args.kwargs["activation"],
                "keyboard",
            )
            fake_session.generate_one.assert_not_called()
            fake_session.download_candidate.assert_not_called()

    def test_manual_download_trace_requires_confirmation_without_creating_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].download_status = "failed"
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            argv = [
                "app.py",
                "trace-download-manual",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--trace-label",
                "physical-user-click",
                "--run-dir",
                str(run_dir),
            ]
            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                side_effect=AssertionError("browser session must not be created"),
            ), redirect_stderr(StringIO()):
                self.assertEqual(app.main(), 2)
            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            self.assertFalse((run_dir / "diagnostics").exists())

    def test_confirmed_manual_trace_writes_immutable_report_without_state_or_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":1,"requested_variants":1}'
            state.items[0].attempt_count = 2
            state.items[0].download_status = "failed"
            state.items[0].download_attempt_count = 5
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            fake_session = MagicMock()

            def passive_trace(**kwargs):
                kwargs["ready_callback"]()
                return {
                    "schema": "nano-banana-manual-download-trace/v1",
                    "trace_mode": "passive_manual",
                    "candidate_id": "candidate-one",
                    "status": "trace_complete",
                    "outcome": "MANUAL_TRACE_NO_DOWNLOAD_ACTIVATION",
                    "events": [],
                    "staging_files": [],
                }

            fake_session.trace_download_manual.side_effect = passive_trace
            argv = [
                "app.py",
                "trace-download-manual",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--timeout",
                "30",
                "--trace-label",
                "physical-user-click",
                "--confirm-manual-trace",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=fake_session
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            report_path = (
                run_dir
                / "diagnostics"
                / "download_manual_order_0001_v01_physical-user-click.json"
            )
            staging_path = (
                run_dir
                / "diagnostics"
                / "download_manual_order_0001_v01_physical-user-click_staging"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["run_state_unchanged"])
            self.assertEqual(report["trace_mode"], "passive_manual")
            self.assertTrue(staging_path.is_dir())
            self.assertFalse((run_dir / "assets").exists())
            self.assertIn("READY FOR MANUAL DOWNLOAD TEST", output.getvalue())
            self.assertIn("Hover candidate v01", output.getvalue())
            fake_session.trace_download_manual.assert_called_once()
            called = fake_session.trace_download_manual.call_args.kwargs
            self.assertEqual(called["candidate"].candidate_id, "candidate-one")
            self.assertEqual(called["timeout_seconds"], 30)
            self.assertTrue(callable(called["ready_callback"]))
            fake_session.trace_download_trigger.assert_not_called()
            fake_session.generate_one.assert_not_called()
            fake_session.download_candidate.assert_not_called()

    def test_passive_submenu_command_writes_report_without_state_or_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            run_dir = root / "run"
            batch_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "order": 1,
                                "visual_id": "scene_0001_visual_001",
                                "prompt": "safe test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            batch = app.load_batch(batch_path)
            state = app.initialize_run_state(batch, run_dir)
            state.items[0].status = "completed"
            state.items[0].completed_at = "2026-01-01T00:00:00+00:00"
            state.items[0].result_details = '{"configured_variants":1,"requested_variants":1}'
            state.items[0].attempt_count = 2
            state.items[0].download_status = "failed"
            state.items[0].download_attempt_count = 5
            state.items[0].candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__scene_0001_visual_001__v01.png",
                    source_kind="rendered-image",
                    candidate_id="candidate-one",
                    download_status="failed",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            before = (run_dir / "run_state.json").read_bytes()
            fake_session = MagicMock()

            def passive_inspection(**kwargs):
                kwargs["ready_callback"]()
                return {
                    "schema": "nano-banana-download-submenu-inspection/v1",
                    "inspection_mode": "passive_manual_dom",
                    "candidate_id": "candidate-one",
                    "status": "inspection_complete",
                    "outcome": "DOWNLOAD_SUBMENU_OBSERVED",
                    "snapshot": {"visible_menu_count": 2, "submenu_detected": True},
                }

            fake_session.inspect_download_submenu.side_effect = passive_inspection
            argv = [
                "app.py",
                "inspect-download-submenu",
                str(batch_path),
                "--order",
                "1",
                "--variant",
                "1",
                "--timeout",
                "30",
                "--run-dir",
                str(run_dir),
                "--config",
                str(root / "missing-config.json"),
            ]
            with patch("sys.argv", argv), patch.object(
                app, "_flow_session_from_args", return_value=fake_session
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual((run_dir / "run_state.json").read_bytes(), before)
            report_path = (
                run_dir / "diagnostics" / "download_submenu_order_0001_v01.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["run_state_unchanged"])
            self.assertEqual(report["inspection_mode"], "passive_manual_dom")
            self.assertFalse((run_dir / "assets").exists())
            self.assertIn("READY FOR MANUAL SUBMENU INSPECTION", output.getvalue())
            self.assertIn("Do NOT select anything from the submenu", output.getvalue())
            fake_session.inspect_download_submenu.assert_called_once()
            called = fake_session.inspect_download_submenu.call_args.kwargs
            self.assertEqual(called["candidate"].candidate_id, "candidate-one")
            self.assertEqual(called["timeout_seconds"], 30)
            fake_session.trace_download_manual.assert_not_called()
            fake_session.trace_download_trigger.assert_not_called()
            fake_session.download_candidate.assert_not_called()
            fake_session.generate_one.assert_not_called()

    def test_project_ui_recon_command_is_terminal_only_and_preserves_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "run_state.json"
            state_path.write_bytes(b"historical run state bytes\n")
            before = state_path.read_bytes()
            fake_session = MagicMock()
            fake_session.inspect_project_ui_controls.return_value = {
                "schema": "nano-banana-flow-project-ui-inspection/v1",
                "inspection_mode": "read_only_dom",
                "current_url": "https://flow.google.com/project/new-project",
                "is_flow_url": True,
                "is_project_url": True,
                "project_id": "new-project",
                "candidate_limit": 50,
                "candidate_count": 1,
                "truncated": False,
                "candidates": [],
            }
            argv = [
                "app.py",
                "inspect-flow-project-ui",
                "--config",
                str(root / "missing-config.json"),
            ]

            with patch("sys.argv", argv), patch.object(
                app,
                "_flow_session_from_args",
                return_value=fake_session,
            ), redirect_stdout(StringIO()) as output:
                self.assertEqual(app.main(), 0)

            self.assertEqual(state_path.read_bytes(), before)
            self.assertIn("READ-ONLY PROJECT UI INSPECTION", output.getvalue())
            fake_session.open_read_only_project_reconnaissance.assert_called_once_with()
            fake_session.inspect_project_ui_controls.assert_called_once_with(limit=50)
            fake_session.close.assert_called_once_with()
            fake_session.open.assert_not_called()
            fake_session.generate_one.assert_not_called()
            fake_session.download_candidate.assert_not_called()

    def test_current_project_recon_command_is_terminal_only_and_bounded(self):
        fake_session = MagicMock()
        fake_session.inspect_current_flow_project.return_value = {
            "schema": "nano-banana-current-flow-project-inspection/v1",
            "inspection_mode": "read_only_dom",
            "current_url": "https://flow.google.com/project/new-project",
            "is_flow_url": True,
            "is_project_url": True,
            "project_id": "new-project",
            "editor_ready": True,
            "dialog_controls": [],
        }
        argv = ["app.py", "inspect-current-flow-project", "--limit", "7"]

        with patch("sys.argv", argv), patch.object(
            app,
            "_flow_session_from_args",
            return_value=fake_session,
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(app.main(), 0)

        self.assertIn("READ-ONLY CURRENT PROJECT INSPECTION", output.getvalue())
        fake_session.open_read_only_project_reconnaissance.assert_called_once_with()
        fake_session.inspect_current_flow_project.assert_called_once_with(dialog_limit=7)
        fake_session.close.assert_called_once_with()
        fake_session.open.assert_not_called()
        fake_session.generate_one.assert_not_called()
        fake_session.download_candidate.assert_not_called()

    def test_project_reconnaissance_cli_commands_parse_without_launcher_changes(self):
        project_ui = app.build_parser().parse_args(
            ["inspect-flow-project-ui", "--limit", "12"]
        )
        current = app.build_parser().parse_args(
            ["inspect-current-flow-project", "--limit", "9"]
        )
        self.assertIs(project_ui.func, app.cmd_inspect_flow_project_ui)
        self.assertEqual(project_ui.limit, 12)
        self.assertIs(current.func, app.cmd_inspect_current_flow_project)
        self.assertEqual(current.limit, 9)


if __name__ == "__main__":
    unittest.main()
