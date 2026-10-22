"""Explicit offline setup-code acceptance for the separate TEST-ONLY worker.

Never point this at the application bundle or an existing run. No driver/browser
is started: the test entry point injects fake Playwright objects before CDP.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nano_batch.state import RunStateStore
from nano_batch.worker_process import WorkerProcess
from nano_batch.worker_protocol import JobMode, JobRequest, VERSION


def finish(worker):
    messages = []
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            value = worker.wait_message(0.05)
            if value is not None:
                messages.append(value.as_dict())
                if value.kind == "terminal":
                    return value.as_dict(), messages
        raise AssertionError("Synthetic worker deadline exceeded.")
    finally:
        worker.request_stop()
        worker.process.wait(timeout=30)
        while not worker.close():
            if time.monotonic() > deadline:
                raise AssertionError("Synthetic worker did not close its handles.")
            time.sleep(0.01)  # Test supervisor only; never a production wait.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("synthetic", type=Path, help="Fresh TEST-ONLY FrozenAcceptance folder")
    parser.add_argument("--result", type=Path, required=True, help="Ignored result JSON path")
    args = parser.parse_args()
    executable = args.synthetic.resolve(strict=True) / "NanoBananaBatchWorker.exe"
    scratch = ROOT / ".tmp" / "setup-diagnostic-acceptance"
    scratch.mkdir(parents=True, exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        root = Path(directory)
        prompts = root / "synthetic \u732b prompts.txt"
        prompts.write_text("Synthetic harmless still life.\n", encoding="utf-8")
        config = root / "synthetic-config.json"
        config.write_text("{}", encoding="utf-8")
        original = {path.name: path.read_bytes() for path in (prompts, config)}
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "PLAYWRIGHT_NODEJS_PATH"}}
        environment.update(NANO_BANANA_BATCH_DATA_DIR=str(root / "synthetic data"),
                           NBB_FROZEN_TEST_ROOT=str(root),
                           PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
        with patch.dict(os.environ, environment, clear=True):
            def invoke(mode, run, *, stop=False):
                with patch.dict(os.environ, {"NBB_FROZEN_TEST_MODE": mode}), patch("nano_batch.runtime_paths.frozen", return_value=True):
                    worker = WorkerProcess(executable)
                if stop:
                    worker.request_stop()
                else:
                    worker.submit(JobRequest(JobMode.EXECUTE, str(prompts), str(run), str(config)))
                return finish(worker)

            cases = (("project", "SETUP_PROJECT_OPERATION"), ("driver", "SETUP_DRIVER_RESOURCE"),
                     ("reconnect", "SETUP_RECONNECT_OPERATION"), ("editor", "SETUP_EDITOR_OPERATION"),
                     ("settings", "SETUP_SETTINGS_INVALID_INPUT"), ("cleanup", "SETUP_CLEANUP_OPERATION"))
            for mode, code in cases:
                run = root / ("new synthetic run \u732b " + mode)
                terminal, messages = invoke("setup_" + mode, run)
                assert (terminal["outcome"], terminal["exit_code"], terminal["code"], terminal["total"]) == ("failed", 2, code, 1)
                assert terminal["v"] == VERSION == 1
                assert set(terminal) == {"v", "type", "outcome", "exit_code", "code", "completed", "total"}
                state = RunStateStore(run / "run_state.json").load()
                assert (state.flow_project is not None) == (mode != "project")
                item = state.items[0]
                assert (item.status, item.attempt_count, item.started_at, item.completed_at, item.error) == ("pending", 0, None, None, None)
                assert not item.candidates and not item.output_files
                assert (item.download_attempt_count, item.automatic_policy_retry_count, item.automatic_generic_failure_retry_count) == (0, 0, 0)
                wire = json.dumps(messages)
                for private in (str(root), "Synthetic harmless", "synthetic-private-prompt", "synthetic-token"):
                    assert private not in wire
                if state.flow_project is not None:
                    assert state.flow_project.project_id not in wire
                checks.append("frozen " + mode + " code, privacy and zero submissions")

            for mode in ("crash", "malformed"):
                run = root / ("no run " + mode)
                terminal, messages = invoke(mode, run)
                assert (terminal["outcome"], terminal["exit_code"], terminal["code"]) == ("communication_lost", 2, "WORKER_LOST")
                assert not run.exists() and "synthetic-private" not in json.dumps(messages)
                checks.append("frozen " + mode + " retains communication-loss handling")
            run = root / "no run after stop"
            terminal, _ = invoke("setup_editor", run, stop=True)
            assert (terminal["outcome"], terminal["exit_code"], terminal["code"]) == ("interrupted", 130, None)
            assert not run.exists()
            checks.append("frozen pre-job Stop remains 130")
            assert not (root / "synthetic data").exists()
            assert {path.name: path.read_bytes() for path in (prompts, config)} == original
            checks.append("no implicit data preparation or input writes")
    result = {"checks": checks, "passed": len(checks), "worker_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
    args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "failed": 0}))


if __name__ == "__main__":
    main()
