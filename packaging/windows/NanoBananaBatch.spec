# One analysis/PYZ/resource set; only the final entry script and console mode differ.
from pathlib import Path

root = Path(SPECPATH).parents[1]
entry_dir = root / "packaging" / "windows"
a = Analysis([str(entry_dir / "gui_entry.py")], pathex=[str(root)],
             binaries=[], datas=[],
             hiddenimports=["app", "nano_batch.gui_worker", "nano_batch.packaged_cli", "playwright.sync_api"],
             excludes=["tests", "pytest"], noarchive=False, optimize=0)
assert a.scripts[-1][0] == "gui_entry"
assert not any(".local-browsers" in str(row) for row in a.datas + a.binaries)
pyz = PYZ(a.pure)
executables = []
for name, entry, console in (("NanoBananaBatch", "gui_entry", False),
                              ("NanoBananaBatchWorker", "worker_entry", True),
                              ("NanoBananaBatchCLI", "cli_entry", True)):
    scripts = [*a.scripts[:-1], (entry, str(entry_dir / (entry + ".py")), "PYSOURCE")]
    executables.append(EXE(pyz, scripts, [], exclude_binaries=True, name=name,
                           console=console, debug=False, strip=False, upx=False,
                           disable_windowed_traceback=True))
coll = COLLECT(*executables, a.binaries, a.datas, strip=False, upx=False, name="NanoBananaBatch")
