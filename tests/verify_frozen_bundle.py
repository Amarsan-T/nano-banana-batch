"""Explicit synthetic packaged acceptance, not automatic unittest discovery."""

import argparse
import ctypes
import csv
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nano_batch.worker_process import WorkerProcess
from nano_batch.worker_protocol import JobMode, JobRequest, StopRequest
from nano_batch.worker_protocol import decode_message
from nano_batch.execution_lock import execution_ownership
from nano_batch.state import RunStateStore


def wait_until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        threading.Event().wait(0.01)
    raise AssertionError("Synthetic acceptance deadline exceeded.")


def files(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()}


def gui_smoke(bundle, cwd):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen([str(bundle / "NanoBananaBatch.exe")], cwd=cwd, startupinfo=startup,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    def find():
        found = []
        @callback_type
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid:
                title = ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(hwnd, title, len(title))
                if title.value == "Nano Banana Batch - INTERNAL TEST BUILD":
                    found.append(hwnd)
            return True
        user.EnumWindows(visit, 0)
        if process.poll() is not None:
            raise AssertionError("GUI exited before creating its window.")
        return found[0] if found else None
    try:
        hwnd = wait_until(find)
        assert user.PostMessageW(hwnd, 0x10, 0, 0)  # Close only our disposable GUI.
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()  # Test timeout only, never an application Stop policy.
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("synthetic", type=Path)
    parser.add_argument("--result", type=Path, default=ROOT / ".tmp/stage5b/packaged-result.json")
    args = parser.parse_args()
    bundle, synthetic = args.bundle.resolve(), args.synthetic.resolve()
    results = []
    def passed(name):
        results.append(name)
        print("PASS:", name, flush=True)
    scratch = ROOT / ".tmp/stage5b/acceptance"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        root = Path(directory)
        prompts = root / "synthetic \u732b prompts.txt"
        prompts.write_text("Synthetic red mug.\nSynthetic blue vase.\n", encoding="utf-8")
        config = root / "synthetic-config.json"
        config.write_text("{}", encoding="utf-8")
        home = root / "user data \u732b"
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "PLAYWRIGHT_NODEJS_PATH"}}
        environment.update(NANO_BANANA_BATCH_DATA_DIR=str(home), NBB_FROZEN_TEST_ROOT=str(root),
                           PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
        with patch.dict(os.environ, environment, clear=True):
            before = files(root)
            gui_smoke(bundle, root)
            assert files(root) == before and not home.exists()
            passed("windowed GUI starts and closes without data writes")
            cli = subprocess.run([str(bundle / "NanoBananaBatchCLI.exe"), "--help"], capture_output=True, timeout=30)
            assert cli.returncode == 0 and not home.exists()
            passed("packaged CLI starts without Python or repository cwd")

            def invoke(folder, mode, run, *, test_mode="normal", stop=False):
                with patch.dict(os.environ, {"NBB_FROZEN_TEST_MODE": test_mode}), patch("nano_batch.runtime_paths.frozen", return_value=True):
                    worker = WorkerProcess(folder / ("NanoBananaBatch.exe" if folder == bundle else "NanoBananaBatchWorker.exe"))
                if stop:
                    worker.request_stop()
                else:
                    worker.submit(JobRequest(mode, str(prompts), str(run), str(config)))
                return worker

            def finish(worker, expected):
                messages = []
                def read():
                    value = worker.wait_message(0.05)
                    if value is not None:
                        messages.append(value.as_dict())
                        if value.kind == "terminal":
                            return value.as_dict()
                    return None
                terminal = wait_until(read)
                assert terminal["outcome"] == expected, terminal
                wait_until(worker.close)
                assert "synthetic-private-not-a-frame" not in repr(messages)
                return terminal

            run = root / "new run \u732b space"
            before = files(root)
            terminal = finish(invoke(bundle, JobMode.PREVIEW, run), "preview_complete")
            assert terminal["total"] == 2 and terminal["exit_code"] == 0
            assert not run.exists() and files(root) == before and not home.exists()
            passed("real frozen worker framing and read-only Preview")
            assert finish(invoke(bundle, JobMode.PREVIEW, run, stop=True), "interrupted")["exit_code"] == 130
            passed("real frozen worker cooperative Stop before job")
            bad = subprocess.run([str(bundle / "NanoBananaBatchWorker.exe")], input=b'{"v":999,"type":"stop"}\n',
                                 capture_output=True, timeout=30)
            messages = [decode_message(line + b"\n").as_dict() for line in bad.stdout.splitlines()]
            # Existing pre-job channel failure requests cooperative interruption.
            assert bad.returncode == 130 and messages[0]["type"] == "ready"
            assert messages[-1]["outcome"] == "interrupted" and messages[-1]["exit_code"] == 130
            assert messages[-1]["code"] == "PROTOCOL_ERROR"
            passed("real frozen worker rejects an incompatible protocol version")
            terminal = finish(invoke(synthetic, JobMode.EXECUTE, run), "completed")
            assert terminal["completed"] == 2 and terminal["exit_code"] == 0
            state = RunStateStore(run / "run_state.json").load()
            counts = [item.attempt_count for item in state.items]
            before = files(run)
            finish(invoke(synthetic, JobMode.EXECUTE, run, test_mode="forbid"), "completed")
            assert files(run) == before
            assert [item.attempt_count for item in RunStateStore(run / "run_state.json").load().items] == counts
            passed("synthetic sequential completion and skip-only Resume without writes")
            with execution_ownership(run):
                for target in (run, root / "another run"):
                    terminal = finish(invoke(synthetic, JobMode.EXECUTE, target), "blocked")
                    assert terminal["code"] == "OWNERSHIP_BUSY" and terminal["exit_code"] == 2
                finish(invoke(bundle, JobMode.PREVIEW, run), "preview_complete")
            passed("same-run/shared-Chrome locks and lock-free Preview")
            for mode in ("crash", "malformed"):
                finish(invoke(synthetic, JobMode.PREVIEW, run, test_mode=mode), "communication_lost")
            passed("worker crash and malformed output fail closed without leakage")

            prompts.write_text("Synthetic checkpoint image.\n", encoding="utf-8")
            interrupted = root / "checkpoint run"
            worker = invoke(synthetic, JobMode.EXECUTE, interrupted, test_mode="checkpoint_wait")
            try:
                wait_until(lambda: (root / "checkpoint-ready").exists())
                saved = RunStateStore(interrupted / "run_state.json").load()
                assert saved.items[0].status == "completed"
                attempts = saved.items[0].attempt_count
                assert worker.request_stop()
                def acknowledged():
                    value = worker.wait_message(0.05)
                    return value is not None and value.kind == "stop_ack"
                wait_until(acknowledged)
                assert worker.process.poll() is None
                terminal = finish(invoke(synthetic, JobMode.EXECUTE, interrupted), "blocked")
                assert terminal["code"] == "OWNERSHIP_BUSY"
                (root / "release").write_text("release")
                assert finish(worker, "interrupted")["exit_code"] == 130
            finally:
                (root / "release").write_text("release")
                worker.process.wait(timeout=30)
                wait_until(worker.close)
            finish(invoke(synthetic, JobMode.EXECUTE, interrupted, test_mode="download_only"), "completed")
            saved = RunStateStore(interrupted / "run_state.json").load()
            assert saved.items[0].attempt_count == attempts
            passed("delayed Stop retains lock/checkpoint and download-only Resume")
            from PIL import Image
            images = list(run.glob("assets/*.jpeg")) + list(interrupted.glob("assets/*.jpeg"))
            assert len(images) == 3
            for image in images:
                with Image.open(image) as decoded:
                    decoded.load()
                    assert decoded.format == "JPEG" and decoded.size == (16, 9)
            passed("all synthetic canonical JPEGs fully decode")
            probe = subprocess.run([str(synthetic / "NanoBananaBatchWorker.exe"), "--driver-probe"],
                                   capture_output=True, timeout=60)
            assert probe.returncode == 0, "Frozen driver probe failed (private details withheld)."
            assert json.loads(probe.stdout)["driver_start_stop"]
            passed("frozen Tcl/Pillow/native extensions and browser-free Playwright driver")
            read_only_owner = tempfile.TemporaryDirectory(prefix="ro-", dir=ROOT / ".tmp")
            read_only = Path(read_only_owner.name) / "\u732b space"
            shutil.copytree(bundle, read_only)
            assert read_only.resolve().is_relative_to(ROOT / ".tmp")
            who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
            sid = next(csv.reader(who.stdout.splitlines()))[1]
            def access(block_writes):
                # Preserve inherited read/execute and service permissions. Replacing
                # the DACL with RX alone can make this hosted environment unreadable.
                arguments = (["/deny", f"*{sid}:(OI)(CI)(WD,AD)"] if block_writes
                             else ["/remove:d", f"*{sid}"])
                result = subprocess.run(["icacls", str(read_only), *arguments, "/T", "/C"], capture_output=True)
                assert result.returncode == 0, "Disposable installation ACL setup failed."
            try:
                access(True)
                try:
                    (read_only / "forbidden-write").write_bytes(b"synthetic")
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Installation directory was not genuinely read-only.")
                assert (read_only / "LICENSE").read_bytes()
                try:
                    with (read_only / "LICENSE").open("ab") as handle:
                        handle.write(b"synthetic")
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Existing installation file was writable.")
                before = files(read_only)
                gui_smoke(read_only, root)
                finish(invoke(read_only, JobMode.PREVIEW, interrupted), "preview_complete")
                assert files(read_only) == before
                assert not home.exists()
                passed("genuinely read-only Unicode installation with independent writable inputs")
            finally:
                access(False)
                read_only_owner.cleanup()
            passed("all executable probes ran with Python/Node absent from PATH")
    args.result.write_text(json.dumps({"passed": len(results), "results": results,
                                      "real_browser_operations": 0, "live_flow_operations": 0}, indent=2), encoding="utf-8")
    print("Packaged acceptance:", len(results), "passed")


if __name__ == "__main__":
    main()
