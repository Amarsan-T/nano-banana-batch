"""Synthetic subprocess supervision and IPC. No Flow/browser connections."""

import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nano_batch.execution_lock import ExecutionBusy, execution_ownership
from nano_batch.worker_process import WorkerProcess
from nano_batch.worker_protocol import JobMode, JobRequest, message, encode
from pending_download_fixtures import pending_download_test_directory


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        directory = pending_download_test_directory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.prompts = self.root / "synthetic \u732b prompts.txt"
        self.prompts.write_text("Synthetic still life.\n", encoding="utf-8")
        self.run = self.root / "synthetic new run"
        self.config = self.root / "synthetic-config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.request = JobRequest(JobMode.PREVIEW, str(self.prompts), str(self.run), str(self.config))
        self.received = []

    def worker(self, script=None):
        if script is None:
            worker = WorkerProcess(Path(sys.executable))
        else:
            original = subprocess.Popen
            def fake_command(arguments, **kwargs):
                self.assertEqual(arguments, [sys.executable, "-B", "-u", "-m", "nano_batch.gui_worker"])
                self.assertIs(kwargs["shell"], False)
                return original([sys.executable, "-B", "-u", "-c", script], **kwargs)
            with patch("nano_batch.worker_process.subprocess.Popen", side_effect=fake_command):
                worker = WorkerProcess(Path(sys.executable))
        self.addCleanup(self.cleanup, worker)
        return worker

    @staticmethod
    def cleanup(worker):
        worker.request_stop()
        worker.process.wait(timeout=20)
        worker._reader_done.wait(5)
        worker.close()

    def wait_for(self, worker, kind):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            values = worker.drain()
            self.received.extend(values)
            found = next((value for value in values if value.kind == kind), None)
            if found is not None:
                return found
            threading.Event().wait(0.005)
        self.fail("Synthetic worker message did not arrive")

    def fake_adapter_script(self, before="pass"):
        return (
            "import sys\nsys.path.insert(0,'tests')\n"
            "import nano_batch.gui_worker as worker\n"
            "from test_gui_worker import SyntheticAdapter\n"
            "from nano_batch.cancellation import current_cancellation_state\n"
            "adapter=SyntheticAdapter()\n"
            "def before():\n" + "\n".join(" " + line for line in before.splitlines()) + "\n"
            "adapter.before_generation=before\n"
            "original=worker.serve\n"
            "def serve(source,write): return original(source,write,adapter_factory=lambda *_args:adapter)\n"
            "worker.serve=serve\nraise SystemExit(worker.main())\n"
        )

    def test_real_worker_entry_preview_sanitizes_output_and_does_not_create_run(self):
        worker = self.worker()
        self.assertEqual(self.wait_for(worker, "ready").kind, "ready")
        worker.submit(self.request)
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual((result["outcome"], result["exit_code"]), ("preview_complete", 0))
        self.assertFalse(self.run.exists())
        self.assertTrue(any(value.kind == "preview" for value in self.received))
        self.assertNotIn(str(self.root), repr(self.received))
        self.assertNotIn("Synthetic still life", repr(self.received))

    def test_normal_subprocess_execution_uses_only_injected_fake_adapter(self):
        worker = self.worker(self.fake_adapter_script())
        worker.submit(JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config)))
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual((result["outcome"], result["completed"]), ("completed", 1))
        self.assertEqual(worker.process.wait(timeout=15), 0)

    def test_native_and_original_stdout_cannot_leak_into_protocol(self):
        worker = self.worker(self.fake_adapter_script(
            "__import__('os').write(1,b'synthetic private native output\\n'); "
            "sys.__stdout__.write('synthetic private original output\\n'); sys.__stdout__.flush()"
        ))
        worker.submit(JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config)))
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual(result["outcome"], "completed")
        self.assertNotIn("private", repr(self.received))

    def test_stop_while_waiting_for_job_is_cooperative_and_idempotent(self):
        worker = self.worker()
        self.wait_for(worker, "ready")
        self.assertTrue(worker.request_stop())
        self.assertFalse(worker.request_stop())
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual(result["exit_code"], 130)
        self.assertEqual(worker.process.wait(timeout=15), 130)
        self.assertFalse(self.run.exists())

    def test_stop_pending_while_sdk_blocked_retains_os_ownership_until_cleanup(self):
        entered = self.root / "synthetic-sdk-entered"
        release = self.root / "synthetic-sdk-release"
        before = (
            "from pathlib import Path\nimport time,threading\n"
            f"Path({str(entered)!r}).touch()\n"
            f"release=Path({str(release)!r})\n"
            "deadline=time.monotonic()+15\n"
            "while not release.exists():\n"
            " if time.monotonic()>deadline: raise AssertionError('Synthetic SDK barrier timed out')\n"
            " threading.Event().wait(0.005)"
        )
        worker = self.worker(self.fake_adapter_script(before))
        worker.submit(JobRequest(JobMode.EXECUTE, str(self.prompts), str(self.run), str(self.config)))
        try:
            deadline = time.monotonic() + 15
            while not entered.exists() and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            self.assertTrue(entered.exists())
            self.assertTrue(worker.request_stop())
            ack = self.wait_for(worker, "stop_ack").as_dict()
            self.assertEqual(ack["status"], "pending")
            self.assertIsNone(worker.process.poll())
            with self.assertRaises(ExecutionBusy):
                with execution_ownership(self.root / "different run"):
                    pass
        finally:
            release.touch()
        self.assertEqual(self.wait_for(worker, "terminal").as_dict()["exit_code"], 130)
        worker.process.wait(timeout=15)
        with execution_ownership(self.run):
            pass

    def test_late_stop_is_ignored_and_close_is_idempotent_after_process_exit(self):
        worker = self.worker()
        worker.submit(self.request)
        self.wait_for(worker, "terminal")
        self.assertFalse(worker.request_stop())
        worker.process.wait(timeout=15)
        worker._reader_done.wait(5)
        self.assertTrue(worker.close())
        self.assertTrue(worker.close())

    def test_second_job_or_job_after_stop_is_rejected(self):
        worker = self.worker()
        worker.submit(self.request)
        with self.assertRaises(ValueError):
            worker.submit(self.request)
        self.wait_for(worker, "terminal")
        with self.assertRaises(ValueError):
            worker.submit(self.request)

    def test_close_of_active_worker_requests_stop_and_keeps_supervision(self):
        worker = self.worker()
        self.wait_for(worker, "ready")
        self.assertFalse(worker.close())
        self.assertEqual(self.wait_for(worker, "terminal").as_dict()["exit_code"], 130)
        worker.process.wait(timeout=15)
        worker._reader_done.wait(5)
        self.assertTrue(worker.close())

    def test_explicit_executable_path_required(self):
        for path in (Path("python.exe"), self.root / "missing-python.exe"):
            with self.assertRaises(ValueError):
                WorkerProcess(path)

    def test_worker_crash_or_clean_exit_without_terminal_is_communication_loss(self):
        for code in (0, 7):
            worker = self.worker(f"import os\nos._exit({code})\n")
            result = self.wait_for(worker, "terminal").as_dict()
            self.assertEqual((result["outcome"], result["code"]), ("communication_lost", "WORKER_LOST"))
            self.assertFalse(self.run.exists())

    def test_malformed_or_private_worker_output_never_becomes_gui_diagnostics(self):
        for frame in (b"synthetic private prompt\n", b"x" * 9000 + b"\n",
                      b'{"v":99,"type":"ready","token":"synthetic private"}\n'):
            worker = self.worker(f"import os\nos.write(1,{frame!r})\n")
            result = self.wait_for(worker, "terminal").as_dict()
            self.assertEqual(result["outcome"], "communication_lost")
            self.assertNotIn("private", repr(result))

    def test_many_progress_frames_are_bounded_and_terminal_not_lost(self):
        from nano_batch.progress import ProgressReporter, ProgressPhase
        from nano_batch.worker_protocol import progress_message
        reporter = ProgressReporter((1,))
        reporter.record(ProgressPhase.GENERATION, order=1)
        frame = encode(progress_message(reporter.snapshot))
        terminal = encode(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
        ready = encode(message("ready"))
        script = f"import os\nos.write(1,{ready!r})\nfor _ in range(2000): os.write(1,{frame!r})\nos.write(1,{terminal!r})\n"
        worker = self.worker(script)
        worker.process.wait(timeout=15)
        self.assertTrue(worker._reader_done.wait(5))
        values = worker.drain()
        self.assertLessEqual(len(values), 10)
        self.assertEqual(values[-1].kind, "terminal")
        self.assertEqual(dict(values[-1].fields)["outcome"], "completed")
        self.assertGreater(worker._messages.coalesced, 0)

    def test_crash_after_terminal_or_exit_mismatch_cannot_claim_success(self):
        terminal = encode(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
        ready = encode(message("ready"))
        worker = self.worker(f"import os\nos.write(1,{ready!r}+{terminal!r})\nos._exit(7)\n")
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual(result["outcome"], "communication_lost")

    def test_trailing_output_after_terminal_is_rejected_privately(self):
        terminal = encode(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
        ready = encode(message("ready"))
        worker = self.worker(f"import os\nos.write(1,{ready!r}+{terminal!r}+b'synthetic private detail\\n')\n")
        result = self.wait_for(worker, "terminal").as_dict()
        self.assertEqual(result["outcome"], "communication_lost")
        self.assertNotIn("private", repr(result))

    def test_missing_or_repeated_readiness_is_a_protocol_failure(self):
        ready = encode(message("ready"))
        terminal = encode(message("terminal", outcome="completed", exit_code=0, code=None, total=1, completed=1))
        for frames in (terminal, ready + ready + terminal):
            worker = self.worker(f"import os\nos.write(1,{frames!r})\n")
            result = self.wait_for(worker, "terminal").as_dict()
            self.assertEqual(result["outcome"], "communication_lost")
