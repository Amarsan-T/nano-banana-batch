"""Explicit frozen companion actions. No browser automation or protocol changes."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from queue import Queue

from .runtime_paths import sibling_executable


class CompanionAction:
    """Run one fixed CLI action off the Tk thread; never terminate a child."""

    def __init__(self):
        self.active = False  # Written/read only by the GUI owner thread.
        self._result = Queue(maxsize=1)
        self._thread = None

    def start(self, action: str, path) -> None:
        if self.active or action not in {"chrome", "output"}:
            raise ValueError("Companion action unavailable.")
        cli = sibling_executable(sys.executable, "NanoBananaBatchCLI.exe")
        arguments = (
            ["open-flow-debug", "--config", str(path), "--url", "https://flow.google.com/"]
            if action == "chrome" else ["--open-output-folder", str(path)]
        )
        self.active = True

        def run():
            success = False
            try:
                flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                result = subprocess.run(
                    [str(cli), *arguments], cwd=str(cli.parent), shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                success = result.returncode == 0
            except Exception:
                pass  # Never relay command lines, private paths or raw errors.
            self._result.put(success)

        self._thread = threading.Thread(target=run, name="packaged-companion", daemon=False)
        try:
            self._thread.start()
        except Exception:
            self.active = False
            raise

    def drain(self):
        if self._result.empty():
            return None
        result = self._result.get_nowait()
        self.active = False
        return result

    def wait(self):
        if self._thread is not None:
            self._thread.join()
