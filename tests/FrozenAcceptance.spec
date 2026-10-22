# TEST-ONLY bundle; never collect it into the application output directory.
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / "tests/frozen_worker_entry.py")], pathex=[str(root), str(root / "tests")],
             hiddenimports=["app", "nano_batch.gui_worker", "playwright.sync_api"],
             excludes=["pytest"], binaries=[], datas=[], optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NanoBananaBatchWorker",
          console=True, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FrozenAcceptance")
