# Nano Banana Batch v0.1

A deliberately small standalone experiment for obtaining organized image candidates from Google Flow / Nano Banana.

Nano Banana Batch is an independent, unofficial project and is not affiliated with
Google. This repository does not establish trademark authorization or compliance
with Google Flow's terms. The project is licensed under the [MIT License](LICENSE).

It accepts the same basic `batch.json` shape exported by YouTube Factory, preserves each `visual_id`, and is intentionally separated from the editing pipeline.

See the [development history](docs/DEVELOPMENT_HISTORY.md) for project origins,
milestones, and provenance limitations.

The [six-issue reliability audit](docs/RELIABILITY_AUDIT.md) records verified
recovery boundaries, recent hardening and remaining acceptance limitations.

## Public repository and private local data

This public copy contains source, offline tests, and reviewed example inputs.
Historical browser profiles, runs, assets, diagnostics, and private configuration
are not distributed. Documentation run paths are illustrative; replace them with
your own run directory. UUIDs in offline tests are synthetic and do not identify
live Flow projects or candidates. Dummy emails and tokens test privacy filtering.

Keep your dedicated Chrome profile, `config.json`, personal prompt files, and
runtime output private. `.gitignore` excludes those files and their common backups,
but cannot remove private content embedded in source, tests, or documentation.
Review new fixtures and `git status --short --untracked-files=all` before staging.
Run `python -m unittest discover -s tests -p test_public_repository_security.py -v`
for the offline publication checks. These checks do not access Chrome or Flow and
are a focused safeguard, not a guarantee that every possible secret is detected.

## What exists now

- Parses and validates batch JSON.
- Accepts `generation_prompt` (YouTube Factory) or `prompt`.
- Enforces unique contiguous `order` and unique `visual_id`.
- Computes a deterministic semantic batch ID.
- Creates resumable local run-state skeletons.
- Uses atomic JSON state writes.
- Provides a zero-browser dry run.
- Generates deterministic future filenames such as `0001__scene_0001_visual_001__v01.png`.
- Opens normal installed Google Chrome with a dedicated profile for fully manual authentication.
- Launches normal headed Chrome with a loopback-only CDP endpoint for automation.
- Attaches Playwright to that exact existing Chrome instance and exact configured Flow project tab.
- Reports sanitized, visible Flow DOM metadata without submitting a prompt.
- Reports a read-only `mode_detection` classification with explicit or inferred DOM evidence.
- Previews one selected item without browser actions, state writes, generation, or credit use; existing state may be read.
- Runs one explicitly requested prompt through a conservative Flow adapter.
- Persists `pending → running → completed/failed`, attempt counts, candidate signals, and deterministic suggested filenames.
- Protects submitted/ambiguous failed generations with explicit Retry, with only the bounded failure-specific automatic recoveries described below.
- Previews or downloads candidates for one already-completed item without regenerating it.
- Uses each persisted Flow candidate UUID to select exactly one image tile, its first-level Download submenu parent, and the exact enabled `1K Original size` action.
- Validates PNG/JPEG/WebP files with Pillow and persists hashes, dimensions, MIME, format, paths, timestamps, and independent download status.
- Resumes missing/failed candidates while preserving already-valid downloads.
- Provides a generic sequential batch runner that delegates every item to the proven one-order generation and download paths.
- Accepts one direct prompt or an ordered UTF-8 prompt file through the same batch runner, with one image per prompt.
- Preserves the historical foreground, covered/unfocused, and minimized acceptance evidence while active production now normalizes the dedicated Chrome window to a verified maximized desktop layout; it may remain covered behind other applications.
- Contains offline unit tests.

## Deliberate limits

The underlying real paths each handle exactly one prompt or one order's persisted candidates. `run-batch` composes those primitives sequentially for every item in any validated non-empty batch. External batches support x1/x2; the historical 51-order acceptance used x2. `run-prompt` and `run-prompts-file` request x1. There is no automatic login, CAPTCHA/security bypass, candidate judging, web search, concurrency, multi-account support, hardcoded 51-item special case, or video generation.

The adapter uses accessible roles, labels, visible control text, rendered image elements, download controls, progress semantics, and terminal error messages. The user manually selects any valid Flow image model. The adapter never opens, changes, or enforces the model selector; a safely detected model name is informational and may be unavailable. `preferred_model_hint`, including the prompt wrappers' historical `Nano Banana 2` value, remains semantic Batch ID metadata and is not a model requirement. Image-mode detection uses current selected-mode and composer/aspect/control evidence; selected video signals, ambiguous ownership, and insufficient readiness fail closed. Configuration saying `mode=image` is not sufficient DOM evidence. Aspect ratio and variant count are checked read-only before Start.

## Installation and offline quick start — Windows PowerShell

The supported workflow targets Windows with installed Google Chrome, manual login,
and a dedicated persistent profile. Active batches require a maximized Chrome
window, which may remain behind other applications. Linux/macOS operation and
other Python versions have not been validated for this public release.

Use Python 3.12 for the documented environment. The Stage 3 offline verification
uses Python 3.12.14, Pillow 12.3.0, and Node.js 24.20.0. Playwright is mocked in the
offline suite and is not installed in that audit runtime; this is not evidence of
live Chrome/Flow compatibility. The declared minimum is Playwright 1.60 because
both CDP attachment paths use `is_local` (added in 1.58) and `no_defaults` (added
in 1.60), as documented in the
[Playwright API reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp).
No claim is made that the entire allowed dependency range has been tested.
An earlier reliability audit checked both attachment calls and 212 public API call
shapes against the official Playwright 1.60.0 Python source. This establishes API
availability, not installed-package, driver or live-Flow acceptance at the minimum.
Subsequent isolated Windows verification installed exactly Playwright 1.60.0 with
Python 3.12.10. Installed sync/async signatures, intercepted argument marshalling,
event handling, cancellation cleanup internals, and browser-free driver start/stop
passed verification, along with all 1,642 offline tests; see the
[engineering log](docs/ENGINEERING_LOG.md#playwright-1600-isolated-compatibility-verification-2026-10-19)
for test results and limits. No Chrome/CDP connection or live Flow acceptance was
performed at 1.60.0. The existing development environment was left unchanged.
The developer reports real acceptance with Python 3.12.10 and Playwright 1.64.0;
Codex did not inspect that live test session. A clean-Windows installation and a
browser version matrix have not been reproduced. The API floor remains `>=1.60,<2`.

Install Python 3.12 and Node.js before following these commands. Node.js must be on
`PATH` to run the embedded JavaScript observer regressions; those tests skip if
Node.js is absent. It is a test prerequisite, not an application runtime dependency.

```powershell
cd "C:\path\to\nano_banana_batch_public"

py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.\config.json')) {
    Copy-Item config.example.json config.json
}
node --version

& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
& '.\.venv\Scripts\python.exe' app.py inspect data\sample_batch.json
& '.\.venv\Scripts\python.exe' app.py run data\sample_batch.json --dry-run
& '.\.venv\Scripts\python.exe' app.py generate-one data\sample_batch.json --order 1 --preview
& '.\.venv\Scripts\python.exe' app.py run-batch data\sample_batch.json --preview
& '.\.venv\Scripts\python.exe' app.py run-prompt "A cinematic photograph of a futuristic city at night" --preview
```

These examples use synthetic inputs and do not launch Chrome or spend credits.
`run --dry-run` does not create completed generation state. `download-one --preview`
requires an existing matching run with completed generation and persisted
candidates; it is therefore shown separately below rather than as a fresh-checkout
command. Previews can read and validate existing local state/assets without writes.

You may activate the environment with `.\.venv\Scripts\Activate.ps1`; subsequent
`python` commands then use it. If activation is restricted by your PowerShell
policy, continue using the explicit executable above. `run.bat` always invokes
`.venv\Scripts\python.exe` directly and fails if that environment has not been
created. Installed Chrome is used over CDP; bundled Playwright Chromium is not the
supported login/browser path.

Offline tests create disposable synthetic states and images. In a restricted
environment whose default Windows temporary directory is not writable, first set
test scratch space inside this checkout:

```powershell
New-Item -ItemType Directory -Path '.\.tmp\offline-tests' -Force | Out-Null
$env:TEMP = (Resolve-Path '.\.tmp\offline-tests').Path
$env:TMP = $env:TEMP
```

## Windows launcher — batch folder names

Run `run.bat`, then choose **2. Start new batch**. Before creating the run, the
launcher asks:

```text
Batch folder name (blank = automatic):
```

Enter a name such as `Example Batch - Hobbies` or `Тестовый батч` to create that exact
folder under `runs/`. Spaces, hyphens, underscores, parentheses, and valid Unicode
are supported. Press Enter for the existing `batch_YYYYMMDD_HHMMSS` naming (with
its existing collision suffixes), or enter `CANCEL` case-insensitively to return
to the menu without creating anything.

Custom names must be one Windows-safe folder component: no paths/traversal,
forbidden characters or controls, reserved device names (including extensions),
trailing periods/spaces, invalid Unicode, or components over 255 UTF-16 units.
Invalid or already-existing names are reprompted; custom names are never silently
changed, suffixed, reused, or overwritten.

Each run still contains `run_state.json` and its deterministic images in `assets/`.
Folder naming does not change the semantic Batch ID, prompts, visual IDs, project,
Retry/Resume behavior, or interrupted-download reconciliation. **Resume current
prompts** discovers valid matching state in both legacy and custom-named folders
and shows their actual names. Existing runs are not renamed or migrated.

## Manual Chrome login bootstrap

```powershell
$repo = (Get-Location).Path
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' "--user-data-dir=$repo\.flow_chrome_profile" 'https://flow.google.com/'
```

Sign in normally in that Chrome window. For commands requiring a configured
project, replace `REPLACE_WITH_PROJECT_ID` in your private `config.json` with your
own project's UUID and confirm that project renders correctly. Do not use an
everyday Chrome profile; `.flow_chrome_profile` is dedicated to this project. The
application never automates Google login.

As a convenience, the following command performs the same normal-Chrome login bootstrap. It does not attach Playwright or automate login:

```powershell
python app.py open-flow
```

After login has been saved, close that bootstrap Chrome instance once so it releases the profile. Then start the attachable debug session:

```powershell
python app.py open-flow-debug
```

This launches installed Chrome with these ordinary arguments:

```text
--user-data-dir=<repo>\.flow_chrome_profile
--remote-debugging-port=9222
--remote-debugging-address=127.0.0.1
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-renderer-backgrounding
--disable-features=CalculateNativeWinOcclusion
https://flow.google.com/
```

Keep this Chrome instance open. Do not close it between `open-flow-debug` and `inspect-flow`. Debugging is bound to IPv4 loopback only; configuration rejects external bind addresses.

Use only a trusted local debugging session and machine. CDP grants control over
the selected authenticated browser; loopback binding does not authenticate local
processes or prove that an arbitrary endpoint belongs to the dedicated profile.
Do not attach to an everyday browser profile. Keep profile data, runtime state,
prompts, assets, and raw diagnostic screenshots private; screenshots may show
prompt/account information even when structured diagnostic text is filtered.

The background switches request disabled timer throttling, occluded-window and
renderer backgrounding, and Windows native-occlusion calculation where Chrome
supports them. The dedicated launcher deliberately omits
`--enable-automation`; loopback CDP remains enabled. Chrome requires that switch for
`Browser.getBrowserCommandLine`, so that method's refusal alone does not prove
missing background flags. On Windows, attachment falls back to the same browser's
browser-target `SystemInfo.getInfo` command-line field and parses it with native
Windows quoting rules. Only the four flag-presence booleans are retained; raw
arguments and unrelated system/GPU metadata are neither printed nor persisted.
Failure diagnostics reuse those booleans without repeating the fallback. The
experimental fallback can be unavailable or delayed while Chrome gathers system
information; it is not part of generation polling. If both methods are unavailable,
status remains explicitly unknown, with one warning under Python's default filter.
An observed missing flag still stops setup. These checks establish startup-switch
presence, not a guarantee about renderer performance or minimized operation.

Use `run.bat` **Launch Chrome**, or `python app.py open-flow-debug`, with the same
dedicated profile. An available endpoint is reused without relaunching Chrome or
applying new launch flags. **No browser restart is needed solely for this verifier
update** if that running browser already has all four switches. If automatic
verification is unavailable, check locally as follows:

1. In the dedicated Chrome, open `chrome://version` manually and compare its
   **Command Line** with the four background flags listed above. Do not share the
   full output: it can contain private paths or URLs. This manual inspection does
   not change the application's automatic `unknown` status.
2. If a switch is missing, pause any batch with Ctrl+C, then exit only the dedicated
   Chrome using its normal UI, including other windows using that same profile.
   Relaunch through **Launch Chrome** or `python app.py open-flow-debug` once that
   process has exited. Reusing an existing process cannot repair its startup flags.
3. Keep `.flow_chrome_profile` intact. Do not delete lock files, clear authentication,
   create a replacement profile, terminate unrelated Chrome processes, or change
   the debugging address. Saved runs still use their existing **Resume** workflow.

Relaunch alone cannot guarantee introspection support. If the warning remains,
retain the unknown status and the manual check; there is no verification override.
On attachment, the adapter enables page-scoped
CDP focus emulation and requests an active page lifecycle for the selected Flow tab;
it does not call `bringToFront` or take Windows foreground focus. Foreground-visible
and covered/unfocused restored operation each passed the
accepted A/B diagnostic. Minimized-to-taskbar operation passed the clean real
historical three-order acceptance: 3/3 orders completed,
six unique candidates, and six validated canonical JPEGs while Chrome remained
minimized after the first Start acknowledgement. During a real Flow batch, a scoped
Windows execution-state request also prevents idle system/display sleep and is cleared
on normal exit, failure, or interruption; it is not fake input and does not itself
guarantee minimized compositor behavior.

For active production, the selected dedicated Chrome window is now checked at
project setup/resume, generation configuration and submission, and candidate
download boundaries. CDP targets only the native window containing the selected
Flow tab and requests restoration before maximization. Restoration may report
`normal` or return directly to the previous `maximized` state; the latter avoids
a redundant maximize request. Both paths still verify `windowState=maximized`,
positive viewport geometry, the exact project, a ready Flow surface and preserved
attempt-observer provenance before continuing an acknowledged generation.
No `bringToFront`, OS input, or explicit focus request is used. Chrome may remain
behind other applications; native Chrome/Windows restoration can still affect
focus, which this application cannot guarantee to prevent. A low-frequency
result-wait watchdog permits one bounded normalization per detected unusable-window
event while continuing the same submitted generation; it never presses Start again
or resets candidate baselines. Native minimized and snapped half-screen layouts are
therefore historical evidence, not the supported active-batch workflow.

Run real Flow production and acceptance commands manually from normal interactive
Windows PowerShell under your normal Windows account. The Codex sandbox remains suitable for code,
offline tests, analysis, and persisted-artifact inspection, but it is not an accepted
execution environment for real Flow downloads unless explicitly re-proven later. A
prior sandbox-only Chrome download permission failure was environmental, not a product
regression.

No stealth plugins, browser-identity overrides, automation-evasion flags, CAPTCHA handling, or weakened browser security are used. The application never reads or prints cookies, tokens, or browser storage.

Successful generation details include a privacy-safe `prestart_timing` record
measured with one monotonic clock. It contains phase offsets, durations, and bounded
poll metadata, never prompt text or arbitrary page content. Candidate and progress
baselines occur before the prompt-fill origin and therefore have negative offsets.
They also include a privacy-safe `post_start_ack_timing` record whose origin is
immediately before Start mouse-down. It records first-signal times, existing-poll
stage durations, the exact acknowledgement signal, and a bounded state-change trace.
Terminal attribution uncertainty uses `ack_signal="failure_attribution_ambiguous"`
and `exact_explicit_failure_detected=false`, without an exact-failure timestamp.
For this terminal outcome, `ack_ms` records when the wait stopped; it does not
prove successful submission acknowledgement. The exception and manual Retry
protection remain authoritative. Diagnostic keys/types and the v1 schema are
unchanged; consumers must allow this additional signal string.
Set top-level `"prestart_timing_debug": true` in `config.json` to print concise
`PRE-START TIMING` and `POST-START ACK TIMING` summaries during an intentional
production diagnostic; the default is `false`, so these timing summaries remain
opt-in.

## Windows GUI

From the public project directory, launch the Tkinter/ttk application with the
existing environment (Python must include Tcl/Tk):

```powershell
.\.venv\Scripts\python.exe gui.py
```

Preview requires no Chrome and consumes no generation credits. **Start / Resume
can consume Flow credits**, including additional submissions from the existing
automatic retry budgets. It requires a fresh Preview and explicit confirmation.
Stage 4D execution has synthetic offline verification only, not live GUI acceptance.

1. Click **Choose file...** and select a UTF-8 prompt text file, one prompt per
   accepted line. For **New run (not created)**, enter a fresh folder beneath an
   existing parent. No folder is created during selection or Preview.
2. To inspect or Resume saved work, click **Open existing run...** and select its
   folder containing `run_state.json`. Keep the same original prompt file.
3. Click **Preview**. The existing planner validates prompts, ownership and assets
   read-only. Review pending, completed, download-only, finalization-pending,
   waiting, blocked and existing automatic-retry eligibility. These are plans.
4. Before real execution, use `run.bat` **Launch Chrome** separately. Log in
   manually in the dedicated profile. Select Image mode, Nano Banana 2.1 (or a
   valid image model), 16:9, x1 and 1K Original size. Keep that Chrome maximized;
   it may remain behind other applications. Native minimized operation is
   unsupported. **Launch Chrome in the GUI is disabled**: the current worker
   protocol has no launch operation, so no shortcut bypasses browser ownership.
5. Click **Start / Resume**, read the credit/retry warning and explicitly confirm.
   The supervisor revalidates the Preview, and the worker repeats validation
   inside the existing run/browser execution locks before calling the runner.
   Changed inputs, configuration, saved state or plan decisions reject the old
   binding. A blocked Order cannot authorize Start; manual `--retry` and policy
   prompt editing remain CLI/launcher operations, never automatic GUI actions.
6. Watch current Order, phase, completed/remaining counts and the progress bar.
   The table retains the labeled Preview snapshot; it is not a live state table.
   Only fully finalized Orders count as completed. Generation saved is distinct
   from download and full completion. 100% does not prove worker exit: terminal
   success is shown only after a valid terminal message, EOF, matching process
   exit code and closed worker handles. Skip-only and download-only Resume use
   the existing paths without authorizing another generation.
7. **Stop safely** requests cooperative cancellation once. The window stays
   responsive and displays Stop pending while an operation/cleanup finishes.
   A blocked SDK call has no finite guaranteed Stop latency. No process tree is
   forcibly terminated. Console Ctrl+C also requests this cleanup and preserves
   exit code 130. Closing an active window offers either keeping it open
   or requesting Stop and closing only after worker exit; it never abandons the
   invocation as normal shutdown.
8. After completion or interruption, use **Existing run** and Preview again before
   another invocation. Inputs and execution controls stay locked until the child
   actually exits, including after communication loss. No automatic restart or
   resubmission occurs. An acknowledged generation without durable ownership
   remains blocked; manual Retry is a new submission, not reconciliation.
9. **Open output folder** explicitly opens the selected saved run's assets folder
   (or run folder if assets do not yet exist). Redirected asset folders are
   rejected. No images or saved state are edited. Safe diagnostics show only
   fixed phases, numeric counts and allowlisted codes; raw CLI/SDK output is not
   forwarded. Use existing CLI diagnostics if further recovery review is needed.

The GUI reads repository-local `config.json` through the same browser-free parsing
helpers as the CLI; absence uses existing defaults. The text workflow always
uses x1. External x2 batch JSON still belongs to the existing CLI and original
batch; the GUI does not guess its identity from a text file. No Flow setting is
changed automatically. The normal CLI and `run.bat` remain available.

The prompt-file and run-folder fields support Ctrl+C/V/X/Z/Y/A (Copy, Paste,
Cut, Undo, Redo, Select all) with standard English and Russian Windows layouts.
Windows virtual-key codes select these actions independently of translated
characters. Bindings belong only to these fields; other controls and file dialogs
retain their normal behavior. Undo/Redo history is local to each window, limited
to 100 edits and 131,072 characters per history stack, and cleared on close.
An edit exceeding that character limit clears history. Typing, pasting and
Undo/Redo invalidate the displayed Preview and require a new Preview before Start.
These shortcuts have synthetic offline coverage; physical-key acceptance under
both layouts still requires user verification. No system-wide key hook is used.

Preview runs on a read-only thread; the table shows at most 100 Orders per page.
Input edits clear authorization immediately, and nominal one-second background
checks rehash prompt/state/configuration files. Slow filesystems can delay change
detection. Assets are revalidated when planning, not continuously monitored.
Confirmation is bound to the displayed plan and private file fingerprints; this
is transient change detection, not a credential, signature or persisted state.
Nonparticipating writers and filesystem races after validation remain outside
these guarantees. Keep the selected run, prompt and configuration files unchanged
during execution; the worker uses the initially validated batch.

### Worker infrastructure and verification limits

Stage 4B user-operated Windows acceptance displayed all 20 previously completed
Orders with zero pending, blocked or download-only items. That is reported
Preview evidence, not an independently repeated inspection or Stage 4D acceptance.

Stage 4D connects the Stage 4C one-job subprocess to the GUI. The supervisor starts
an explicit Python executable with `shell=False` away from Tk callbacks; the
existing runner and all Playwright activity stay on the worker main thread.
Bounded typed messages coalesce progress under backpressure. All widgets stay on
the Tk thread; no human-readable terminal output is parsed for execution state.
No dependency, persisted field, browser poll or generation engine was added.

Windows OS-held mutexes protect the run and a coarse machine-wide dedicated-browser
gate across participating profiles/ports. Competing execution fails closed while
read-only Preview remains available. POSIX fallback is local, per-user advisory
locking, not cross-user/host coordination; it was not verified as Windows GUI
acceptance. Older nonparticipating programs, manual browser actions and filesystem
races are not covered. See [the private worker contract](docs/GUI_WORKER_PROTOCOL.md).

Suggested user-operated acceptance starts with a completed run: Preview and confirm
skip-only Resume, expecting no generation/download. Only with explicit user choice
and awareness of credit cost, use a fresh two-Order batch, fully decode its JPEGs,
then Preview and perform skip-only Resume. Validate cooperative Stop first on
non-submitting work. Do not deliberately interrupt unfinished acknowledged
production generation to prove recovery; retain the existing offline fault tests.
GUI execution, Stop and real Flow behavior still require user-operated acceptance.

## Sequential batch progress

Launcher batches and the sequential `run-batch`, `run-prompt`, and
`run-prompts-file` commands show simple progress milestones on stdout by default.
Existing result lines are retained; stderr keeps existing errors
and warnings. Progress uses stdout because Windows PowerShell 5.1 can convert
informational stderr into a terminating error under strict error handling.
Lines are informational, newline-terminated and flushed;
they require no ANSI support or interactive terminal. Preview remains read-only
and does not start the live progress reporter. Standalone `generate-one` and
`download-one` keep their existing output.

For a two-Order batch, progress includes lines such as:

```text
[1/2] Generating image...
[1/2] Generation saved. Preparing download...
[1/2] Downloading image...
[1/2] Complete - 1/2 (50%)
[2/2] Generating image...
...
[2/2] Complete - 2/2 (100%)
All orders complete: 2/2 (100%)
```

The default shows Order position, phase and completed-Order counts at milestones,
without prompt text. Download-only Resume says generation is already saved;
skip-only Resume reports validated completion without claiming new generation or
downloads. x2 batches show individual candidate downloads while counting the Order
complete only after both are finalized. Waiting and blocked messages preserve the
existing recovery rules; they never authorize a new submission.

For the detailed Stage 2 event lines, select `--progress-format detailed`:

```powershell
python app.py run-prompts-file prompts.txt --run-dir runs/example --progress-format detailed
python app.py launcher --progress-format detailed
```

The option also works with `run-batch` and `run-prompt`; `--progress-format simple`
explicitly selects the default. `run.bat` uses the simple default. Detailed lines
start with `PROGRESS` and identify the phase, invocation-local event number and
elapsed milliseconds. Order lines include the actual Order number, its position
in the batch, and an `order-0007` identifier rather than prompt text. Phases distinguish
generation/waiting, saved generation, skipped generation, downloading, pending
receipt reconciliation, saved candidates, and finalized Orders. No phase claims
Start acknowledgement or Flow's internal percentage. Automatic-retry events
describe existing durable authorizations; they do not grant another retry.

Counts become verified only after the existing ownership/asset planning checks
succeed. `percent` is the integer percentage of validated, fully finalized Orders,
including valid work from earlier invocations. Completed generation with unfinished
downloads is not a completed Order. Detailed mode partitions the batch into `pending`,
`active`, `completed`, `waiting`, `failed_or_blocked`, and `stopped` (interrupted
active work). These are display states, not new saved statuses or recovery rules.
In particular, failed downloads may still be normally resumable. `100%`,
`All orders complete`, and detailed `orders_finalized` report Order completion,
not command exit success; existing errors, `Interrupted.`
and exit codes remain authoritative.

Progress is transient, with at most 64 pending snapshots. Checkpoints record in
memory only; terminal output occurs at outer execution boundaries. Saved-result,
candidate and same-session retry notifications can therefore appear after the
browser call returns; detailed mode includes their original recording time.
Simple mode hides internal verification events and duplicate download notices;
the underlying typed events remain available unchanged. There is no ETA,
continuous timer, additional browser polling, or state-file watcher. A long-running
call can leave the last phase unchanged. Progress does not prove browser liveness.
Output failures disable this reporter without changing retry or ownership logic;
a blocked output stream can still delay an outer print. Ctrl+C retains its first
signal and existing recovery handling, without fresh progress output during
cancellation cleanup. Missing notifications do not prove an action did not occur.

The immutable events and pure display reducer in `nano_batch/progress.py` are
independent of browser automation. The mailbox belongs to the automation thread;
a future GUI bridge must enqueue drained snapshots for its own UI thread.
Run state and the existing validated Resume plan remain the recovery authority.
No GUI, telemetry file, new state fields, or browser instrumentation is introduced.

## Safe first real-prompt sequence

```powershell
python app.py open-flow-debug
python app.py inspect-flow
python app.py generate-one data\sample_batch.json --order 1 --preview
```

`inspect-flow` connects to `http://127.0.0.1:9222` with Playwright `connect_over_cdp`. It never launches a second browser, creates a tab, or navigates the page. It requires exactly one open tab matching the configured `https://flow.google.com/project/...` URL and fails clearly for an unavailable endpoint, no matching tab, or duplicate matching tabs.

Review the reconnaissance and preview. Only when you intentionally want to spend Flow credits, run:

```powershell
python app.py generate-one data\sample_batch.json --order 1
```

For a terminal failure requiring manual Retry, or interruption after Start may
have been sent, inspect the Flow session for a possibly completed result before
explicitly retrying. Retry authorizes a fresh generation and can spend credits:

```powershell
python app.py generate-one data\sample_batch.json --order 1 --retry
```

State is written beneath `runs/<semantic-batch-id-prefix>/run_state.json`. A completed item cannot be overwritten, even with `--retry`.

A preview reads an existing state file without changing it and reports saved
status, attempts, and errors. Sequential batch previews additionally distinguish
available automatic retry budgets, external waiting, and blocked manual Retry.
The single-item command does not provide the sequential runner's automatic policy
retry. See the failure/retry rules below rather than treating every failure alike.

## Download one completed item

`download-one` requires an existing generation with `status: completed` and persisted candidates. It never submits a prompt or regenerates the item.

Preview the exact candidates, actions, and deterministic destinations without
browser actions or state writes; saved ownership and local assets are validated:

```powershell
& '.\.venv\Scripts\python.exe' app.py download-one 'data\sample_batch.json' --order 1 --preview
```

After reviewing the preview, download the existing candidates through their individual Flow tile menus:

```powershell
& '.\.venv\Scripts\python.exe' app.py download-one 'data\sample_batch.json' --order 1
```

Candidate files are stored directly beneath `runs/<semantic-batch-id-prefix>/assets/` using deterministic order, visual, and variant stems. The extension is chosen only after the staged bytes decode successfully: PNG becomes `.png`, JPEG becomes `.jpeg`, and WebP becomes `.webp`. A placeholder `.png` name recorded during generation therefore cannot force JPEG bytes into a misleading filename.

The command matches the persisted candidate UUID to exactly one `img[data-media-id]` and enclosing `flow-image-tile`, reveals only that tile's toolbar, and requires one visible enabled `More options` control. If that exact semantic control is absent or connected/enabled with non-positive geometry, the adapter performs one bounded dedicated-window normalization, reacquires the same UUID from fresh DOM state, and reevaluates actionability; it never substitutes a candidate or uses a guessed viewport breakpoint. The first-level `Download` control is a submenu parent, not a completed download action. In its closed phase it must be a stable, connected, visible, enabled `role=menuitem` with `aria-haspopup=menu` and `aria-expanded=false`; Flow validly omits `aria-controls` at that point. The adapter reacquires and samples that parent during a short bounded settle period, verifies that `elementFromPoint` at its stable center belongs to the parent or a descendant, and moves the real page mouse directly to that center. It does not call Playwright `Locator.hover()`. If mouse movement does not expose the submenu, the adapter reacquires and revalidates the parent before sending at most one real mouse move/down/up activation. During the bounded open-state poll it again reacquires the semantic parent, requires `aria-expanded=true`, and prefers the `aria-controls` relationship when Flow adds it. A temporarily absent relationship is not rejected immediately: exactly one visible menu containing the exact semantic `1K Original size` option must remain unique for three polls before it can be used as the structural fallback. Unresolved or ambiguous structure fails closed. The final 1K item must itself be connected, visible, enabled, stable, and unobscured before one exactly-once activation. The production boundary normally supplies a verified maximized window and keeps the proven real mouse move/down/up path; the existing exact-minimized semantic Enter contingency remains exactly-once and is not used to bypass failed window normalization. There is no automatic second activation because a missing acknowledgement cannot rule out delayed acceptance safely. The adapter never automatically selects `2K Upscaled` or the disabled `4K Upscaled` option, never uses JavaScript `element.click()`, and never force-clicks through actionability failures.

The preview prints this full visible UI path—`More options` → `Download submenu` → `1K Original size`—and an `<actual-extension>` destination until validated bytes determine the canonical suffix. The command does not fetch candidate URLs directly or use the generic browser Downloads folder as canonical storage.

Because the browser is an already-running Chrome instance attached through CDP with Playwright defaults deliberately disabled, downloads are captured through Chrome's browser-level CDP download events instead of Playwright's `expect_download`. Before the final `1K Original size` activation, the command installs Browser-domain event handlers and enables GUID-named downloads in a staging directory. Exactly one `downloadWillBegin` and terminal `downloadProgress` are required; correlated bytes must validate before canonical installation. Multiple begins or completed files fail as `MULTIPLE_DOWNLOADS_FOR_CANDIDATE`; completed staging without a required begin event is not committed. Normal cleanup requests restoration of default download behavior. Intentional cancellation skips fresh browser cleanup RPCs; see the limitations below.

Flow candidate identity is the persisted `data-media-id` UUID. A transient rendered `https://flow.google.com/asb/...` source is neither persisted nor used for identity matching. Download failures include a safe stage code and bounded facts such as exact CDP window state, activation method and acknowledgement, required-background-flag status, page-lifecycle keepalive status, event counts, GUID, terminal state, and staging filenames; request URLs, raw browser command lines, headers, browser storage, and authentication data are excluded.

Interrupted downloads with a saved pending commit record can recover locally on
Resume. Before installing a project-owned candidate, the runner atomically saves
its ownership, both attempt counters, expected paths, and image-integrity metadata.
Recovery requires matching saved project/UUID ownership and checks available files
against that earlier hash, size, format, MIME, and dimensions. It can finalize a
matching canonical file or install matching staging bytes without overwriting an
existing asset. Both ordinary downloads and recovery check global file claims
across all saved candidates, Order outputs, and pending records. Ordinary planning
checks the deterministic canonical stem across supported extensions; the actual
staging path is checked before browser download. Saved state and claims are
rechecked before saving the receipt, installing bytes, and persisting completion.
If the saved snapshot changed, ordinary execution stops without overwriting that
newer state. Recovery preserves generation and download attempt counts.
Preview stays read-only; missing or conflicting evidence stops recovery. Old untracked files and
legacy runs without project evidence are never adopted automatically. This record
binds expected content and logical ownership, not the identity of the physical writer.

### Inspect leftover staging metadata offline

Supply exactly one run directory; this command does not discover runs, load prompt
files or private configuration, attach to Chrome, or contact Flow:

```powershell
& '.\.venv\Scripts\python.exe' -B .\app.py inspect-staging --run-dir '.\runs\example_batch'
```

The bounded JSON report uses opaque entry IDs and overlapping evidence labels:
`A` pending-receipt reference, `B` potentially needed for interrupted recovery,
`C` disputed or ambiguous evidence, `D` a file-identity association with completed
canonical metadata, and `E` apparently orphaned without proven ownership. IDs are
local to that report, not stable file identities. Missing, unreadable, inconsistent,
changing, unsafe, and truncated evidence are reported separately.

**This tool does not delete files. Results are not cleanup authorization.
Apparently orphaned does not mean disposable. Recovery eligibility remains
determined by normal Resume with its independent Batch inputs.** The inventory
does not install, adopt, recover, repair, save state, change permissions, or consume
attempts. It does not read media contents or verify image integrity; matching file
sizes or hard-link metadata cannot replace normal recovery validation.

State JSON parsing necessarily reads embedded prompts into memory. Output omits
those fields, URLs, credentials, original filenames, absolute paths, hashes, and raw
errors. Only necessary metadata references are retained during inspection. Defaults
cap state at 2 MiB/2,048 Orders, enumeration at 512 asset entries, 64 staging
directories with 64 immediate entries each, and 256 canonical metadata references.
No nested-directory crawl is performed. Numeric limits and truncation appear in
the report. Exit code `0` means inspection completed, including any reported
conflicts; `2` means invalid arguments or incomplete/unavailable inspection.

Use this offline while the run is inactive. The command does not prove writer
inactivity. Windows guards hold directory-list handles without delete sharing;
they can briefly prevent directory rename/deletion and state-file replacement.
Insufficient permissions or unsupported providers fail closed. Symlinks, junctions,
unknown reparse points, and ambiguous aliases are skipped/rejected; Windows UNC
and device roots and mapped network drives are rejected. POSIX uses descriptor-relative no-follow access,
but this release's native verification is Windows-only. No global filesystem
snapshot, provider/power-loss guarantee, or protection after handles close is
claimed. OS-managed access timestamps may change as a consequence of inspection.
The documented `-B` option also prevents Python interpreter bytecode-cache writes.

### Trace one Download trigger

`trace-download-trigger` is a diagnostic command for an existing candidate, separate from `download-one`. It is currently restricted to candidate `v01`. It does not submit a prompt, change generation/download state, validate or commit a canonical asset, or advance the download attempt count. The required activation choice selects exactly one conservative input path (`locator`, stable-center `mouse`, or accessible `keyboard`), and the confirmation flag authorizes exactly one candidate-specific Flow Download activation:

```powershell
& '.\.venv\Scripts\python.exe' app.py trace-download-trigger 'data\sample_batch.json' --order 1 --variant 1 --activation mouse --trace-label menu-open-fix --timeout 10 --run-dir 'runs\example_batch' --confirm-download-activation
```

Before input, the tracer reacquires the exact candidate's live `More options` control, opens that tile's menu, and polls briefly for one current visible menu containing one freshly queried connected/enabled Download item. Detached items are discarded and reacquired. The final item must be unobscured at its center and settled across the trailing bounding-box samples. A mouse strategy uses explicit move/down/up only after rechecking the center before both button transitions; the locator path uses a normal actionability-checked click without `force`; the keyboard path focuses the exact `button[role=menuitem]` and sends Enter. JavaScript `element.click()` is not an activation strategy.

The bounded trace records the exact target's pointer/mouse/click or keyboard event sequence, safe DOM and focus facts, menu visibility before/after, filtered Flow/Google-media request lifecycle metadata, response status and download-relevant content headers, redacted URL structure, popup/new-page and navigation signals, reversible fetch/blob/actual-anchor observations, CDP download events, and project staging filenames. It never reads request bodies, cookies, authorization headers, browser storage, authentication data, or private framework state. Query strings, fragments, unrelated hosts, opaque path components, and sensitive header values are removed or redacted. A button activation is not classified as an anchor operation. Hooks are restored and Chrome's default download behavior is reinstated after the bounded window.

Dispatching input is not treated as acceptance. The trace reports `DOWNLOAD_ACTION_NOT_ACCEPTED` when the menu stays open and there is no menu removal/focus-exit/status/native-download/relevant-network/blob/navigation evidence. Only a positive downstream signal reports `DOWNLOAD_ACTION_ACCEPTED`.

The example report path is `runs/example_batch/diagnostics/download_trigger_order_0001_v01.json`. Historical reports are excluded from this public copy. An optional safe `--trace-label` creates a distinct immutable report without overwriting prior strategy traces. The command above writes once to the fresh `download_trigger_order_0001_v01_mouse_menu-open-fix.json`, with any native file isolated beneath the adjacent `_staging` directory. Existing evidence at that exact labeled path causes a later invocation to fail closed.

Trace cleanup is outermost and independent: known V1/V2 in-page hooks are removed and Chrome's default download behavior is requested on every exit after attachment, including candidate-menu failure before hooks or temporary behavior were installed. Failure of either cleanup is recorded without skipping the other or replacing the primary trace failure.

### Passively trace a physical Download click

After the automated mouse trace delivered a complete trusted pointer/mouse/click sequence to the exact connected, enabled, unobscured `button[role="menuitem"]`, Flow still kept the Download menu open and produced no native-download, staging, relevant-network, fetch, blob, anchor, popup, or navigation signal. The next diagnostic is therefore passive and compares that automated evidence with a physical user interaction; it does not try another automated activation strategy.

Start the passive observer with the authenticated debug Chrome project already open:

```powershell
& '.\.venv\Scripts\python.exe' app.py trace-download-manual 'data\sample_batch.json' --order 1 --variant 1 --timeout 30 --run-dir 'runs\example_batch' --trace-label physical-user-click --confirm-manual-trace
```

Wait until the terminal prints `READY FOR MANUAL DOWNLOAD TEST`. Then, in that already-open Chrome window, use your physical mouse to hover candidate `v01`, click that tile's `More options` button once, and click the visible `Download` menu item once. The process waits for the remainder of the bounded 30-second window so it can observe the result.

The command performs only read-only candidate matching and passive instrumentation before the ready message. It contains no code path that opens `More options`, focuses or activates the Download item, dispatches pointer/keyboard input, submits a prompt, or generates an asset. It records only safe structural input/menu/focus facts; sanitized Flow/Google-media request metadata and response status/download-related headers; fetch, blob, actual-anchor, popup and navigation signals; browser download events; and diagnostic staging filenames. It never reads cookies, authorization data, request bodies, browser storage, credentials, or account email. Browser-level downloads are temporarily directed to the fresh diagnostic staging directory and are not moved into canonical assets.

The report is created once at `runs/example_batch/diagnostics/download_manual_order_0001_v01_physical-user-click.json`; native files, if any, remain only in `runs/example_batch/diagnostics/download_manual_order_0001_v01_physical-user-click_staging/`. Either path already existing causes a fail-closed error so prior evidence cannot be overwritten. `run_state.json` is compared byte-for-byte before the immutable report is written, download attempts are not incremented, and hooks plus Chrome's default download behavior are restored in outermost cleanup on every attached-session exit.

### Inspect the Download submenu without selecting it

The completed physical-user trace confirmed that Flow accepted the first-level Download interaction through one `POST` to its `batchexecute` endpoint, but Chrome emitted no native download event and no staging file appeared. That response was `application/json`; its `Content-Disposition: attachment; filename="response.bin"` metadata is not treated as evidence of final image bytes. The visible right-facing indicator on `Download >` is only a submenu hypothesis until the live menu structure is captured.

With the authenticated debug Chrome project already open, start the passive DOM inspector:

```powershell
& '.\.venv\Scripts\python.exe' app.py inspect-download-submenu 'data\sample_batch.json' --order 1 --variant 1 --timeout 30 --run-dir 'runs\example_batch'
```

Wait for `READY FOR MANUAL SUBMENU INSPECTION`. In Chrome, physically hover candidate `v01`, open that tile's `More options`, and hover the first-level `Download >` item. If hover alone does not expose the second level, click only that first-level parent once. Keep the second-level menu visible and do **not** select any submenu item.

The command performs no automated hover, focus, click, keyboard input, menu opening, Download selection, prompt submission, generation, network-body read, or browser-download configuration. It polls only user-visible DOM attributes and records the visible `role=menu` count, menu and item text/ARIA/disabled/tabindex facts, bounding boxes, safe icon labels, and explicit or clearly marked inferred parent/submenu relationships. Unknown relationships remain `unresolved`; private framework state and secrets are excluded.

### Foreground versus covered Download UI probe

`probe-download-ui` reuses the production-safe exact candidate, toolbar, More options, Download-parent, controlled-submenu, and exact `1K Original size` lookup helpers, but stops before activating 1K. It never arms native download tracking, submits a prompt, calls Start generation, changes run state, or writes canonical assets. Each command runs three bounded samples and safely dismisses the two diagnostic menu levels between samples. It does not call `bringToFront` or intentionally change Windows focus.

Foreground/visible condition:

Set `$candidateId` to the exact candidate UUID persisted in your own run's
`run_state.json`; use the matching batch file, order, and run directory. The
following paths and order are examples, not distributed production state.

```powershell
& '.\.venv\Scripts\python.exe' app.py probe-download-ui 'data\sample_batch_51.json' --order 28 --variant 1 --candidate-id $candidateId --label foreground-visible --samples 3 --start-delay 8 --run-dir 'runs\example_51_order_run' --confirm-ui-probe
```

After pressing Enter, use the eight-second preparation window to bring the restored dedicated Chrome/Flow window to the foreground. Do not move the mouse or touch Flow after the countdown while all three samples run.

Covered/unfocused condition:

```powershell
& '.\.venv\Scripts\python.exe' app.py probe-download-ui 'data\sample_batch_51.json' --order 28 --variant 1 --candidate-id $candidateId --label covered-unfocused --samples 3 --start-delay 8 --run-dir 'runs\example_51_order_run' --confirm-ui-probe
```

Keep Chrome restored, cover it completely with another application, leave Flow unfocused, and do not touch Flow while all three samples run. Do not minimize Chrome. Reports are written immutably beneath `runs\example_51_order_run\diagnostics` and include per-stage timings, retries/remount evidence, safe ARIA relationship facts, `document.hasFocus()`, `document.visibilityState`, and whether the existing CDP focus emulation was active.

For the example run directory, the immutable result is saved to `runs/example_batch/diagnostics/download_submenu_order_0001_v01.json`. The command creates no staging or canonical asset directory, increments no attempts, and verifies `run_state.json` byte-for-byte before writing the report.

That historical inspection established the two-phase structure: while closed, the first-level `button[role="menuitem"]` named `Download` has `aria-haspopup="menu"` and `aria-expanded="false"` but no `aria-controls`; after exposure it changes to `aria-expanded="true"` and adds `aria-controls` for the second-level menu. Its choices are enabled `1K Original size`, enabled `2K Upscaled`, and disabled `4K Upscaled`. A later physical-user trace selecting `1K Original size` confirmed the complete mechanism: a candidate-specific Flow-content GET returned a JPEG blob, a programmatic real anchor operation followed, and Chrome emitted `Browser.downloadWillBegin` plus completed `Browser.downloadProgress`. Such a report uses the example path `runs/example_batch/diagnostics/download_manual_order_0001_v01_physical-user-1k.json`; manually initiated native files stay in its adjacent `_staging` directory and are not canonical assets. Historical reports and files are not included here.

Every file must exist, be non-empty, fully decode with Pillow, have positive dimensions, use PNG/JPEG/WebP content matching its extension, and pass SHA-256 calculation before it is marked downloaded. The browser download GUID and capture mechanism are persisted for diagnosis. Raw download-request URLs and authentication information, including cookies and tokens, are not persisted; sanitized candidate source URLs may be retained as informational metadata. Generation status remains `completed`; item and candidate download statuses are tracked independently. If a later candidate fails, prior validated candidates remain persisted and are skipped on the next `download-one` invocation.

Existing files and invalid completed-download metadata fail closed. `--overwrite` is the only way to request replacement, and it should be used only after inspecting the existing deterministic assets.

For a future new one-item generation, `generate-one ... --download` invokes this same validated download path after candidate metadata has been persisted.

## Run one prompt

Supply one quoted Unicode prompt directly from PowerShell without creating a batch
JSON file:

```powershell
& '.\.venv\Scripts\python.exe' .\app.py run-prompt "A cinematic photograph of a futuristic city at night" --run-dir ".\runs\my_run"
```

`run-prompt` constructs one in-memory order with the deterministic visual ID
`prompt_0001_visual_001` and requests image mode, 16:9, and one variant. Its legacy
`preferred_model_hint="Nano Banana 2"` preserves Batch IDs without enforcing the
selected model. It then uses the same sequential generation,
1K download, validation, canonical commit, state, and resume path as `run-batch`.
Repeating the same prompt and run directory resumes or skips proven work. A different
prompt has a different semantic batch identity and is rejected against that run
directory. Real execution remains a manual normal-PowerShell operation under your
normal Windows account; add `--preview` for a browser-free plan that writes no
state. Select a valid Flow image model, 16:9, and `x1` manually before real
execution. The command checks image mode, aspect ratio, and variant count without
changing them; it does not enforce the model's name.

## Run many prompts from a file

Create a UTF-8 text file containing one prompt per line, then run:

```powershell
& '.\.venv\Scripts\python.exe' .\app.py run-prompts-file ".\prompts.txt" --run-dir ".\runs\multi_prompt_001"
```

Lines remain in file order. Surrounding whitespace is trimmed, internal whitespace
and Unicode are preserved, and blank lines plus lines beginning with `#` after
optional leading whitespace are ignored. A file with no remaining prompts is
rejected. Accepted lines become orders `1..N` with deterministic visual IDs such as
`prompt_0001_visual_001`; every order declares one variant. No temporary batch JSON
is written.

Rerunning unchanged accepted prompt content against the same run directory uses the
existing persisted state and skips or resumes work normally. Changing accepted
prompt content changes the semantic batch ID, so the existing mismatch protection
rejects that run directory. Because variant count participates in the identity, an
older two-variant prompt-wrapper run is intentionally not reusable as a one-variant
run. Select any valid Flow image model, 16:9, and `x1` manually; mode/aspect/variant
verification is read-only and model names are informational.

## Generic sequential batch runner

The existing `run` command remains state initialization/dry-run only. `run-batch` accepts any non-empty batch with unique positive contiguous Orders starting at 1, unique `visual_id` values, and non-empty prompts. Orders are validated and processed sequentially. External x1/x2 batches are supported; the sample config requests x2. Each Order delegates to `execute_one` and `execute_download_one`, retaining UUID matching, `1K Original size`, CDP correlation, image validation, canonical naming, and atomic JSON state replacement. File installation prefers a no-overwrite hard link; its Windows fallback flushes complete staging bytes and uses same-volume, no-overwrite rename. Unsupported fallback operations fail closed; see the limitations below.

Preview the persisted resume plan without creating a browser session or changing state:

```powershell
& '.\.venv\Scripts\python.exe' app.py run-batch 'data\sample_batch.json' --preview --run-dir 'runs\example_batch'
```

Historical private sample-state evidence reported all three Orders as completed
with validated candidates skipped. That state and its assets are not included.
A fresh checkout prints a pending plan; a matching existing run is decoded and
its assets are revalidated before skip actions are reported. Preview submits no
prompt, performs no browser/download action, spends no credits, and writes no state.

After reviewing that plan, the corresponding real sequential run is:

```powershell
& '.\.venv\Scripts\python.exe' app.py run-batch 'data\sample_batch.json' --run-dir 'runs\example_batch'
```

Execution follows the batch loader's validated Order sequence. Completed generation is never regenerated. Existing completed assets are opened for Pillow decoding and metadata/hash revalidation, then skipped without replacement. Completed generation with incomplete downloads resumes only the necessary downloads. An unresolved failure stops before the next Order. A later invocation uses saved state and the failure-specific rules below. Failed downloads can be attempted again on Resume without a generation retry; ambiguous canonical files remain protected. No concurrent Orders or run-state writers are supported.

New launcher-managed runs create exactly one Flow project and persist its canonical
UUID before Order 1. Resume and explicit Retry reopen that exact saved project and
never create or rotate another one. A completed generation with a failed download
therefore resumes the exact persisted candidate download without incrementing its
generation attempt count, then advances to the next pending order after a successful
canonical commit.

### An acknowledged generation stopped before candidate persistence

A successful in-process window recovery continues the same submitted attempt
using its original pre-Start baseline and observer. It does not submit again.
If generation is already persisted as completed with owned candidate UUIDs,
normal Resume can download those exact candidates and skip valid existing assets.

The adapter now checkpoints a fully correlated, complete result through the
runner's existing atomic completion save before observer/session cleanup. This
closes the cleanup-interruption gap once that save succeeds. It saves the normal
completed-generation/candidate records, with no new state schema or ownership
heuristic. If interruption or a storage error prevents the save, the preceding
durable state remains authoritative and may still require blocking Resume.
This does not persist an unfinished attempt as a proven result.

If execution instead stopped with generation failed/interrupted **before those
candidate records were saved**, acknowledgement, a project UUID, candidate counts,
or a newly visible image do not establish which image belongs to that Order.
The pre-Start baseline and observer provenance were held in memory, not durably
saved for restart reconciliation. The application keeps this case blocked; this
window fix does not retrospectively repair it or add an image-import command.
**Retry authorizes a new generation and may spend credits. Do not use Retry to
recover an image that already exists.**

Preserve the affected run and project. The user can manually save their identified
image using Flow's UI to a separate private location outside the run directory.
That preserves a copy only; it does not establish application ownership or
finalize the Order. Do not edit run state, rename an image into canonical assets,
or select a result by newest/last position. Automatic reconciliation would require
independent, durable evidence binding the exact candidate UUID to the original
attempt and its pre-Start exclusions, followed by the existing ownership and asset
integrity checks. A UUID or image alone is insufficient. Without that evidence,
leave the Order blocked and retain the manually saved image separately.

## Failure attribution, Retry, and cancellation

Start is an exactly-once action for each submitted attempt. The observer's
failure baseline is established before Start. Only failure content proven new or
changed for that attempt can establish attribution; DOM activity in a shared
history ancestor alone is insufficient. Historical/remounted cards and ambiguous
provenance do not qualify as confirmed failures. Lost post-Start evidence reports
terminal `FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS`, stops waiting, and requires manual
Retry rather than accepting success or consuming an automatic retry budget.

| Current-attempt evidence/state | Existing recovery rule |
|---|---|
| Exact policy rejection | Sequential batch commands allow one durable automatic retry per Order, using the same prompt/project. A repeat requires explicit Retry. `generate-one` does not enable that automatic policy retry. |
| Exact generic generation failure | At most two durable automatic retries per Order with the same prompt/project; a third failure requires explicit Retry. |
| Exact unusual-activity failure | One bounded recovery within the same live Flow session, reopening the saved project. A repeat in that operation requires manual Retry. Each new Start is durably counted. |
| Exact high-demand failure | Stops immediately and requires explicit Retry; no automatic retry/backoff budget is consumed. |
| Usage-limit evidence | Persists `waiting_for_flow_limit_reset`. After the limit resets, Resume with the same command, without `--retry`. |
| Proven failure/cancellation before Start was sent | Remains not submitted and normally resumable; the provisional generation attempt is rolled back. |
| Other terminal failure or submitted/ambiguous interruption | Stops the Order and protects against duplicate submission; inspect the run before explicit Retry. |

Policy and generic retry counters are persisted before the retry Start; Resume
does not reset them. No recovery rotates the Flow project, substitutes a candidate,
or bypasses account restrictions. Only exact, eligible failure classifications
receive the automatic paths above.

The pre-Start baseline also retains known candidate UUIDs. New failure content
inside a tile already identified before Start cannot establish a confirmed
current-attempt failure, even after remount or a progress update; it stops with
attribution ambiguity. This uses only the nearest tile or an ancestor's explicit
media identity, never UUIDs collected from shared history. Unchanged historical
failures remain excluded, and new genuine failures retain their classifications.
An unseen, unrelated failure with no identifying evidence cannot be distinguished
universally by DOM timing alone; the single-writer/session assumptions still apply.

Completed downloads still require unique candidate ownership across Orders and
variants before they can be skipped. Explicit and legacy URL identities must agree
when both identify a candidate, and a duplicate matching Order is rejected.
Canonical UUID hex case is equivalent for these comparisons; legacy opaque IDs
retain exact string semantics. Valid bytes alone cannot resolve conflicting ownership.
The existing explicit candidate-repair command remains separate: it may validate
earlier assets against an in-memory post-repair projection only after the disputed
target is proven to have no canonical assets or pending receipt. Normal Resume
never applies that projection or clears disputed ownership automatically.

Completed generation/download labels do not bypass state validation. Preview and
execution require generation completion evidence, no retained generation error,
the expected x1/x2 candidates, clean completed-download metadata and matching
canonical output references. Partial downloads retain download-only Resume;
inconsistent completed state fails closed without authorizing Retry or changing
files. Expected counts come from the validated Batch/runtime settings, with saved
configured-count evidence available to standalone `download-one`. The surviving
candidate-list length is never proof of that expected count. Legacy URL/opaque
identities and optional metadata remain supported when the required consistency
evidence is available. If an older undeclared Batch lacks saved count evidence,
standalone download planning is blocked; matching sequential Resume can use its
validated generation settings. Do not edit saved ownership or use `--retry` to
repair a completed-state contradiction. Preview never performs finalization repair.

During generation/download batches, one Ctrl+C requests cancellation. Direct batch
commands exit 130 after unwind; the launcher offers its recovery menu. A
submitted/ambiguous attempt retains explicit Retry protection. Intentional batch
teardown disconnects the driver without closing
externally launched Chrome and skips fresh CDP/browser cleanup calls, including
download-behavior reset and staging deletion. Completed generation, candidate
UUIDs, and committed assets remain available for download-only Resume.
Standalone `generate-one` and `download-one` use the same first-signal latch.
Swallowed/translated interrupts and a late signal after the final batch save still
exit as cancellation. If completion was already saved, it stays completed and the
next Resume validates/skips it; cancellation does not undo committed work.

Intentional disconnect also observes abandoned protocol futures that receive the
selected Playwright connection's own shutdown `TargetClosedError`. It does not
hide pre-existing or unrelated errors, change the global asyncio error handler,
or close the dedicated Chrome. Playwright owns task cancellation; this local
future handling adds no browser calls. The guarded internal bridge was tested
offline with installed Playwright 1.64.0 and synthetic futures, without starting
a driver or browser. If the internal layout is unavailable, original error
reporting remains; live Ctrl+C acceptance of this correction is still required.

## Operational limitations and verification status

- **Single writer:** run one invocation per run directory. There is no lock or
  compare-and-swap state transaction. Ordinary download checks reject a changed
  saved snapshot; they do not make concurrent writers supported.
- **Filesystem races:** checks cannot prevent mutation after the last validation.
  Trusted ownership/content checks are evidence at their boundaries, not a proof
  of which physical process installed identical bytes.
- **File installation:** JSON state uses flushed temporary files and atomic
  replacement. No-overwrite file installation prefers a hard link. If that fails,
  Windows fallback flushes an existing writable staging file, closes its handle,
  and renames it within the same volume without replacement or copying. Flush,
  sharing, unsupported-platform, and publication failures propagate; the installer
  does not chmod or delete either path to force success. Read-only historical
  staging can therefore block fallback recovery while its pending evidence remains
  intact. Receipt-based recovery supports canonical-only files after rename.
  This removes direct copying into canonical names, not all filesystem/provider or
  power-loss uncertainty. Old partial canonical files remain protected and rejected
  even when valid staging survives. Explicit overwrite retains its separate contract.
- **Staging leftovers:** publication failure, ownership rejection, cancellation, and interrupted pending commits may leave
  staging files/directories. Successful receipt recovery does not recursively
  clean historical staging directories.
- **Path confinement:** ordinary planning requires the literal `assets` child of
  the resolved run root and exact canonical/staging destinations. Detected redirects fail
  before attempt accounting, state writes, staging allocation, or adapter calls.
  A run-root alias may resolve to the trusted run; aliases of its assets and
  candidate/staging paths are rejected. Synthetic Windows junction checks are
  offline tests, not live Flow acceptance. Filesystem races after validation
  remain subject to the limitation above.
- **Browser trust:** attach only to the dedicated, manually authenticated local
  Chrome session. Loopback CDP is privileged; keep runtime data and screenshots
  private and do not expose its port externally.
- **Latency:** the bounded historical-failure scan can aggregate large subtrees.
  Synthetic deep-DOM measurements were around three seconds; this is not a
  measured real Flow baseline cost. Callback-local text reuse reduces redundant
  mutation processing in synthetic tests; baseline aggregation cost remains.
- **Acceptance:** current public-checkout behavior is offline-tested with synthetic
  files and browser doubles. The developer reports a real two-Order sequential
  acceptance at `48a0676`, using Nano Banana 2.1: two valid 1376x768 JPEGs and
  Resume skipping both completed generations/downloads. Codex did not inspect
  those private artifacts. That run predates the background-verification fallback
  and does not establish background/minimized stability. The Stage 2.1/2.2 recovery
  and Stage 3 ownership boundaries still require targeted real acceptance. Earlier private
  milestones do not establish current live UI compatibility or complete recovery
  acceptance, including post-generation Ctrl+C and edited-policy-prompt completion.

Validation evidence and the independent developer's direct Python contribution,
engineering decisions, and Codex-assisted implementation/testing are recorded in
[the engineering log](docs/ENGINEERING_LOG.md). Historical fixtures and private
acceptance outputs are not distributed. Public Git identity presentation remains
the owner's separate decision; no release or publication is implied by these checks.

## Flow reconnaissance

`inspect-flow` is read-only with respect to Flow UI actions. After bounded SPA stabilization, it reports sanitized URL/title/readiness, control and media metadata, selected settings, and loading/error evidence. It writes a local screenshot to `diagnostics/flow_inspect.png`. Structured text removes or redacts query strings, fragments, data/blob URLs, email addresses, and Google Account labels. These filters do not redact screenshot pixels; keep the image private.

For download-toolbar layout measurements on the currently open Flow project, run `& '.\.venv\Scripts\python.exe' .\app.py inspect-flow-download-layout`. This read-only command reports the exact CDP window state, outer/inner/viewport dimensions, normalized project identity, and at most ten visible candidate tiles with safe `data-media-id`, bounding-box, and semantic `More options` presence facts. It does not hover, click, type, navigate, take a screenshot, or manipulate the window, and it deliberately does not guess a responsive breakpoint.

Historical evidence from the separate application described a rendered authenticated editor with a unique contenteditable prompt surface, a `Start generation` control, and informational `Nano Banana Pro`, `crop_16_9`, and `x2` labels. That private inspection is not a public-checkout acceptance result. The adapter permits inferred image-mode evidence without claiming an explicit Image tab or enforcing that model name. Screenshot capture is capped at five seconds; a failed refresh retains prior local diagnostics.

Historical manual evidence favored installed Chrome for authentication; bundled Playwright Chromium was rejected during that earlier login attempt. This does not verify the current public checkout against live Flow. Authentication remains manual, with the adapter attaching to installed Chrome over local CDP.

Candidate completion requires the configured count of new candidate UUIDs outside the pre-Start baseline, disappearance of current progress, and stability across result polls. Candidate source URLs, when retained, are sanitized informational metadata; selection and Resume use the persisted UUID. Earlier private reconnaissance observed UUID-owned `flow-image-tile` controls; the limited developer-reported public acceptance above does not establish compatibility across all Flow layouts or recovery paths.

## Sample input

`data/sample_batch.json` contains exactly 3 harmless prompts in a YouTube Factory-compatible structure.

## Intended future flow

```text
YouTube Factory batch.json
        ↓
load / validate
        ↓
persistent queue
        ↓
Flow / Nano Banana
        ↓
2 candidates per visual (configurable)
        ↓
candidate selection (later)
        ↓
one selected image per visual_id
        ↓
YouTube Factory import
```

Accepted historical milestones include a 51-order Nano Banana 2 production run
and a minimized-Chrome three-order run. Their runtime artifacts are private and
excluded from this public copy. The acceptance results and operational
constraints are recorded in `AGENTS.md`. The automatic maximized-window and
project-per-batch hardening is offline-tested. The developer-reported two-Order
acceptance above covers the ordinary sequential and completed-Resume path;
window recovery, minimized/background stability and the new flag-verification
fallback still require targeted real acceptance.

## License

Licensed under the [MIT License](LICENSE).

Copyright (c) 2026 Amarsan Tsyrendorzhiev

Third-party dependencies retain their own licenses:
[Playwright (Apache 2.0)](https://github.com/microsoft/playwright-python/blob/v1.60.0/LICENSE)
and [Pillow (MIT-CMU)](https://github.com/python-pillow/Pillow/blob/12.3.0/LICENSE).
They are installed separately, rather than vendored in this repository. The
project license does not grant rights to Google Flow's service or trademarks.
