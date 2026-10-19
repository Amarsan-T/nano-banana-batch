"""Frozen CLI wrapper: keep external-program DLL settings out of the GUI/worker."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def external_program_environment():
    """Only the single-purpose CLI launch process changes its DLL search path."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = (ctypes.c_uint32, ctypes.c_wchar_p)
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = (ctypes.c_wchar_p,)
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    length = kernel.GetDllDirectoryW(0, None)
    buffer = ctypes.create_unicode_buffer(length + 1)
    if length and not kernel.GetDllDirectoryW(len(buffer), buffer):
        raise OSError("External-program environment unavailable.")
    previous_dll = buffer.value if length else None
    previous_path = os.environ.get("PATH")
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    def bundled(value):
        try:
            Path(value.strip('"')).resolve().relative_to(bundle)
            return True
        except (ValueError, OSError):
            return False
    if not kernel.SetDllDirectoryW(None):
        raise OSError("External-program environment unavailable.")
    try:
        if previous_path is not None:
            os.environ["PATH"] = os.pathsep.join(value for value in previous_path.split(os.pathsep) if value and not bundled(value))
        yield
    finally:
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path
        if not kernel.SetDllDirectoryW(previous_dll):
            raise OSError("External-program environment restoration failed.")


def main() -> int:
    try:
        if sys.argv[1:2] == ["--open-output-folder"]:
            if len(sys.argv) != 3:
                return 2
            from .gui_execution import output_folder
            folder = output_folder(sys.argv[2])
            with external_program_environment():
                os.startfile(str(folder))
            return 0
        import app
        if sys.argv[1:2] in (["open-flow"], ["open-flow-debug"]):
            with external_program_environment():
                return app.main()
        return app.main()
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Windowless companion actions never show a private traceback.
        if sys.stderr is not None:
            print("Packaged command failed. Check configuration and component availability.", file=sys.stderr)
        return 2
