# Nano Banana Batch v0.1

A deliberately small standalone experiment for obtaining organized image candidates from Google Flow / Nano Banana.

It accepts the same basic `batch.json` shape exported by YouTube Factory, preserves each `visual_id`, and is intentionally separated from the editing pipeline.

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
- Previews one selected item with zero browser, state, generation, or credit interaction.
- Runs one explicitly requested prompt through a conservative Flow adapter.
- Persists `pending → running → completed/failed`, attempt counts, candidate signals, and deterministic suggested filenames.
- Requires `--retry` for failed or interrupted work and protects completed items from overwrite.
- Previews or downloads candidates for one already-completed item without regenerating it.
- Uses each persisted Flow candidate UUID to select exactly one image tile, its first-level Download submenu parent, and the exact enabled `1K Original size` action.
- Validates PNG/JPEG/WebP files with Pillow and persists hashes, dimensions, MIME, format, paths, timestamps, and independent download status.
- Resumes missing/failed candidates while preserving already-valid downloads.
- Provides a generic sequential batch runner that delegates every item to the proven one-order generation and download paths.
- Accepts one direct prompt or an ordered UTF-8 prompt file through the same batch runner, with one image per prompt.
- Preserves the historical foreground, covered/unfocused, and minimized acceptance evidence while active production now normalizes the dedicated Chrome window to a verified maximized desktop layout; it may remain covered behind other applications.
- Contains offline unit tests.

## Deliberate limits

The underlying real paths each handle exactly one prompt or one order's persisted candidates. `run-batch` composes those primitives sequentially for every item in any validated non-empty batch. The accepted external-batch Flow configuration remains two candidates per order; `run-prompt` and `run-prompts-file` request one candidate per prompt. There is no automatic login, CAPTCHA/security bypass, candidate judging, web search, concurrency, multi-account support, hardcoded 51-item special case, or video generation.

The adapter uses accessible roles, labels, visible control text, rendered image elements, download controls, progress semantics, and terminal error messages. Image mode is accepted either from one explicitly selected Image control or, when that control is absent, from all of these positive current-page signals together: one configured-hint-matching Nano Banana/Imagen model, one matching aspect ratio, one prompt editor, and one generation action. Any selected/current Veo or video signal, missing evidence, ambiguity, loading state, or editor-readiness problem fails closed. Configuration saying `mode=image` is never sufficient DOM evidence. If the current Flow UI does not expose an unambiguous control or result, the item is marked failed. It never assumes success.

## Quick start — PowerShell

```powershell
cd "C:\path\to\nano_banana_batch_public"

python -m unittest discover -s tests -v
python app.py inspect data\sample_batch.json
python app.py run data\sample_batch.json --dry-run
python app.py generate-one data\sample_batch.json --order 1 --preview
python app.py download-one data\sample_batch.json --order 1 --preview
python app.py run-batch data\sample_batch.json --preview
python app.py run-prompt "A cinematic photograph of a futuristic city at night" --preview
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
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
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

The background switches keep timer work, occluded-window rendering, renderer
process scheduling, and Windows native-occlusion calculation from reducing the
dedicated Flow target. The dedicated launcher deliberately omits
`--enable-automation`; loopback CDP remains enabled. If Chrome cannot expose its
command line, required-switch status remains compatible but explicitly unknown. A
process whose introspected command line provably lacks a required switch is still
rejected with close/relaunch guidance. On attachment, the adapter enables page-scoped
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
Flow tab, transitions a minimized window through `normal` to `maximized` when
needed, and verifies both `windowState=maximized` and positive viewport geometry.
No `bringToFront`, OS input, or focus request is used, so Chrome can stay behind
other applications. A low-frequency result-wait watchdog permits one such recovery
while continuing the same submitted generation; it never presses Start again or
resets candidate baselines. Native minimized and snapped half-screen layouts are
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
Set top-level `"prestart_timing_debug": true` in `config.json` to print concise
`PRE-START TIMING` and `POST-START ACK TIMING` summaries during an intentional
production diagnostic; the default is `false`, so normal launcher output remains
unchanged.

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

If a prior attempt failed or the process was interrupted while the item was `running`, inspect the Flow session for a possibly completed result before explicitly retrying:

```powershell
python app.py generate-one data\sample_batch.json --order 1 --retry
```

State is written beneath `runs/<semantic-batch-id-prefix>/run_state.json`. A completed item cannot be overwritten, even with `--retry`.

A preview reads an existing state file without changing it. When the selected item has failed previously, it reports the persisted status, attempt count, last error, and that a future real execution requires `--retry`.

## Download one completed item

`download-one` requires an existing generation with `status: completed` and persisted candidates. It never submits a prompt or regenerates the item.

Preview the exact candidates, actions, and deterministic destinations with no browser or state interaction:

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

Because the browser is an already-running Chrome instance attached through CDP with Playwright defaults deliberately disabled, downloads are captured through Chrome's browser-level CDP download events instead of Playwright's `expect_download`. Before the final `1K Original size` activation, the command installs both Browser-domain event handlers and enables GUID-named downloads in a per-command staging directory. It requires exactly one `downloadWillBegin` and a terminal `downloadProgress`, validates the correlated GUID file, and only then commits it under the byte-derived canonical extension. Multiple begins or multiple completed files fail as `MULTIPLE_DOWNLOADS_FOR_CANDIDATE`; a completed staging file without the required begin event also fails closed and is not committed. Chrome's default download behavior is restored afterward.

Flow candidate identity is the persisted `data-media-id` UUID. A transient rendered `https://flow.google.com/asb/...` source is neither persisted nor used for identity matching. Download failures include a safe stage code and bounded facts such as exact CDP window state, activation method and acknowledgement, required-background-flag status, page-lifecycle keepalive status, event counts, GUID, terminal state, and staging filenames; request URLs, raw browser command lines, headers, browser storage, and authentication data are excluded.

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

Every file must exist, be non-empty, fully decode with Pillow, have positive dimensions, use PNG/JPEG/WebP content matching its extension, and pass SHA-256 calculation before it is marked downloaded. The browser download GUID and capture mechanism are persisted for diagnosis, but URLs, cookies, and tokens are not. Generation status remains `completed`; item and candidate download statuses are tracked independently. If a later candidate fails, prior validated candidates remain persisted and are skipped on the next `download-one` invocation.

Existing files and invalid completed-download metadata fail closed. `--overwrite` is the only way to request replacement, and it should be used only after inspecting the existing deterministic assets.

For a future new one-item generation, `generate-one ... --download` invokes this same validated download path after candidate metadata has been persisted.

## Run one prompt

Supply one quoted Unicode prompt directly from PowerShell without creating a batch
JSON file:

```powershell
& '.\.venv\Scripts\python.exe' .\app.py run-prompt "A cinematic photograph of a futuristic city at night" --run-dir ".\runs\my_run"
```

`run-prompt` constructs one in-memory order with the deterministic visual ID
`prompt_0001_visual_001` and explicitly declares the accepted image settings:
Nano Banana 2, 16:9, and one variant. It then uses the same sequential generation,
1K download, validation, canonical commit, state, and resume path as `run-batch`.
Repeating the same prompt and run directory resumes or skips proven work. A different
prompt has a different semantic batch identity and is rejected against that run
directory. Real execution remains a manual normal-PowerShell operation under your
normal Windows account; add `--preview` for a browser-free, state-free plan. Set Flow to
Nano Banana 2, 16:9, and `x1` manually before real execution. The command verifies
the visible settings and never changes them automatically.

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
run. Set Flow to Nano Banana 2, 16:9, and `x1` manually; verification is read-only.

## Generic sequential batch runner

The existing `run` command remains state initialization/dry-run only. `run-batch` accepts any non-empty batch that passes the existing batch contract: unique positive contiguous orders starting at 1, unique `visual_id` values, and non-empty prompts. Input items are canonicalized by validated order and processed one at a time. The current Flow acceptance configuration remains two candidates per order. The runner does not duplicate Flow logic: each order delegates to `execute_one` and then `execute_download_one`, so candidate UUID matching, `1K Original size`, native CDP correlation, image validation, canonical naming, and atomic state/file behavior remain the proven implementations.

Preview the persisted resume plan without creating a browser session or changing state:

```powershell
& '.\.venv\Scripts\python.exe' app.py run-batch 'data\sample_batch.json' --preview --run-dir 'runs\example_batch'
```

For the accepted sample state, all three orders are decoded and metadata-checked through the existing validation path and report `completed -> skip`, `complete and valid -> skip`, with both candidates `skip_valid`. A new N-item batch prints the corresponding generation and download plan for every item. Preview submits no prompt, performs no browser or download action, spends no credits, and writes no state.

After reviewing that plan, the corresponding real sequential run is:

```powershell
& '.\.venv\Scripts\python.exe' app.py run-batch 'data\sample_batch.json' --run-dir 'runs\example_batch'
```

Execution follows the batch loader's validated canonical order for all N items. A completed generation is never regenerated. Valid completed candidate assets are never opened or overwritten. A completed generation with incomplete downloads resumes only those downloads; a previously committed candidate is revalidated and skipped. Any failure stops the loop before the next order. A later invocation resumes from persisted state. Failed or interrupted generation requires the explicit `--retry` flag; download retries remain automatic under the existing download-state rules. No threads, workers, or concurrent tasks are used.

New launcher-managed runs create exactly one Flow project and persist its canonical
UUID before Order 1. Resume and explicit Retry reopen that exact saved project and
never create or rotate another one. A completed generation with a failed download
therefore resumes the exact persisted candidate download without incrementing its
generation attempt count, then advances to the next pending order after a successful
canonical commit.

## Flow reconnaissance

`inspect-flow` is read-only. After a bounded SPA-stabilization wait, it reports the sanitized current URL, title, document readiness, visible body-text length, buttons, textboxes/textareas/contenteditables, comboboxes, tabs, menus, images/videos, iframes, selected controls, model/aspect/generation signals, and loading/error states. It writes a diagnostic screenshot to `diagnostics/flow_inspect.png`. Query strings, fragments, data/blob URLs, email addresses, and Google Account control labels are omitted or redacted.

For download-toolbar layout measurements on the currently open Flow project, run `& '.\.venv\Scripts\python.exe' .\app.py inspect-flow-download-layout`. This read-only command reports the exact CDP window state, outer/inner/viewport dimensions, normalized project identity, and at most ten visible candidate tiles with safe `data-media-id`, bounding-box, and semantic `More options` presence facts. It does not hover, click, type, navigate, take a screenshot, or manipulate the window, and it deliberately does not guess a responsive breakpoint.

The authenticated CDP inspection confirmed the configured project is fully rendered: `document.readyState` is `complete`, the prompt surface is a unique visible `contenteditable="true"` element, the submission control is exposed as `Start generation`, and visible settings text reports `Nano Banana Pro`, `crop_16_9`, and `x2`. No loading or error state was visible. On this UI there is no separate accessible Image tab, so the read-only preflight reports `confidence: inferred` and includes those exact positive signals; it does not claim an explicit tab. Screenshot capture is read-only and capped at five seconds; if a later refresh times out, the prior diagnostic is retained and reported instead of allowing inspection to hang.

The configured project URL and dedicated profile have been confirmed to open the real authenticated Flow editor in normal Chrome. Bundled Playwright Chromium was rejected by Google as an unsupported or insecure sign-in environment, so all authentication remains manual. Reconnaissance now attaches to the already-working normal Chrome instance over local CDP instead of opening the profile a second time.

Candidate completion requires new visible rendered image assets (preferred) or new user-facing download controls, no new progress signal, and a stable result across three polls. Candidate source URLs are stored only after removing query strings and fragments. Live reconnaissance confirmed that each completed candidate's persisted UUID maps to one `flow-image-tile`; a mouse move to the image center reveals one tile-specific `More options` control whose menu contains one `Download` action.

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
project-per-batch hardening is offline-tested; its final bounded real two-prompt
acceptance remains pending.
