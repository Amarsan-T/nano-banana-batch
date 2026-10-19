"""Scan build artifacts and write their checksum inventory; withhold matched content."""

import argparse
import hashlib
import json
import marshal
import ntpath
from pathlib import Path
import re
import types
import zipfile

PATTERNS = (
    re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+", re.I),
    re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(rb"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    re.compile(rb"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
)


def check_content(data):
    # CPython embeds this exact public documentation example in ntpath and in
    # python312.dll. It is upstream text, not a local user's filesystem path.
    data = data.replace(ntpath.splitroot.__doc__.encode("utf-8"), b"public-stdlib-documentation")
    if any(pattern.search(data) for pattern in PATTERNS):
        raise ValueError("Artifact content requires private-data review (match withheld).")


def check_code(code):
    if not isinstance(code, types.CodeType):
        return
    check_content(code.co_filename.encode("utf-8"))
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            check_code(value)
        elif isinstance(value, str):
            check_content(value.encode("utf-8"))
        elif isinstance(value, bytes):
            check_content(value)


def audit(bundle):
    from PyInstaller.archive.readers import CArchiveReader
    bundle = Path(bundle).resolve(strict=True)
    manifest = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Redirected artifact path.")
        relative = path.relative_to(bundle).as_posix()
        parts = {part.lower() for part in Path(relative).parts}
        if parts & {"runs", ".flow_chrome_profile", "chrome-profile", ".git", ".venv", ".env"} or path.name.lower() in {"run_state.json", "cookies", "login data", "config.json"}:
            raise ValueError("Runtime/private artifact is prohibited.")
        if not path.is_file() or path.name == "ARTIFACT_SHA256.json":
            continue
        data = path.read_bytes()
        check_content(data)
        manifest[relative] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if path.name == "base_library.zip":
            with zipfile.ZipFile(path) as archive:
                for entry in archive.namelist():
                    if entry.endswith(".pyc"):
                        check_code(marshal.loads(archive.read(entry)[16:]))
    for name in ("NanoBananaBatch.exe", "NanoBananaBatchWorker.exe", "NanoBananaBatchCLI.exe"):
        archive = CArchiveReader(str(bundle / name))
        for entry, facts in archive.toc.items():
            kind = facts[-1]
            if kind == "s":
                check_code(marshal.loads(archive.extract(entry)))
            elif kind == "z":
                pyz = archive.open_embedded_archive(entry)
                for module in pyz.toc:
                    if module == "tests" or module.startswith("tests.") or module.startswith("frozen_fixture"):
                        raise ValueError("Test-only module found in application artifact.")
                    check_code(pyz.extract(module))
    required = ("_internal/playwright/driver/node.exe", "_internal/playwright/driver/package/cli.js",
                "_internal/_tcl_data/init.tcl", "_internal/_tk_data/tk.tcl",
                "THIRD_PARTY_NOTICES/Python-LICENSE.txt", "LICENSE", "BUILD_INFO.json", "INTERNAL_TEST_BUILD.txt")
    if not all((bundle / path).is_file() for path in required):
        raise ValueError("Required bundle resource is missing.")
    if any(".local-browsers" in name for name in manifest):
        raise ValueError("Browser binaries must not be bundled.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    manifest = audit(args.bundle)
    # Inventory is a build artifact; scanning itself never changes runtime data.
    (args.bundle / "ARTIFACT_SHA256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(manifest), "bytes": sum(v["bytes"] for v in manifest.values()), "privacy_scan": "passed"}))


if __name__ == "__main__":
    main()
