"""TEST-ONLY frozen backend. Never included in the application spec or release."""

import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager


def offline_guard(event, args):
    if event == "socket.connect":
        frame = sys._getframe(1)
        while frame:
            if frame.f_code.co_name in {"socketpair", "_fallback_socketpair"} and Path(frame.f_code.co_filename).name == "socket.py":
                return
            frame = frame.f_back
        raise AssertionError("Test backend forbids endpoint connections.")
    if event == "subprocess.Popen" and Path(str(args[0])).name.lower() in {"chrome.exe", "msedge.exe", "firefox.exe", "chromium.exe"}:
        raise AssertionError("Test backend forbids browser launch.")


sys.addaudithook(offline_guard)


class SyntheticAdapter:
    @contextmanager
    def generation_completion_checkpoint(self, callback):
        self.checkpoint = callback
        try:
            yield
        finally:
            self.checkpoint = None

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        from nano_batch.models import DiscoveredCandidate, GenerationResult
        mode = os.environ.get("NBB_FROZEN_TEST_MODE", "normal")
        if mode in {"forbid", "download_only"}:
            raise AssertionError("Unexpected duplicate generation.")
        result = GenerationResult(candidates=(DiscoveredCandidate(
            "rendered-image", candidate_id=f"00000000-0000-4000-8000-{item.order:012x}",
        ),))
        self.checkpoint(result)
        if mode == "checkpoint_wait":
            root = Path(os.environ["NBB_FROZEN_TEST_ROOT"])
            (root / "checkpoint-ready").write_text("synthetic")
            deadline = time.monotonic() + 60
            while not (root / "release").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("Synthetic gate timed out.")
                threading.Event().wait(0.01)
        return result

    def download_candidate(self, *, candidate, destination):
        if os.environ.get("NBB_FROZEN_TEST_MODE") == "forbid":
            raise AssertionError("Unexpected duplicate download.")
        from PIL import Image
        from nano_batch.models import BrowserDownloadResult
        Image.new("RGB", (16, 9), (10, 20, 30)).save(destination, format="JPEG")
        return BrowserDownloadResult(candidate_id=candidate.candidate_id, staged_path=str(destination),
                                     browser_suggested_filename="synthetic.jpeg", browser_download_guid="synthetic-guid",
                                     mechanism="cdp_download_events")


def main():
    if sys.argv[1:] == ["--driver-probe"]:
        import tkinter
        from PIL import Image
        from playwright.sync_api import sync_playwright
        image = io.BytesIO()
        Image.new("RGB", (16, 9)).save(image, format="JPEG")
        image.seek(0)
        with Image.open(image) as decoded:
            decoded.load()
            assert decoded.format == "JPEG" and decoded.size == (16, 9)
        tcl = tkinter.Tcl().call("info", "patchlevel")
        driver = sync_playwright().start()
        driver.stop()
        driver.stop()
        print(json.dumps({"driver_start_stop": True, "pillow": True, "tcl": tcl}))
        return 0
    mode = os.environ.get("NBB_FROZEN_TEST_MODE", "normal")
    if mode == "malformed":
        os.write(1, b"synthetic-private-not-a-frame\n")
        return 0
    if mode == "crash":
        os.write(1, b'{"v":1,"type":"ready"}\n')
        return 9
    import nano_batch.gui_worker as worker
    if mode in {"setup_project", "setup_driver", "setup_reconnect", "setup_editor", "setup_settings", "setup_cleanup"}:
        from setup_diagnostics_fixtures import SyntheticSetupSession
        adapter_factory = lambda *_args: SyntheticSetupSession(
            Path(os.environ["NBB_FROZEN_TEST_ROOT"]) / "synthetic-profile", mode.removeprefix("setup_"),
        )
    else:
        adapter_factory = lambda *_args: SyntheticAdapter()
    original = worker.serve
    worker.serve = lambda source, write: original(source, write, adapter_factory=adapter_factory)
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
