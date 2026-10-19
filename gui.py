"""Explicit Windows GUI entry point. The CLI launcher is unchanged."""

import sys


def main() -> int:
    try:
        from nano_batch.preview_gui import main as gui_main
    except ImportError:
        if sys.stderr is not None:
            print("GUI requires Python with Tkinter/Tcl-Tk installed.", file=sys.stderr)
        return 2
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
