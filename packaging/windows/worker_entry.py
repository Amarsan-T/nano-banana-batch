"""Console bootloader required: the backend uses private inherited pipe handles."""

import sys


def main():
    if sys.stdin is None or sys.stdout is None or sys.stdin.isatty() or sys.stdout.isatty():
        return 2
    try:
        from nano_batch.gui_worker import main as worker_main
        return worker_main()
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
