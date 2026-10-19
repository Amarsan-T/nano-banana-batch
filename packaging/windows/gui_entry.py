"""INTERNAL TEST BUILD: GUI entry, never a backend dispatcher."""


def main():
    try:
        import gui
        result = gui.main()
    except Exception:
        result = 2
    if result == 2:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, "Nano Banana Batch could not start. Check the complete internal build folder and writable application-data location.",
            "Nano Banana Batch - INTERNAL TEST BUILD", 0x10,
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
