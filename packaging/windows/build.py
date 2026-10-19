"""Explicit local-only internal build. Never installs packages or publishes artifacts."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build_location(value):
    path = Path(value).resolve()
    relative = path.relative_to(ROOT)
    if not relative.parts or relative.parts[0] not in {"build", "dist", ".tmp"}:
        raise ValueError("Build locations must be inside ignored build/dist/.tmp directories.")
    return path


def verify_environment():
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"} or sys.version_info[:3] != (3, 12, 10):
        raise RuntimeError("This prototype build is pinned to Windows x64 Python 3.12.10.")
    if Path(sys.prefix).resolve() == (ROOT / ".venv").resolve():
        raise RuntimeError("Use a separate build environment, never the working .venv.")
    versions = {}
    for line in (HERE / "requirements-build.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split()[0].split("==")
        if metadata.version(name) != version:
            raise RuntimeError("Build environment does not match the lock.")
        versions[name] = version
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    return versions


def collect_notices(destination, versions):
    destination.mkdir(parents=True, exist_ok=False)
    for name in versions:
        distribution = metadata.distribution(name)
        for entry in distribution.files or ():
            if not any(word in entry.name.lower() for word in ("license", "notice", "copying")):
                continue
            source = Path(distribution.locate_file(entry)).resolve()
            if not source.is_file():
                continue
            relative = Path(*[part for part in entry.parts if part not in {".", ".."}])
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    base = Path(sys.base_prefix)
    tcl_license = base / "tcl/tcl8.6/license.terms"
    if not tcl_license.is_file():
        import tkinter
        if tkinter.Tcl().call("info", "patchlevel") != "8.6.15":
            raise RuntimeError("Supply the verified license for the installed Tcl patch level.")
        tcl_license = HERE / "licenses/Tcl-8.6.15-license.terms"
        if sha256(tcl_license) != "c0a69a2bfd757361ec7e6143973b103c90409316b49e9c88db26ad6388e79f16":
            raise RuntimeError("Vendored Tcl notice integrity check failed.")
    required = [(base / "LICENSE.txt", "Python-LICENSE.txt"),
                (tcl_license, "Tcl-license.terms"),
                (base / "tcl/tk8.6/license.terms", "Tk-license.terms")]
    for source, name in required:
        if not source.is_file():
            raise RuntimeError("Required Python/Tcl/Tk license resource is unavailable.")
        shutil.copyfile(source, destination / name)


def source_snapshot():
    paths = [ROOT / "app.py", ROOT / "gui.py", ROOT / "requirements.txt", ROOT / "LICENSE", ROOT / "README.md"]
    paths += sorted((ROOT / "nano_batch").rglob("*.py"))
    paths += sorted(HERE.glob("*.py")) + sorted(HERE.glob("*.spec")) + [HERE / "requirements-build.lock"]
    paths += sorted((HERE / "licenses").glob("*"))
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist/stage5b")
    parser.add_argument("--work", default="build/stage5b")
    args = parser.parse_args()
    dist, work = build_location(args.dist), build_location(args.work)
    bundle = dist / "NanoBananaBatch"
    if bundle.exists():
        raise RuntimeError("Choose a fresh output directory; existing builds are never overwritten.")
    versions = verify_environment()
    work.mkdir(parents=True, exist_ok=True)
    notice_dir = work / "notices"
    collect_notices(notice_dir, versions)
    snapshot = source_snapshot()
    info = {"internal_test_build": True, "schema": 1,
            "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "working_tree_modified": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
            "python": platform.python_version(), "platform": "windows-x64", "dependencies": versions,
            "python_runtime_sha256": sha256(Path(sys.base_prefix) / "python312.dll"),
            "source_sha256": snapshot}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
                    str(HERE / "NanoBananaBatch.spec")], cwd=ROOT, check=True)
    if source_snapshot() != snapshot:
        raise RuntimeError("Source changed while building; discard this unverified build.")
    for name in ("LICENSE", "README.md"):
        shutil.copyfile(ROOT / name, bundle / name)
    shutil.copytree(notice_dir, bundle / "THIRD_PARTY_NOTICES")
    (bundle / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (bundle / "INTERNAL_TEST_BUILD.txt").write_text(
        "INTERNAL TEST BUILD. Not a public binary release.\nExternal installed Chrome required.\n"
        "No live Flow acceptance of this packaged build is claimed.\n", encoding="utf-8")
    subprocess.run([sys.executable, str(HERE / "audit_bundle.py"), str(bundle)], check=True)
    print("Internal one-folder build verified:", bundle)


if __name__ == "__main__":
    main()
