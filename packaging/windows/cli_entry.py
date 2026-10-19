"""Internal console companion. Normal CLI commands remain application-owned."""

from nano_batch.packaged_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
