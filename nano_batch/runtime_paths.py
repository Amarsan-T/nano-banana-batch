"""Source/frozen launch paths. Never creates or migrates runtime data implicitly."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_PATH = object()


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_home() -> Path:
    override = os.environ.get("NANO_BANANA_BATCH_DATA_DIR")
    value = override if override is not None else os.environ.get("LOCALAPPDATA")
    if not value or "\0" in value:
        raise ValueError("A writable application-data location is required.")
    path = Path(value)
    if not path.is_absolute() or path.parent == path:
        raise ValueError("Application-data location must be an absolute directory.")
    return path if override is not None else path / "NanoBananaBatch"


def config_path(*, gui: bool = False) -> Path:
    if frozen():
        return data_home() / "config.json"
    return source_root() / "config.json" if gui else Path("config.json")


def runs_directory() -> Path:
    return data_home() / "runs" if frozen() else Path("runs")


def launcher_directory(source_directory: Path | None = None) -> Path:
    return data_home() if frozen() else (source_directory if source_directory is not None else source_root())


def runtime_path(value, *, source_default: str, packaged_default: str,
                 config_file=None, from_argument: bool = False) -> Path:
    if value is DEFAULT_PATH:
        return data_home() / packaged_default if frozen() else Path(source_default)
    path = Path(value)
    if not frozen():
        return path
    if path.is_absolute():
        return path
    base = Path.cwd() if from_argument else (
        Path(config_file).absolute().parent if config_file else data_home()
    )
    return base / path


def sibling_executable(executable: Path, name: str) -> Path:
    root = Path(executable).resolve(strict=True).parent
    candidate = root / name
    if not candidate.is_file() or candidate.is_symlink() or candidate.resolve().parent != root:
        raise ValueError("Packaged application component is unavailable.")
    return candidate


def worker_launch(executable: Path) -> tuple[list[str], Path]:
    executable = Path(executable)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("An explicit executable is required.")
    if frozen():
        worker = sibling_executable(executable, "NanoBananaBatchWorker.exe")
        return [str(worker)], worker.parent
    return [str(executable), "-B", "-u", "-m", "nano_batch.gui_worker"], source_root()


def prepare_data_home() -> Path:
    """Explicit first-run setup only; existing configuration is never replaced."""
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "runs").mkdir(exist_ok=True)
    config = {
        "flow_url": "https://flow.google.com/",
        "browser_profile_dir": str(home / "chrome-profile"),
        "debug_address": "127.0.0.1", "debug_port": 9222,
        "diagnostics_path": str(home / "diagnostics" / "flow_inspect.png"),
        "prestart_timing_debug": False,
        "generation": {"mode": "image", "aspect_ratio": "16:9", "variants": 1,
                       "result_timeout_seconds": 300},
    }
    try:
        with (home / "config.json").open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    except FileExistsError:
        pass
    return home
