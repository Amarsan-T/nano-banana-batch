# Nano Banana Batch v0.1 — AGENTS.md
This repository is a standalone local automation project for reliable sequential
still-image generation through Google Flow. Do not couple it to YouTube Factory
production code; compatibility is through external batch and asset formats only.
This is the public source copy. Keep authenticated profiles, run state, assets,
diagnostics, personal prompts, and private configuration out of version control.
Historical acceptance results below are context; their runtime artifacts are not
included. Documentation paths are examples and test UUIDs are synthetic. Never
write to a separate production checkout or its runtime files from this workspace.
## 1. Current production contract
Primary prompt-file workflow:
- model: any valid Flow image model selected manually by the user
- aspect ratio: 16:9
- variants: x1
- download quality: 1K Original size
- execution: strictly sequential
- browser: installed Google Chrome attached through loopback CDP
- authentication: manual Google login in the dedicated persistent Chrome profile
Flow settings are verified read-only. The adapter may record a safely detected
model name for information, but it never enforces, opens, or changes the model
selector. External batch planning remains compatible with x1 and x2; the
historical 51-order acceptance used x2.
The product goal is reliable large-batch execution with exact Flow project and
candidate identity, resumable state, and validated canonical local assets without
duplicate generation or ambiguous UI actions.
## 2. Supported user workflow and window policy
Normal workflow:
1. Run `run.bat`.
2. Choose **Launch Chrome** if the dedicated Flow Chrome is not running.
3. Choose **Start new batch** or **Resume current prompts**.
4. Leave the dedicated Chrome alone; other applications may be used normally.
During an active batch, the dedicated Chrome must be in a verified maximized
desktop layout. It may stay behind other applications. Native minimized and
snapped half-screen operation are not supported steady states.
At bounded production checkpoints the adapter may normalize only the selected
Chrome window through:
- `Browser.getWindowForTarget`
- `Browser.getWindowBounds`
- `Browser.setWindowBounds`
Minimized recovery may use `minimized -> normal -> maximized`; the final state
must verify as maximized with a positive viewport. Each distinct detected
unusable-window event may receive one bounded normalization. After restoration,
the adapter waits boundedly for the exact project, positive viewport, responsive
Flow DOM, and relevant editor/result surface; Angular remount during that settle
window is recoverable.
A successful window recovery is transparent and nonfatal. An already submitted
generation continues as the same acknowledged attempt with its original candidate
baseline. Recovery never resubmits Start, creates or consumes a generation retry,
resets correlation, rotates the project, or changes candidate identity. During
download it settles and reacquires the same candidate UUID. Repeated manual
minimizations may each be restored boundedly without bringing Chrome to the
foreground.
Never use `bringToFront`, OS focus APIs, OS mouse/keyboard window management, or
coordinate-based window management. Ctrl+C—not minimizing Chrome—is the
intentional pause mechanism.
## 3. One Flow project per new batch
Every genuinely new sequential batch owns exactly one Flow project.
New-batch sequence:
1. Normalize the dedicated Chrome window.
2. Navigate normally to `https://flow.google.com/`.
3. Resolve exactly one connected, visible, enabled project-list `button` whose
   normalized name is `add New project`.
4. Activate it once with normal Playwright interaction.
5. Require `https://flow.google.com/project/<uuid>`.
6. Persist the exact UUID and normalized URL before Order 1.
7. Run normal readiness/settings verification, then generate.
The current-project header `add` control opens project content and is never a
project-creation fallback. Do not identify creation by coordinates, size, DOM
index, first/last position, or generic `add` text.
For new-format Resume/Retry:
- reopen and verify the exact persisted project UUID;
- after navigation/window normalization, boundedly wait for the authenticated
  image editor before image-mode preflight;
- tolerate Angular loading/remount only by passive semantic reacquisition;
- do not click, type, submit, consume retry, create a project, or mutate runtime
  state during that wait;
- never create or rotate another project.
Legacy states without `flow_project` metadata retain configured-project behavior.
Never guess historical project ownership.
## 4. Adapter and interaction boundaries
All Flow-specific browser logic belongs in `nano_batch/adapters/flow.py`.
Queue/state/orchestration code must not know Flow selectors or duplicate browser
logic. Keep changes narrow and reuse proven adapter paths.
For Flow controls proven unreliable with generic Playwright actions, the accepted
direct-mouse sequence is:
1. exact semantic reacquisition;
2. connected/visible/enabled checks;
3. bounded bounding-box stability;
4. pointer-events validation;
5. `elementFromPoint()` ownership;
6. overlay detection;
7. `page.mouse.move/down/up`.
Use this only on already-proven problematic actions. Never force-click or
JavaScript-click Flow controls.
Generation preflight does not require one Start control globally. Before prompt
input, resolve the unique current prompt editor, its active composer, and exactly
one connected, visible, positive-geometry Start control structurally owned by
that composer. The owned control may legitimately be disabled with
`pointer-events:none` while the editor is empty; ownership and actionability are
separate proofs. After exact prompt entry and verification, boundedly reacquire
the same semantic owner or its Angular replacement and require the Start control
to be enabled, pointer-usable, stable, and unobscured before its exactly-once
activation. Unrelated, stale, or hidden controls are ignored; multiple genuinely
owned actionable controls fail closed. Both settle phases use fresh DOM state
without clicking. A failure before mouse activation remains not-submitted and
normally resumable.
## 5. Candidate identity and correlation
Authoritative candidate identity is the persisted `data-media-id` UUID, never a
transient image URL.
Required invariants:
- UUID ownership is unique within a run;
- historical run-owned UUIDs cannot be reused;
- pre-Start baseline exclusion is mandatory;
- x1/x2 must produce exactly the configured new-candidate count;
- ambiguity fails closed;
- resume/recovery reacquires the same exact UUID.
Flow may virtualize older result cards, so zero current DOM matches does not by
itself prove that a persisted candidate was lost. During download-only Resume,
the adapter may boundedly identify and search the scroll surface that owns
rendered `flow-image-tile` results, reacquiring the DOM fresh after each move.
The persisted UUID remains the only selection identity; recovery never
regenerates, substitutes another card, consumes a generation retry, or changes
the Flow project. If the exact UUID still cannot be proven, recovery fails
closed with `CANDIDATE_REACQUISITION_TIMEOUT`.
Never select a candidate by first/newest/last position, visibility alone, or
screen position.
## 6. Generation and explicit failures
Before Start, verify the manually selected Flow settings. Do not automatically
change model, aspect ratio, variant count, or other generation settings.
Start is an exactly-once action. Ambiguous acknowledgement fails closed and Start
must never be submitted twice. Result observation remains tied to the current
submitted attempt and its pre-Start candidate baseline.
A coded pre-Start failure that proves Start was never activated is persisted as
not submitted and remains normally resumable without consuming a generation
retry. The exact legacy page-global duplicate-Start preflight failure receives the
same treatment only when its empty candidate/result/download state also proves it
was pre-Start. Any ambiguous or post-Start failure retains explicit Retry
protection.
Failure semantics:
- current-attempt usage-limit evidence persists
  `waiting_for_flow_limit_reset`; resume uses the same command without `--retry`;
- ordinary terminal generation failure persists failed/retry-required and never
  retries automatically;
- `FLOW_HIGH_DEMAND` requires the current-attempt card clauses `Failed`,
  `Flow is experiencing high demand. Please try again later.`, and
  `You have not been charged for this generation.`; it stops waiting immediately
  and requires normal explicit manual Retry of the same Order/prompt/project,
  without backoff or consuming any automatic recovery budget;
- exact unusual-activity evidence requires all of `Failed`,
  `We noticed some unusual activity.`,
  `Please visit the Help Center for more information.`, and
  `You have not been charged for this generation.`; Flow may insert the exact
  advisory `Note that browser extensions that modify the page may cause issues.`
  between the unusual-activity and Help Center clauses;
- exact policy rejection requires all of `Failed`,
  `This generation might violate our policies.`,
  `Please try a different prompt or send feedback.`, and
  `You have not been charged for this generation.`;
- exact generic generation failure requires all of `Failed`,
  `Sorry, this image failed to generate.`, and
  `You have not been charged for this generation.`.
Only post-Start observer evidence for the submitted attempt may match. Historical
cards and partial/generic text are insufficient.
The observer retains only a bounded set of failure-semantic containers changed or
introduced after the Start boundary and rechecks those containers on each normal
result poll. Exact matching considers normalized visible/text and accessibility
labels independently, so nested inline content is preserved without allowing an
unchanged pre-Start card to become current-attempt evidence.
The first exact current-attempt unusual-activity failure receives one bounded
automatic recovery while the same `FlowSession`, browser connection, CDP session,
tab, Order, prompt, and persisted project remain selected. The existing tab
navigates to canonical Flow home and then directly reopens the exact persisted
project URL; it never creates or selects another project. After authenticated
editor readiness, all composer DOM, observer, and candidate-baseline state is
rebuilt before the retry Start. The submitted attempt remains consumed and the
new attempt is durably counted before Start. A repeated exact failure stops the
Order with normal retry protection; later Orders do not start.
A policy rejection is first persisted as an ordinary failed generation. The
runner may then consume exactly one durable automatic retry for that Order using
the same prompt, saved project, and normal baseline/correlation path. Production
authorizes and runs that retry inside the same open generation session.
`automatic_policy_retry_count=1` is saved before the retry Start, so Resume cannot
reset it. A repeated rejection remains manually retry-required. Never rotate a
project, work around an account restriction, or bypass a policy failure.
After that policy budget is exhausted, the CLI/launcher offers explicit Retry,
edit only this failed Order's prompt and retry, main menu, or Exit. The editor
accepts Unicode/multiline text until an exact `END` line; empty input, `CANCEL`,
or EOF cancels without writes. Editing requires failed policy state with no
candidate/download results or local assets; it does not submit Start or reset
the automatic retry budget. Another rejection of the edited prompt returns to
the same protected menu, never an automatic loop.
This prompt-edit recovery is offline-tested. Real production evidence has also
confirmed the repeated-policy recovery menu and entry into the prompt editor. The
end-to-end path of saving an edited prompt, retrying that same Order, and completing
successfully remains pending.
An exact generic generation failure receives at most two durable automatic
retries for the same Order, prompt, project, browser connection, and open editor.
Each `automatic_generic_failure_retry_count` increment is saved before the fresh
normal attempt, which rebuilds the candidate baseline, composer/control lookup,
and current-attempt observer without clicking Flow's failure-card Retry control
or navigating through Flow home. A third consecutive exact generic failure stops
the Order with normal manual Retry protection; Resume never resets the budget.
## 7. Download pipeline
Accepted path:
`candidate UUID -> exact tile -> More options -> Download submenu -> 1K Original size -> CDP acknowledgement -> completed staging file -> Pillow validation -> byte-derived extension -> atomic canonical commit`
The first-level `Download` item is a submenu parent. Do not assume PNG; the
canonical extension comes from validated bytes.
Before More options:
- reacquire the exact UUID and structural owner;
- require connected/enabled state and positive stable geometry;
- validate pointer events and `elementFromPoint()` ownership;
- detect overlays and fail closed on unrelated occlusion.
Only the existing bounded snackbar wait/reacquisition is permitted for a proven
Material snackbar.
If More options is absent or connected/enabled with non-positive geometry, the
adapter may perform exactly one bounded window normalization, settle, reacquire
the same UUID, and reevaluate. Do not hardcode a width breakpoint, regenerate,
substitute a candidate, or loop indefinitely.
Final `1K Original size` uses the proven mouse path in normal/maximized/fullscreen
or unknown state. The historical minimized path may use the existing semantic
focus + Enter activation, but active production normalizes to maximized. Never
repeat the final download action after ambiguous acknowledgement.
## 8. State and resume invariants
`run_state.json` is the runtime source of truth; writes are transactional and
atomic.
Never:
- regenerate completed generation;
- discard a successful partial candidate download;
- reorder items or shift later `visual_id` mappings;
- overwrite a valid canonical asset without explicit recovery logic;
- edit persisted ownership by hand.
If generation is complete but download is incomplete, resume download only,
preserve attempt count, reacquire the persisted UUID, and advance only after the
download succeeds. If one expected asset is valid, preserve it and retry only the
missing/failed download. Corrupt or ambiguous ownership requires the explicit
transactional repair path.
Resume may atomically finalize stale Order-level download metadata only when
generation is completed and every expected persisted candidate already has a
fully validated canonical asset, matching output paths, unique UUID ownership,
and no conflicting download error. Candidate metadata, generation/retry counts,
prompts, project, and assets remain unchanged; no browser generation/download is
performed for that Order. This interrupted-finalization recovery is offline-tested;
real acceptance remains pending.
Project rotation is never recovery for limits, unusual activity, ordinary
failure, timeout, candidate ambiguity/reacquisition, or download failure.
A policy-recovery prompt edit is run-local: `prompt` stores the effective text
and optional `source_prompt` retains the immutable original semantic-batch
binding (state version 5 on edit). Batch ID, Order/visual identity, `prompts.txt`,
project, neighboring Orders, assets, and retry counts remain unchanged. Resume
matches the original source and submits the effective stored prompt; changed
source files still fail the existing semantic Batch ID/identity checks. Legacy
states without the binding keep exact original-prompt matching.
## 9. Browser, authentication, and security
Use installed Chrome, `.flow_chrome_profile`, loopback CDP at
`127.0.0.1:9222`, and manual authentication.
The dedicated Chrome deliberately omits `--enable-automation`; loopback CDP and
the existing attach-over-CDP architecture remain enabled. That omission prevents
`Browser.getBrowserCommandLine` from returning arguments. Windows verification
falls back to browser-target `SystemInfo.getInfo` and native Windows argument
parsing; retain only four flag-presence booleans, never raw command lines or
unrelated system metadata. If both methods are unavailable, status is explicitly
unknown and nonfatal, with manual `chrome://version` guidance. A relaunch alone
does not guarantee introspection support. Proven missing flags still fail closed.
Existing debug Chrome is reused without changing its startup flags or profile;
only the user may close and relaunch it when flags are missing. Verification
does not prove minimized stability or actual rendering performance.
Never store or print passwords, cookies, tokens, storage secrets, or account
credentials; expose CDP beyond loopback; automate CAPTCHA/login; bypass security,
quota, usage-limit, rate-limit, or access controls; or add stealth/evasion.
Background hardening includes:
- `--disable-background-timer-throttling`
- `--disable-backgrounding-occluded-windows`
- `--disable-renderer-backgrounding`
- `--disable-features=CalculateNativeWinOcclusion`
- live flag verification when available
- `Page.setWebLifecycleState(active)` where supported
- scoped Windows system/display idle protection during real batches
These do not make native-minimized Flow a supported active-batch mode.
## 10. Launcher behavior
`run.bat` menu:
1. Launch Chrome
2. Start new batch
3. Resume current prompts
4. Open prompts.txt
5. Open latest images
6. Exit
`run.bat` invokes `.venv\Scripts\python.exe` directly.
Generic Launch Chrome starts at canonical `https://flow.google.com/`, or leaves
an available dedicated debug Chrome/Flow tab alone. It never uses a run-owned
historical project as the global startup destination.
Start new batch creates a fresh run directory and one persisted Flow project.
Resume offers semantically matching Batch IDs and reopens the selected
run's exact project. For a persisted failed/interrupted generation, it offers
explicit Retry, main menu, or Exit rather than issuing a doomed normal Resume.
Before creating a new run, the launcher asks `Batch folder name (blank = automatic):`.
A custom Windows-safe name is used exactly under `runs/`; blank keeps timestamp
naming. Invalid or existing names are rejected and reprompted without reuse,
overwrites, or custom-name suffixes. Case-insensitive `CANCEL` returns to the menu
without creation. Folder names do not affect semantic Batch IDs or asset names.
Resume discovers both legacy and custom names from valid matching run-state
metadata, not a directory-name prefix, and displays their actual folder names.
One Ctrl+C during a launcher batch is sufficient to request cancellation,
unwind through normal cleanup, and open the bounded recovery menu. The exact
`socket.send() raised exception.` asyncio warning is suppressed only while an
intentional Ctrl+C teardown is in progress; unexpected transport failures still
surface. If the interrupt checkpoint proves Start mouse-down was never reached,
the Order remains pending/not-submitted, its provisional attempt is rolled back,
and normal Resume is offered. Once mouse-down may have been sent, the Order keeps
submitted/ambiguous exactly-once protection and requires explicit Retry.
KeyboardInterrupt is never converted into an ordinary Flow failure. State,
assets, ownership, policy-retry history, and project identity remain intact.
Direct non-launcher commands exit 130 after clean interruption.
The first SIGINT is both immediate and latched for the active batch invocation.
Bounded setup, editor, result/failure, retry/recovery, candidate, download, and
window waits recheck that shared cancellation state, so no nested loop can require
a second signal or dispatch another Start. Intentional Playwright teardown avoids
new synchronous CDP/browser RPCs after cancellation and disconnects the driver
without closing the dedicated Chrome. Interrupt diagnostics remain prompt-free and
record only bounded phase/submission and cleanup/recovery facts.
Post-generation cancellation boundaries also preserve the first signal when a
blocking call returns normally or translates it into a transport exception.
After cancellation, menu/observer cleanup, history restoration, download-behavior
reset, CDP detach, and staging-directory deletion are skipped; the driver is
disconnected without closing externally launched Chrome. Interrupted staging
directories may remain. Completed generation, persisted candidate UUIDs, and
previously committed assets retain the existing download-only Resume semantics.
The launcher prints bounded signal/phase/blocking-operation recovery facts.
This path is offline-tested; real post-generation one-Ctrl+C verification remains
pending.
## 11. Prompt-file semantics
For `run-prompts-file` and launcher batches:
- UTF-8;
- one order per non-empty accepted line;
- trim surrounding whitespace, preserve internal Unicode;
- ignore blank lines and lines beginning with `#`;
- prompt-wrapper target is x1.
Changing accepted prompt content changes the semantic Batch ID. Same prompts and
run directory means Resume; a new run directory means a separate rerun.
## 12. Accepted milestones
Historical evidence only; do not recreate it.
- Historical 51-order acceptance: 51/51 orders, 102 unique UUIDs, 102 validated assets,
  no missing/extra or duplicate-SHA assets. This proves generic sequential x2.
- Historical three-order acceptance: 3/3 orders and 6/6 assets while Chrome
  remained minimized after Order 1 acknowledgement. Later long-run evidence made
  minimized operation unsupported despite this historical success.
Implemented and offline-tested: one project per new batch, exact-project
Resume/Retry, transparent post-restore settle, same-attempt generation watchdog,
same-UUID responsive-toolbar recovery, exact unusual-activity detection with one
same-session home/project retry, exact policy rejection with one durable retry,
policy-recovery prompt editing, exact generic-generation failure handling with two
durable automatic retries, exact FLOW_HIGH_DEMAND detection with immediate manual
Retry, usage-limit waiting state, passive editor readiness, and authoritative
single-SIGINT cancellation semantics.
The final bounded real two-prompt acceptance of the combined project/window
hardening remains pending unless a later real run explicitly proves it.
## 13. Scope
In scope: still-image generation, sequential batches, x1 prompt files, compatible
x1/x2 external planning, user-selected valid Flow image models, 16:9, 1K,
deterministic assets, persistent state, Resume, explicit Retry, partial-download
preservation, and one project per new batch.
Out of scope unless explicitly requested: concurrency, automatic candidate
judging, 2K/4K upscale, web image search, multiple accounts, login/CAPTCHA
automation, video generation, CapCut, YouTube Factory mutation/integration, and
GUI development.
## 14. Diagnostics and CLI policy
Keep production/recovery commands and bounded diagnostics that remain useful for
current failure classes. Current read-only/bounded diagnostics include:
- `inspect-flow`
- `inspect-flow-project-ui`
- `inspect-current-flow-project`
- `inspect-flow-download-layout`
- `inspect-flow-model-menu`
- `inspect-download-submenu`
- `probe-download-ui`
- download trigger/manual trace commands
Respect each command's existing confirmation and mutation boundary.
`probe-download-ui` must not submit Start, activate final 1K, or mutate state.
Model-menu inspection is strictly read-only: no click, hover, input, navigation,
screenshot, focus/window change, or model mutation.
Diagnostics must remain bounded and privacy-filtered. Width is evidence only;
production uses semantic ownership/actionability, not guessed breakpoints.
Generation result details include privacy-safe monotonic pre-Start phase timing and
bounded post-Start acknowledgement timing. The latter starts immediately before
Start mouse-down, records only existing-poll state changes and stage durations, and
never changes acknowledgement criteria or polling. Optional terminal rendering of
both summaries is controlled only by the top-level `prestart_timing_debug` boolean,
default false; it never includes prompt content or changes waits, polling,
activation, or acknowledgement behavior.
With `prestart_timing_debug=true`, progressive-latency diagnostics add exactly two
count-only DOM/history snapshots and two cheap browser round trips per submitted
attempt. Existing acknowledgement-check timings retain at most 128 samples per
stage. Adapter-owned generation observer lifecycle counters persist as scalars
for the page lifetime; callback cost/counts reset per attempt. First mutation
callback timing uses the last pre-down browser clock sample with reported RPC
uncertainty; the older mutation counter resets at observer arm, before mouse
positioning, and is not a cumulative session counter. Rendered history counts are
proxies, not a proven total project/virtualized history count; unavailable metrics
are null. One prompt-free `ORDER LATENCY SUMMARY` line prints after each newly
completed batch Order, not for skipped completed generations. These are offline-
tested diagnostics only: they neither prove the real slowdown cause nor optimize,
reload, rotate projects, or alter polling/acknowledgement/recovery semantics.
Opt-in acknowledgement poll breakdowns measure only existing calls, including
nested RPC/helper spans and the existing poll wait. They retain fixed-name
aggregates, at most 128 percentile samples per name, and five slowest polls;
completed Orders print a concise `ACK POLL BREAKDOWN`. Inclusive parent/child
durations must not be summed; helper self-time identifies work outside nested
calls. Browser callback, first nonzero Python observer-read completion, their gap,
and safe unavailable reasons remain distinct. The nominal five-second ACK loop
deadline is checked before polls only: a poll has no finite guaranteed wall-time
bound because page evaluation and locator JavaScript execution are not bounded
by locator-resolution timeouts. This instrumentation does not change that behavior.
Opt-in `OBSERVER READ BREAKDOWN` microtiming wraps only the existing observer read
and its retained-container rescan; it adds no DOM passes or observer callbacks.
The unchanged 200-container limit is fully scanned, with at most 128 container
duration samples and five slowest read records retained across ACK/result waits.
Fixed-name numeric stages distinguish text access, normalization/signatures,
style/geometry/visibility, scan self-work, and return-object construction. Existing
pre-click/post-ACK count snapshots are reused and labeled, not claimed as live
per-read DOM counts. Nested stage times are inclusive; dominance uses self-time.
Python wall time and browser `performance.now()` duration are distinct. Their
residual includes queueing, transport, protocol serialization, and wrapper work;
those components cannot be separated directly. Existing clock-anchor estimates
carry uncertainty; unavailable/inconsistent estimates remain null. Return-object
construction is not protocol serialization, and accessor timing alone does not
prove forced layout/reflow. This is offline-tested instrumentation, not a proven
real slowdown diagnosis or optimization.
Opt-in `EVENT LOOP VS CDP` installs passive document-capture listeners in the
existing pre-Start observer setup. The first trusted primary gesture after arm
schedules one microtask of each kind, one zero-delay timeout, and two one-shot
animation frames. It records numeric browser timestamps only, not target identity.
One best-effort sentinel read follows ACK success/terminal failure; no sentinel
RPC enters the ACK/result poll. Existing ACK timestamps are committed before that
read. Optional longtask observation retains aggregates for at most 128 eligible
entries, with truncation reported; the window ends at the post-ACK read, not an
exact browser-side ACK boundary. Read/normal attempt cleanup disconnects it and
cancels pending callbacks; a one-shot 30-second expiry bounds abandoned diagnostics
without a fresh Ctrl+C cleanup RPC (delivery still depends on the browser loop).
Comparisons use browser-relative clocks; Python alignment estimates above 50 ms
uncertainty are unavailable. Early responsive callbacks cannot exclude later
blockage, and delayed frames/timers may reflect throttling. Hints are evidence,
not a proven renderer/CDP cause or optimization. Disabled diagnostics installs
no sentinel/listeners/timers/longtask observer.
Real production sentinel evidence has now been collected across repeated Orders.
The page remains responsive for the first tens of milliseconds after Start
(microtasks, zero-delay timeout, animation frames, and early mutation callbacks),
then repeatedly records one dominant main-thread long task lasting roughly 6-8
seconds. The observer page-evaluate body itself remains sub-millisecond and begins
only after that long task finishes. Treat this as proven timing behavior of the
current Flow UI, not as proof of server-side cause or a Python-side optimization
opportunity.
Opt-in `GENERATION NETWORK TIMING` reuses the selected page CDP session and the
existing post-ACK sentinel read. Before Start it enables metadata-only Network
observation and samples Performance TimeTicks `Timestamp`/`NavigationStart`, then
disables Performance. Request timestamps subtract NavigationStart and the browser
pointerdown; the existing performance.now()/Python clock bracket validates this
alignment. RPC widths plus residual give empirical uncertainty; inconsistent,
missing, expired, or >50 ms alignment is unavailable. Python event receipt and
estimated ACK are separate from browser request start. The longest task retains
its own start/duration pair. These timings do not measure server receipt/work.
The attempt listener stops after ACK/terminal ACK failure or normal cleanup;
Ctrl+C removes it locally without fresh cleanup RPCs. Capture is bounded to 30
seconds/1,000 events, 32 sanitized POST Fetch/XHR records, and 10 printed candidates.
No generation endpoint is currently verified by repository evidence: exact
identity remains unresolved, even for image-generation-like RPC names. Real
production traces have nevertheless been collected. They repeatedly show early
sanitized POST/Fetch candidates within roughly the first 60-100 ms after Start,
followed by later POST/XHR activity, including flow.google.com candidates, only
after the multi-second main-thread long task. Because the exact generation RPC is
still unverified, do not promote any candidate to the generation request or infer
server work start from these timings.
No bodies, headers, credentials, query values, raw URLs, or request IDs are
retained. This offline-tested diagnostic does not intercept traffic, add post-click
evaluation, or change Start, polling, retries, project/candidate ownership, or
downloads.
## 15. Testing and execution rules
Offline tests must never spend credits, submit real prompts, generate, or
download from Flow.
After production code changes:
1. run focused tests;
2. run relevant Flow/launcher/CLI regressions;
3. run the full offline suite;
4. run `compileall app.py nano_batch tests`;
5. run embedded JavaScript syntax checks when relevant;
6. verify persisted run-state/diagnostic/asset integrity when applicable.
Real acceptance commands are run manually from normal interactive Windows
PowerShell under your normal Windows account. Codex may inspect, edit, test offline, and analyze
persisted artifacts, but must not execute real Flow generation/download from its
sandbox unless that environment is explicitly re-proven.
## 16. Fresh-session and change discipline
For each fresh Codex session:
1. read this file completely;
2. inspect the working tree;
3. inspect `run_state.json` only when runtime state matters;
4. read only relevant implementation/tests;
5. preserve proven architecture and historical runs;
6. keep changes narrow;
7. stop before real Flow actions unless asked for a manual procedure.
Stable decisions not to rediscover: use installed Chrome; use persisted
`data-media-id`; treat Download as a submenu; do not require pre-open
`aria-controls`; use narrow validated mouse paths only where proven; derive file
extension from bytes; boundedly retry transient `os.replace` WinError 5; wait
locally for proven snackbars; create projects only from Flow home; recover narrow
toolbar geometry with one same-UUID maximization; keep active Chrome maximized
behind other apps; run real production outside the Codex sandbox.
## 17. Decision principles
- preserve exactly-once actions;
- prefer semantic identity over position;
- fail closed on ambiguity;
- preserve completed work;
- recover only the smallest proven state;
- never weaken ownership to improve success rate;
- never use project rotation as recovery;
- never bypass Flow/account restrictions;
- prefer deterministic production behavior over supporting every window state.
