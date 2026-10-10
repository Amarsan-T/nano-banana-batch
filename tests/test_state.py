import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nano_batch.errors import (
    FLOW_GENERIC_GENERATION_FAILED,
    FLOW_POLICY_REJECTED,
    FLOW_USAGE_LIMIT_REACHED,
    WAITING_FOR_FLOW_LIMIT_RESET,
)
from nano_batch.models import CandidateRecord, RunItemState, RunState
from nano_batch.state import RunStateStore, StatePersistenceError


class StateTests(unittest.TestCase):
    @staticmethod
    def pending_state(*, updated_at="now"):
        return RunState(
            schema="nano-banana-batch-run-state",
            version=1,
            batch_id="sha256:test",
            items=[RunItemState(order=1, visual_id="v1", prompt="hello")],
            created_at="now",
            updated_at=updated_at,
        )

    def test_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            real_replace = os.replace
            with patch(
                "nano_batch.state.os.replace",
                wraps=real_replace,
            ) as replace:
                store.save(self.pending_state())
            loaded = store.load()
            self.assertEqual(loaded.batch_id, "sha256:test")
            self.assertEqual(loaded.items[0].visual_id, "v1")
            self.assertEqual(loaded.items[0].status, "pending")
            replace.assert_called_once()
            source, target = replace.call_args.args
            self.assertEqual(Path(source).parent, store.path.parent)
            self.assertEqual(Path(target), store.path)

    def test_atomic_replace_retries_one_permission_error_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            real_replace = os.replace
            calls = 0

            def flaky_replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError(13, "transient access denied")
                return real_replace(source, target)

            with patch(
                "nano_batch.state.os.replace",
                side_effect=flaky_replace,
            ), patch("nano_batch.state.time.sleep") as sleep:
                store.save(self.pending_state())

            self.assertEqual(calls, 2)
            sleep.assert_called_once_with(0.05)
            self.assertEqual(store.load().batch_id, "sha256:test")

    def test_atomic_replace_retries_several_winerror_five_failures_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            real_replace = os.replace
            calls = 0

            def flaky_replace(source, target):
                nonlocal calls
                calls += 1
                if calls <= 3:
                    error = OSError("transient Windows sharing violation")
                    error.winerror = 5
                    raise error
                return real_replace(source, target)

            with patch(
                "nano_batch.state.os.replace",
                side_effect=flaky_replace,
            ), patch("nano_batch.state.time.sleep") as sleep:
                store.save(self.pending_state())

            self.assertEqual(calls, 4)
            self.assertEqual(
                [call.args[0] for call in sleep.call_args_list],
                [0.05, 0.10, 0.20],
            )
            self.assertEqual(store.load().updated_at, "now")

    def test_atomic_replace_retry_exhaustion_preserves_old_state_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            store.save(self.pending_state(updated_at="old"))
            old_bytes = store.path.read_bytes()

            with patch(
                "nano_batch.state.os.replace",
                side_effect=PermissionError(13, "persistent access denied"),
            ) as replace, patch("nano_batch.state.time.sleep") as sleep, self.assertRaisesRegex(
                StatePersistenceError,
                "previous run_state.json was left untouched",
            ):
                store.save(self.pending_state(updated_at="new"))

            self.assertEqual(replace.call_count, 4)
            self.assertEqual(sleep.call_count, 3)
            self.assertEqual(store.path.read_bytes(), old_bytes)
            self.assertEqual(store.load().updated_at, "old")
            self.assertEqual(
                list(store.path.parent.glob("run_state.json.*.tmp")),
                [],
            )

    def test_non_permission_replace_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            store.save(self.pending_state(updated_at="old"))
            old_bytes = store.path.read_bytes()
            with patch(
                "nano_batch.state.os.replace",
                side_effect=OSError(28, "disk full"),
            ) as replace, patch("nano_batch.state.time.sleep") as sleep, self.assertRaisesRegex(
                OSError,
                "disk full",
            ):
                store.save(self.pending_state(updated_at="new"))

            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(store.path.read_bytes(), old_bytes)
            self.assertEqual(
                list(store.path.parent.glob("run_state.json.*.tmp")),
                [],
            )

    def test_temp_handle_is_closed_before_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            real_fdopen = os.fdopen
            real_replace = os.replace
            opened = []

            def tracked_fdopen(*args, **kwargs):
                handle = real_fdopen(*args, **kwargs)
                opened.append(handle)
                return handle

            def checked_replace(source, target):
                self.assertEqual(len(opened), 1)
                self.assertTrue(opened[0].closed)
                return real_replace(source, target)

            with patch(
                "nano_batch.state.os.fdopen",
                side_effect=tracked_fdopen,
            ), patch(
                "nano_batch.state.os.replace",
                side_effect=checked_replace,
            ):
                store.save(self.pending_state())

            self.assertTrue(opened[0].closed)
            self.assertTrue(store.path.is_file())

    def test_atomic_replace_has_no_delete_then_rename_fallback(self):
        source = inspect.getsource(RunStateStore._replace_atomically)
        self.assertIn("os.replace", source)
        self.assertNotIn("unlink", source)
        self.assertNotIn("remove", source)
        self.assertNotIn("rename", source)

    def test_failed_item_keeps_identity(self):
        item = RunItemState(order=2, visual_id="scene_0002_visual_001", prompt="x", status="failed", attempt_count=1, error="test")
        self.assertEqual(item.order, 2)
        self.assertEqual(item.visual_id, "scene_0002_visual_001")

    def test_flow_limit_wait_state_and_machine_code_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            state = self.pending_state()
            state.items[0].status = WAITING_FOR_FLOW_LIMIT_RESET
            state.items[0].error = f"{FLOW_USAGE_LIMIT_REACHED}: visible Flow usage limit"
            state.items[0].generation_error_code = FLOW_USAGE_LIMIT_REACHED
            store.save(state)

            loaded = store.load()

            self.assertEqual(loaded.items[0].status, WAITING_FOR_FLOW_LIMIT_RESET)
            self.assertEqual(
                loaded.items[0].generation_error_code,
                FLOW_USAGE_LIMIT_REACHED,
            )

    def test_automatic_policy_retry_consumption_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            state = self.pending_state()
            state.items[0].status = "failed"
            state.items[0].attempt_count = 1
            state.items[0].generation_error_code = FLOW_POLICY_REJECTED
            state.items[0].automatic_policy_retry_count = 1
            store.save(state)

            loaded = store.load()

            self.assertEqual(loaded.items[0].automatic_policy_retry_count, 1)
            self.assertEqual(loaded.items[0].generation_error_code, FLOW_POLICY_REJECTED)

    def test_legacy_state_without_policy_retry_field_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            store.save(self.pending_state())
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            raw["items"][0].pop("automatic_policy_retry_count")
            store.path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertEqual(loaded.items[0].automatic_policy_retry_count, 0)

    def test_automatic_generic_failure_retry_consumption_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            state = self.pending_state()
            state.items[0].status = "failed"
            state.items[0].attempt_count = 3
            state.items[0].generation_error_code = FLOW_GENERIC_GENERATION_FAILED
            state.items[0].automatic_generic_failure_retry_count = 2
            store.save(state)

            loaded = store.load()

            self.assertEqual(
                loaded.items[0].automatic_generic_failure_retry_count,
                2,
            )
            self.assertEqual(
                loaded.items[0].generation_error_code,
                FLOW_GENERIC_GENERATION_FAILED,
            )

    def test_legacy_state_without_generic_retry_field_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            store.save(self.pending_state())
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            raw["items"][0].pop("automatic_generic_failure_retry_count")
            store.path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertEqual(
                loaded.items[0].automatic_generic_failure_retry_count,
                0,
            )

    def test_generic_failure_retry_count_is_bounded(self):
        item = RunItemState(
            order=1,
            visual_id="v1",
            prompt="hello",
            status="failed",
            attempt_count=4,
            automatic_generic_failure_retry_count=3,
        )

        with self.assertRaisesRegex(ValueError, "must be 0, 1, or 2"):
            item.validate()

    def test_candidate_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStateStore(Path(tmp) / "run_state.json")
            state = RunState(
                schema="nano-banana-batch-run-state",
                version=2,
                batch_id="sha256:test",
                items=[
                    RunItemState(
                        order=1,
                        visual_id="v1",
                        prompt="hello",
                        candidates=[
                            CandidateRecord(
                                variant=1,
                                suggested_filename="0001__v1__v01.png",
                                source_kind="rendered-image",
                                candidate_id="candidate-one",
                                browser_download_guid="guid-one",
                                download_mechanism="cdp_download_events",
                                download_status="completed",
                                local_path="C:\\project\\runs\\test\\assets\\0001__v1__v01.png",
                                local_relative_path="assets\\0001__v1__v01.png",
                                sha256="a" * 64,
                                byte_size=123,
                                width=16,
                                height=9,
                                mime_type="image/png",
                                image_format="PNG",
                                downloaded_at="later",
                            )
                        ],
                        download_status="completed",
                        download_attempt_count=1,
                        download_completed_at="later",
                    )
                ],
                created_at="now",
                updated_at="now",
            )
            store.save(state)
            loaded = store.load()
            self.assertEqual(loaded.items[0].candidates[0].variant, 1)
            self.assertEqual(loaded.items[0].candidates[0].suggested_filename, "0001__v1__v01.png")
            self.assertEqual(loaded.items[0].download_status, "completed")
            self.assertEqual(loaded.items[0].download_attempt_count, 1)
            self.assertEqual(loaded.items[0].candidates[0].candidate_id, "candidate-one")
            self.assertEqual(loaded.items[0].candidates[0].browser_download_guid, "guid-one")
            self.assertEqual(
                loaded.items[0].candidates[0].download_mechanism,
                "cdp_download_events",
            )
            self.assertEqual(loaded.items[0].candidates[0].sha256, "a" * 64)
            self.assertEqual(loaded.items[0].candidates[0].width, 16)
            self.assertEqual(loaded.items[0].candidates[0].height, 9)


if __name__ == "__main__":
    unittest.main()
