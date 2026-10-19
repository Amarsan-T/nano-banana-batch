# Internal Windows one-folder prototype — Stage 5B

This is an **INTERNAL TEST BUILD**, not a public executable release, installer or
update mechanism. Do not upload the generated folder or archives. Earlier
user-operated source-GUI acceptance is separate evidence: the developer reported
successful sequential generation/download, skip-only Resume, cooperative Stop
(130), download-only Resume, and English/Russian shortcuts. No real Flow test of
the frozen application has been performed by Codex.

## Build environment

Build on Windows x64 with Python **3.12.10**, including Tcl/Tk 8.6.15. Keep the
working `.venv` unchanged. The reviewed lock contains twelve exact distributions
and wheel SHA-256 hashes: PyInstaller 6.22.3, hooks-contrib 2026.8, Playwright 1.64.0,
Pillow 12.3.0 and their build/runtime dependencies. This is one tested build set;
the application's `playwright>=1.60,<2` dependency contract remains unchanged.

From the source repository in PowerShell:

```powershell
py -3.12 -m venv .venv-build-stage5b
.\.venv-build-stage5b\Scripts\python.exe -m pip install --require-hashes -r packaging\windows\requirements-build.lock
.\.venv-build-stage5b\Scripts\python.exe -B packaging\windows\build.py
```

The build refuses the working `.venv`, incorrect versions and existing output.
For another build, supply fresh ignored locations, for example
`--dist dist/stage5b-review --work build/stage5b-review`. No Playwright browser
download is required. The build does not install dependencies or publish anything.

## Shared one-folder layout

```text
NanoBananaBatch/
  NanoBananaBatch.exe          windowed Tkinter GUI
  NanoBananaBatchWorker.exe    private console-mode backend
  NanoBananaBatchCLI.exe       existing CLI and explicit Chrome bootstrap
  _internal/                  Python, Tcl/Tk, Pillow, Playwright Node/driver
  THIRD_PARTY_NOTICES/
  LICENSE
  README.md
  INTERNAL_TEST_BUILD.txt
  BUILD_INFO.json
  ARTIFACT_SHA256.json
```

One PyInstaller Analysis/PYZ supplies all three entry points and shared resources.
The frozen GUI resolves the exact sibling worker executable; it never invokes
itself with `-m`. Source mode still invokes Python with the existing module entry
point. Worker stdin/stdout remain private bounded protocol pipes, stderr stays
isolated, and Windows supervision hides its console. There is no frozen-only
generation, download, ownership, retry or cancellation implementation.

## First internal launch

1. Keep the entire folder together. Double-click `NanoBananaBatch.exe`.
   The title identifies the internal build. Startup and Preview are read-only.
2. Click **Prepare data folders** and confirm only if you want new independent
   defaults. It creates `%LOCALAPPDATA%\NanoBananaBatch\config.json` and `runs`.
   Existing configuration is never replaced. No profile or run is imported.
3. Select a UTF-8 prompt file and a prospective run folder, or explicitly reopen
   an existing run with its original prompt file. Preview does not create the
   selected run or repair saved state.
4. Live execution requires a separate user-operated acceptance decision. It can
   consume credits. This build procedure does not authorize or perform it.

`NANO_BANANA_BATCH_DATA_DIR` may explicitly select another absolute writable data
directory **before** launching. There is no automatic migration. Frozen defaults
for configuration, profile, diagnostics and CLI-created runs live there, not in
the executable directory. Explicit GUI run selections stay exactly where chosen.
Relative paths inside a frozen configuration resolve against that configuration's
directory; explicit CLI relative path arguments resolve against the caller's
working directory. Source-mode relative-path behavior is unchanged.

**Launch dedicated Chrome** is a separately confirmed frozen-GUI action through
`NanoBananaBatchCLI.exe open-flow-debug`, using the existing loopback-only
`127.0.0.1:9222` bootstrap at canonical Flow home. Installed Chrome remains external.
Manual login, model selection and image settings remain the user's responsibility.
Existing debug Chrome is reused with its existing flags/profile. The new defaults
do not silently import an authenticated profile. Chrome must stay maximized
(it may remain behind other windows); minimized execution is unsupported.

The CLI companion temporarily restores Windows external-program DLL search
settings and excludes bundled PATH entries only around Chrome/output-folder
launch. It restores them afterward. The GUI and worker do not change their DLL
environment. Companion stdout/stderr are discarded; the GUI receives only success
or failure. Closing waits for the action to exit rather than terminating it.

## Offline packaged acceptance

The production worker has no fake-adapter selector. A **separate test-only build**
freezes the existing worker/runner with synthetic fixtures:

```powershell
.\.venv-build-stage5b\Scripts\python.exe -m PyInstaller --clean --noconfirm --distpath dist/stage5b-tests --workpath build/stage5b-tests tests/FrozenAcceptance.spec
.\.venv-build-stage5b\Scripts\python.exe -B tests\verify_frozen_bundle.py --help
```

Pass the actual application folder, separate synthetic worker folder and ignored
result-file path using the displayed arguments. Do not run this acceptance script
concurrently with lock-owning regression suites: both intentionally use the same
Windows execution mutex. It creates only disposable synthetic data under `.tmp`.
Its native ACL probe changes permissions only on a confined disposable copy of
the build and restores them before removal.

Checks include actual windowed startup/close, frozen worker framing and read-only
Preview, protocol rejection, synthetic completion/Resume, delayed cooperative Stop
and code 130, locks, crashes, malformed output, Unicode/spaces, a genuinely
read-only installation and driver/Tcl/Pillow resources. Child probes remove
Python/Node from PATH. Existing pre-job protocol failure reports
`interrupted`, exit 130 and `PROTOCOL_ERROR`; it never authorizes execution.
The driver probe starts/stops bundled Playwright without a browser or endpoint.
Test-only modules must never enter the application build.

## Inventory, privacy and notices

The build records Python/dependency versions, the base Git commit, whether the
working tree was modified, relative source hashes and the Python runtime hash.
It does not pretend an uncommitted build is a clean release. The checksum manifest
lists every artifact except itself; it is integrity evidence, not a signature.

The scanner reviews files, executable archives, embedded Python constants and
code filenames. It rejects runtime/private directories, secret patterns, local
home paths, bundled browsers and test modules. One exact upstream CPython
`ntpath.splitroot` documentation string is exempted because its public example is
embedded in Python's DLL and stdlib; arbitrary home paths remain rejected. This
pattern scan supplements an allowlisted build, rather than proving the absence
of every conceivable secret.

Project MIT licensing remains separate from dependency licenses. Python, Tcl/Tk,
Playwright/Node, Pillow and dependency notices are retained, including build-tool
notices conservatively. The Python installation omitted Tcl's license file, so
the unmodified Tcl 8.6.15 notice is retained from the
[official Tcl source](https://github.com/tcltk/tcl/blob/core-8-6-15/license.terms),
with SHA-256 `c0a69a2bfd757361ec7e6143973b103c90409316b49e9c88db26ad6388e79f16`.
No exclusive historical source authorship or legal-compliance guarantee is added.

## Remaining gates

The prototype is unsigned. SmartScreen/Defender behavior, a clean Windows machine
without installed Python, and user-operated packaged Chrome/Flow acceptance are
not verified. Removing Python from PATH is not a clean-machine test. Windows path
length limits can still affect deeply nested third-party resource paths; use a
short installation directory. No installer, update, uninstall or profile migration
is implemented. Failed/interrupted first setup is not silently repaired.

Existing single-writer assumptions, filesystem races, storage-provider/power-loss
limits and conservative ownership rules still apply. A blocking SDK operation may
delay Stop; no forced termination is introduced. An acknowledged generation
without durable candidate ownership remains blocked. Passing synthetic packaged
tests does not establish real Flow acceptance or reliability at arbitrary scale.
