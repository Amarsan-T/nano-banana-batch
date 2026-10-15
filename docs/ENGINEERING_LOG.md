# Engineering log

## Stage 2.1: historical Failed-card attribution

### Problem and historical context

The owner reports recognizing related failure-attribution problems during earlier
development. Exact dates, original quotations, diagnoses, and implementation
authorship from that period remain unverified. The public Git history starts at
`cd85c7e` (`Initial sanitized project snapshot`); this does not imply that earlier
development or direct owner implementation did not occur.

The current investigation reproduced a specific defect: an unchanged historical
failure and a new generation's progress share a history ancestor. The observer
treated the ancestor's aggregate text as current failure evidence after a progress
mutation. This establishes the shared-ancestor mechanism for this stage; it does
not establish who originally discovered the broader problem.

### Owner's decisions and development participation

During our collaborative discussion, the owner reviewed, adopted, refined, and
approved the precise eligibility rule and its ambiguity treatment. The owner
preferred a Start-boundary baseline combined with mutation-local qualification.
The resulting governing rule was:

> A failure may be attributed to the current generation attempt only when there
> is evidence that the failure content was newly introduced or changed for that
> attempt, rather than inherited from historical DOM content.

The owner requested explicit, enforceable local surfaces; conservative, promptly
reported ambiguity without automatic retries; and preservation of nested messages,
identical wording across attempts, progress-to-failure transitions, classifications,
and retry budgets. After reviewing the proposed eligibility rule and ambiguity
treatment, the owner approved implementation and delegated code and regression
work to Codex while retaining engineering direction and diff/commit review.

Codex inspected the adapter, wrote and ran the initially failing offline
reproduction, established the specific root cause, discussed alternatives, and
implemented this correction and its tests under that direction. No direct
owner-written patch for this stage is evidenced in this conversation. Earlier
component authorship remains unresolved; this log makes no line-count or
percentage claims about human or AI contributions.

### Root cause and alternatives

`remember()` walked from mutation targets to ancestors and immediately called
`detectFailureIn()`. `trackFailureContainers()` also retained failure-bearing
ancestors for later polling. Both paths accepted inherited historical text.
Observer activation established mutation timing, but did not establish the origin
of the failure content. Clearing the failure array or deduplicating DOM nodes did
not provide that missing proof.

Removing only immediate detection would leave the polling defect. Removing all
ancestor lookup would lose nested messages. Excluding every progress ancestor
would hide genuine failures after progress. A global blacklist of old failure
wording alone would suppress legitimate repeated failures. The selected approach
separates discovery from qualification and combines normalized failure-clause
history with local mutation and transition evidence.

### Implementation and eligibility rule

Files changed:

- `nano_batch/adapters/flow.py`
- `tests/test_flow_failure_attribution.py` (new regression suite)
- `tests/test_flow_adapter.py`
- `tests/test_flow_explicit_failures.py`
- `tests/test_flow_observer_read_timing.py`
- `tests/test_flow_event_loop_sentinel.py`
- `tests/test_flow_progressive_latency.py`
- `tests/test_flow_failure_boundary_review.py` (pre-commit and recovery regressions)
- `tests/test_flow_production_window.py` (preserved-state recovery fixtures)
- `docs/ENGINEERING_LOG.md` (new)

Production changes are confined to the generation observer in
`nano_batch/adapters/flow.py`:

- `_begin_current_generation_attempt_observation()` installs the baseline,
  provenance, transition, locality, and ambiguity helpers. Classification patterns
  and canonical failure messages remain unchanged.
- `_arm_current_generation_attempt_observation()` captures the failure-clause
  baseline at the existing Start boundary and discards queued pre-boundary
  mutation records. Re-arming the same observer during window recovery preserves
  that attempt's baseline and provenance.
- `_current_generation_attempt_failures()` reports attribution ambiguity through
  a fixed `FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS` error before normal classification
  or candidate success can proceed.

A local failure surface is the smallest message subtree containing a complete
signature, or one complete accessibility label belonging to an element. A subtree
cannot combine clauses across nested independent card/message boundaries:
`flow-image-tile`, `article`, `[data-media-id]`, alert/status roles, or `aria-live`.
A parent of an already complete descendant message cannot claim that descendant's
text. A complete label is evaluated independently of descendant text; partial
labels and text channels are never combined to construct a signature.

Discovery alone does not grant eligibility. A content-bearing added subtree,
changed text node, or changed accessibility label must supply failure-specific
mutation provenance. Class/style/visibility changes and unrelated progress are
insufficient. Normalized clause signatures ignore case, whitespace, and incidental
nonfailure text. Unchanged baseline content remains historical.

Identical wording can qualify after an observed local content reset on the exact
message node, or after progress in a separate semantic owner without baseline or
current failure evidence. Progress
ownership cannot extend across independent sibling cards. A newly mounted node
with baseline failure wording cannot establish freshness by node identity alone;
it reports ambiguity unless independent transition evidence proves eligibility.
Similarly, a broader ancestor cannot borrow historical partial clauses to complete
a newly changed sibling's message merely because its tag or role looks semantic.

The baseline visits at most 10,000 elements, and the existing retained-container
limit remains 200. Unknown failure evidence beyond a truncated baseline, or a
complete content-bearing failure that cannot be retained, reports ambiguity
conservatively. Polling remains on the existing acknowledgement/result paths;
there are no new sleeps, recovery loops, Start activations, or browser RPCs.

Ambiguity takes precedence over candidate discovery and existing automatic failure
retries. The existing generic exception persistence path saves the Order as failed
with the fixed ambiguity identifier in its error text and explicit Retry protection.
Its structured `generation_error_code` retains the existing generic-exception
behavior. No runner/state schema, retry-budget, project, candidate, download, or
launcher implementation was changed.

### Regression evidence and validation

The original regression used descendant-aggregating DOM getters and only one
post-Start mutation in the current card. Historical text remained unchanged. On
the baseline implementation, the expected empty failure list instead contained
the historical generic failure. The corrected observer leaves the failure list
empty and does not retain the shared history ancestor.

Regression coverage includes historical mutations, same-node reinsertion,
remounted and nested historical cards, partial-clause borrowing, genuine nested
messages, local resets, identical failures after new or existing progress,
independent accessibility labels, visibility changes, baseline/retention limits,
and same-attempt re-arming. Integration tests verify immediate ambiguity failure
in acknowledgement and result waits, persisted failed state, unchanged automatic
retry budgets, pending later Orders, and normal Resume requiring explicit Retry.

The first full integration run executed 955 tests and found 22 errors in two
older diagnostic fake DOM fixtures, which lacked `MutationObserver.takeRecords()`
and `document.createTreeWalker()`. Those empty-DOM fixtures were updated to expose
the real browser APIs; their diagnostic assertions were preserved. Other older
observer fixtures now register child relationships and supply text mutations on
the nodes whose content actually changes. The new aggregating fixture also checks
completion from a deeply nested text mutation, preserving meaningful nested-message
coverage instead of relying on synthetic ancestor-only text updates.

Implementation validation, before the pre-commit review below:

- Expanded focused observer/adapter/classification/diagnostic suite: 449 tests
  passed, including all 31 new regression tests.
- Full offline discovery suite: 956 tests passed, zero failures, errors, or skips.
  This includes launcher, CLI, Resume/state, candidate/download, cancellation,
  retry-budget, and public-repository security checks.
- `compileall app.py nano_batch tests`: passed. An initial redirected cache path
  exceeded Windows path limits; the normal local-cache run passed and generated
  caches were removed. The existing invalid-escape SyntaxWarning in the older
  explicit-failure test harness was not changed as part of this fix.
- Embedded begin/arm/poll JavaScript: Node syntax checks passed.
- `git diff --check` and a whitespace scan including new files: passed.

Suggested commit subject:

`fix(flow): prevent historical failures from affecting new attempts`

Suggested body:

Unrelated progress mutations previously allowed a shared history ancestor to
report an unchanged Failed card as current-attempt evidence. Capture normalized
failure clauses at the Start boundary and require local content or transition
provenance before classification. Report unresolved remount/partial-content
attribution as a manual-retry-protected failure without consuming automatic
retries. Preserve existing failure classifications and retry budgets.

Validated with 449 focused and 956 full offline tests, Python compilation,
embedded JavaScript syntax checks, and whitespace checks. Real Flow validation
remains pending.

### Limits and review status

All execution is offline with synthetic DOM and mocked browser/session interfaces.
No real Chrome, CDP connection, Flow generation, or download was used. The separate
production project and private runtime artifacts were not inspected or modified.

DOM evidence and semantic boundaries require real manual validation against Flow's
current rendering, remounting, and virtualization behavior. The baseline describes
rendered DOM content, not the complete server-side project history. An identical
remount without sufficient ownership evidence intentionally stops for manual
review; conservative ambiguity can also stop on historical partial content or
bounded observation limits. The baseline adds bounded pre-Start DOM work, whose
real latency has not been measured. Node-count bounds do not guarantee an RPC
wall-time bound.

Nothing was staged, committed, pushed, or published. The owner reviews the diff
before deciding whether to commit.

### Pre-commit findings before the final recovery correction: 2026-10-10

The attribution wording above now reflects the collaborative discussion: the
owner reviewed, adopted, refined, and approved the precise rule. The owner's
earlier firsthand observation of the broader problem is preserved; the precise
historical diagnosis and authorship remain unverified.

Normal Start ordering is safe against a click-triggered fast failure:
`submit_prompt()` installs the observer, awaits its arming evaluation (including
the baseline capture), then calls `_activate_generation_submit_button()`. That
method validates the control, moves the mouse, validates again, sends mouse-down,
validates once more, and sends mouse-up. The arming evaluation activates observation
before it returns; it does not capture another baseline after either gesture.
An offline test records this order through the actual Python submission path and
executes the real embedded observer scripts against an aggregating synthetic DOM.
An immediate failure on either mouse-down or mouse-up is classified correctly
before candidate discovery. Only one down/up activation occurs.

A distinct post-Start recovery defect was reproduced before proposing a correction.
If the observer is absent during same-attempt window recovery,
`_rearm_generation_observer_after_window_recovery()` installs a replacement and
arms it. Its fresh `activatedAtMs` is null, so it captures the post-Start DOM as a
new historical baseline. In the regression, Start introduces a genuine failure,
the observer is lost before the first failure poll, and the real recovery helper
reinstalls it. The next poll returns no failures and no attribution ambiguity.
The original-state re-arm guard covers a surviving observer, but cannot cover
lost state. The test establishes the conditional mechanism offline; occurrence
in real Flow remains unverified.

`tests/test_flow_failure_boundary_review.py` retains both checks. Its normal-Start
test passed (two gesture subcases); its lost-observer regression remained failing
at this checkpoint. The previous 956-test success predated this newly discovered
case and could not establish a fully green suite at that checkpoint. A separate
355-test run covering the existing attribution, adapter, production-window, and
public-security suites passed. Browser launches and sessions in these existing
tests are mocked; no Chrome, CDP, or Flow was accessed.

The baseline benchmark uses the real begin/arm JavaScript in Node v24.20.0,
diagnostics disabled, with synthetic text only. A lazy iterative tree walker
avoids the test fixture's recursive/array-shifting traversal overhead. Two getter
models return the same descendant-aggregating text: cached strings prepared before
measurement, and iterative text construction on each read. Setup is excluded.
Each full arming call is measured after one warm-up with three measured samples;
the table gives medians. The baseline is the only DOM-reading work in that call.
Each element contributes a fixed 32-character synthetic text. Deep chains are
stress inputs, not claims about Flow's actual DOM shape.

| Elements | Maximum chain depth | Cached text getters | Traversing text getters | Characters returned across both channels |
| --- | --- | --- | --- | --- |
| 1,000 | 1 | 3.45 ms | 3.77 ms | 64,000 |
| 10,000 | 1 | 21.68 ms | 29.20 ms | 640,000 |
| 10,000 | 10 | 49.21 ms | 75.93 ms | 3,565,000 |
| 10,000 | 100 | 199.06 ms | 235.58 ms | 32,815,000 |
| 2,000 | 2,000 | 752.25 ms | 791.04 ms | 130,063,000 |
| 4,000 | 4,000 | 3,058.15 ms | 4,273.95 ms | 520,126,000 |

Every visited element performs one aria-label read, one `innerText` read, and one
`textContent` read. Thus 10,000 visited elements mean 20,000 aggregate text reads
plus 10,000 label reads. Both text channels are read and whitespace-normalized
before the 1,200-character filter; long ancestor strings are not avoided by that
filter. Ancestors repeatedly aggregate the same descendants. A depth-N chain
therefore exposes quadratic aggregate character work even below the node cap.
The 4,000-element traversing model performed 16,004,000 subtree-node visits.
Cached getters still incur normalization over the same large strings. The node
bound is not a bound on total characters or synchronous duration. Browser style,
layout, transport, and rendering are not modeled; these timings neither measure
nor predict real Flow latency, and do not establish forced layout. Significant
synthetic synchronous cost is demonstrated; production impact is unverified.
The reproducible local benchmark script is retained in ignored review scratch
at `.tmp/stage2_1_review/benchmark_failure_baseline.py`; run with the repository
on `PYTHONPATH` and `python -B`.

No further production-code or performance changes were made during that initial review.
The adapter's SHA-256 remained unchanged from the start of review. Only this log,
the new review regression, and ignored offline benchmark scratch were written.
The owner's saved `stage_2_1_changes.diff` was preserved.

The proposed narrow recovery correction is to preserve surviving attempt state
and, when that state is lost, report terminal manual-retry-protected attribution
ambiguity rather than silently replacing the pre-Start baseline with post-Start
history. It must propagate through the settle path without being swallowed into
another wait, preserve candidate ownership, and leave all retry budgets unchanged.
That proposal was left unimplemented pending the owner's review and approval.
The commit remained on hold. Nothing was staged, committed, pushed, or published;
the separate production project remained untouched.

### Approved final recovery-gap correction and performance analysis

The owner continues to direct the project as its independent developer, working
collaboratively with Codex. After reviewing the reproduced recovery gap, the owner
approved a narrow correction: post-Start loss of essential attribution evidence
must stop with terminal `FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS`, never a new historical
baseline or a confirmed generation-failure classification. The owner also required
regression-first validation, preserved manual Retry and budgets, and a separate
approval before any production performance optimization. Codex implemented the
correction and tests under those decisions; this does not establish historical
code authorship or alter the earlier firsthand-experience distinction.

Before production changes, Codex reran the retained lost-observer regression and
observed its original failure: no failure or ambiguity was reported. The assertion
was then strengthened to require terminal ambiguity and forbid replacement, and
again failed before the correction (`FlowAutomationError not raised`). The same
test name and genuine-failure-before-loss scenario remain; it now passes. It was
not deleted, skipped, marked as an expected failure, or relaxed to obtain green
results.

The additional production changes remain confined to `nano_batch/adapters/flow.py`:

- `FlowFailureAttributionAmbiguous` is a distinct `FlowAutomationError` subclass,
  without confirmed policy/generic/unusual/usage classification. It preserves the
  fixed ambiguity identifier and existing generic-exception persistence path.
- The observer exposes a constant-time `hasFailureProvenance()` check for the
  original activation timestamp, baseline, historical signatures, mutation
  sources, and transition sets. The existing recovery snapshot reports a single
  boolean when that evidence is lost, including for an apparently active observer.
- First arming remains the normal pre-Start path. Recovery calls arming with
  `preserve_attempt=True`; missing, uninitialized, or incomplete original state
  returns failure before any baseline capture. Valid existing state is reactivated
  without resetting its timestamp, baseline, containers, or provenance. An already
  active observer with valid evidence remains untouched.
- A missing observer stops same-attempt settle on the first successful snapshot,
  even while the DOM is loading. Attribution ambiguity passes through both settle
  and window-normalization handlers immediately. The existing recovery event is
  recorded without a fresh diagnostic browser RPC on this terminal path. Latched
  cancellation takes precedence over ambiguity.
- Normal failure polls also stop on lost provenance, so a maximized window cannot
  bypass the protection. Lost evidence never enters a retained-container rescan
  or becomes an empty successful failure result. Existing exact failure patterns
  and their classification order are unchanged.

No sleeps, recovery loops, Start activations, automatic retries, state schema,
candidate/project changes, downloads, or launcher changes were added. The existing
baseline traversal and normalization remain unchanged in production.

The review regression module now has 15 tests covering first initialization,
immediate failures on both Start gestures, surviving observer re-arming, already
active state, missing observer with and without visible failure, loss between
snapshot and arm, missing baseline, active-but-incomplete provenance, normal polls,
prompt settle failure, window-wrapper propagation, false-success prevention, and
cancellation. Integration coverage verifies persisted failed Order state with the
ambiguity identifier, one consumed submitted attempt, no confirmed failure code,
zero policy/generic automatic retry increments, pending later Orders, no home/project
recovery or downloads, and Resume planning requiring explicit Retry. A subsequent
explicit manual Retry completes in the same saved project with the expected attempt
count and unchanged automatic budgets using synthetic candidates and validated
offline fixture assets.

Older window tests now model an existing inactive observer for legitimate re-arm,
assert no new observer installation, and require `preserve_attempt=True`. The
bounded DOM-readiness timeout fixture retains its observer, so it continues to
test the original timeout behavior independently of the new lost-evidence stop.
During test development, the new manual-Retry fixture initially used the wrong
runner keyword and omitted its synthetic success steps; those fixture issues were
corrected without changing runner behavior or weakening assertions.

Performance analysis found an equivalent small proposal, implemented only in an
ignored offline prototype. During baseline normalization, examine at most the
first 2,400 code units of a channel only when its raw length exceeds 1,200. If
whitespace normalization and trimming of that prefix already exceed 1,200, omit
the channel immediately; otherwise perform the original full normalization.
The normalized-and-trimmed prefix length cannot exceed the normalized-and-trimmed
whole string length: continuing the string cannot remove non-whitespace content
already counted. Thus early omission rejects only channels the existing filter
already rejects. Leading/trailing whitespace and Unicode use the same ECMAScript
whitespace and UTF-16 length rules; uncertain cases fall back to the original path.

The prototype preserves independent labels, the three original property reads,
all 10,000 visited elements, every historical signature, and truncation behavior.
It matched normalization/channel results in 1,013 boundary/Unicode/whitespace and
deterministic generated cases, including long whitespace whose normalized result
still fits. All 31 attribution regressions passed with the prototype substituted
into the real embedded scripts. These checks supplement the equivalence argument;
they do not prove browser timing or universal correctness by testing alone.

Before/after measurements use the same Node harness and getter models as the
initial review: one warm-up, three measured full arming calls, setup excluded.
The current corrected production script and the unimplemented prototype were
measured consecutively on the same synthetic DOM; the table gives medians.

| Elements / depth | Getter model | Current production | Prefix prototype |
| --- | --- | --- | --- |
| 10,000 / 1 | Cached | 19.58 ms | 20.35 ms |
| 10,000 / 1 | Traversing | 25.94 ms | 27.62 ms |
| 10,000 / 10 | Cached | 48.61 ms | 51.22 ms |
| 10,000 / 10 | Traversing | 49.39 ms | 55.83 ms |
| 10,000 / 100 | Cached | 279.36 ms | 272.30 ms |
| 10,000 / 100 | Traversing | 238.20 ms | 234.16 ms |
| 2,000 / 2,000 | Cached | 685.32 ms | 62.97 ms |
| 2,000 / 2,000 | Traversing | 778.49 ms | 187.53 ms |
| 4,000 / 4,000 | Cached | 3,024.93 ms | 120.56 ms |
| 4,000 / 4,000 | Traversing | 3,161.95 ms | 750.47 ms |

The deep stress case shows avoidable full-string normalization work. The prototype
adds overhead on shallow inputs and is not a universal speedup. Both variants
still return 520,126,000 aggregate characters and, in the 4,000-element traversing
case, visit 16,004,000 subtree nodes. It does not eliminate superlinear subtree
aggregation, layout-related `innerText` cost, or pathological whitespace scans.
Removing those remaining costs while preserving independent visible/text channels
and exact semantics would require a broader collection design and separate review;
it was not attempted. Real Flow DOM shape, layout, and latency remain unmeasured.

The reproducible ignored prototype and raw measurements are retained at
`.tmp/stage2_1_review/benchmark_failure_baseline_prefix.py` (use `--equivalence`
for comparison/regressions) and `.tmp/stage2_1_review/prefix_benchmark.jsonl`.
No prefix optimization has been applied to production; the owner must review and
approve any such follow-up separately.

Final recovery-correction validation:

- Focused attribution/recovery/adapter/classification/diagnostic suite: 473 passed.
- Full offline suite: 971 passed in 145.082 seconds, zero failures, errors, or
  skips. This includes existing launcher/CLI, Resume/state, candidate/download,
  cancellation, retry-budget, and public-security tests.
- `compileall app.py nano_batch tests`: passed.
- Node syntax checks for actual embedded begin, arm, poll, and recovery-snapshot
  JavaScript: passed.
- `git diff --check` and whitespace checks for the authored source, tests, and
  untracked engineering log: passed. Compilation caches were removed from the
  public workspace after validation; ignored benchmark evidence was retained.
- Existing invalid-escape test-harness SyntaxWarning and mocked unavailable-Chrome
  command-line warning are unchanged; no real Chrome was launched or connected.

The public workspace/root were verified before work. The separate production
checkout, profiles, live Flow sessions, and private runtime data were neither
accessed nor modified. The owner's saved review diff was preserved. Nothing was
staged, committed, pushed, published, or connected to a remote. Work stops for the
owner's review before additional production optimization or a Git commit.

## Stage 2.2: interrupted download commit recovery

### Developer participation and verified contribution history

The independent developer's hands-on Python contribution includes studying the
existing code and writing the source/RunState lookup, candidate selection by
variant, non-unique-match rejection, explicit candidate identity check, and use of
the UUID ownership helper. The saved working tree independently confirms this
initial validator draft. The developer also worked through the reasoning behind
these checks before delegating completion and integration. Codex retained those
parts and
completed validation, persistence integration, recovery, corrections, and offline
tests under the developer's direction.

The distinction between discussion drafts and saved code matters. At the previous
preparation checkpoint, Codex had left `validate_pending_download_ownership()`
absent and prepared the schema, fixture, guide, and deliberately failing tests.
At the completion checkpoint on 2026-10-11, `runner.py` contained the partial
function described above. Its pending-record expression was unfinished, and an
indentation error prevented import. These are verified repository observations;
the exact additional snippets drafted during discussion are not available in this
repository review and are not attributed as saved implementation. Codex corrected
the indentation and completed the unfinished checks. The final implementation is
not represented as wholly authored by the developer, nor is uncertain historical
code attributed solely to either participant.

The architecture and schema were discussed collaboratively. The developer reviewed
and approved durable pending evidence, independent source/state identity, unique
UUID ownership, both attempt counters, project proof, path containment, earlier
persisted integrity metadata, no-overwrite recovery, conservative legacy handling,
and idempotence. The developer then explicitly delegated completion and integration
to Codex while retaining final diff and commit approval.

### Verified gap and final implementation

The retained regressions reproduce interruption after actual canonical JPEG
installation and before candidate completion reaches disk. Before integration,
both Resume cases refused the untracked file, despite completed generation and
the unchanged persisted candidate. The original negative test also preserves the
refusal to adopt unrelated valid JPEG bytes. No regression was removed or weakened.

`PendingDownloadCommit` record version 1 lives on `CandidateRecord`. Atomic save of
an active record promotes run-state version to 6 in the same replacement; the
in-memory version changes only after success. Missing/null records remain loadable,
empty fields are omitted, ordinary legacy saves do not migrate, and clearing a
record retains version 6. Unsupported/malformed active records are rejected.

The validator independently selects a unique source and saved Order, preserving
the original source-prompt binding after a policy edit. It requires a unique
variant, explicit canonical UUID, global UUID ownership, an unconflicted asset URL,
completed generation, running download states, no conflicting completion metadata,
saved project ownership, and matching Batch/project/Order/visual/variant/UUID and
positive integer attempt counters. Canonical naming comes from the recorded byte
format. Paths must match exact relative layouts and resolve under the trusted run
without symlink/junction aliases. Other candidates' saved local/relative paths,
every Order's outputs, and competing pending records are examined globally for
claims on either file or a conflicting canonical extension. Malformed, escaping,
or inconsistent path evidence fails closed. Premature canonical or staging output
registration is rejected. This function
does not decode images, modify state, install files, or require file availability.

New project-owned downloads save expected content and ownership before canonical
installation. Image format, MIME, dimensions, byte size, and SHA-256 are persisted
from validated staging bytes. Normal completion and local recovery use one shared
atomic candidate save: completed metadata and `output_files` are written together
with removal of the pending record. In-memory completion is published only after
that replacement succeeds. Failed completion saves retain the running receipt;
staging cleanup also preserves outstanding pending evidence.

Resume planning remains read-only. Execution reconciles pending records before
incrementing another download attempt. Every available canonical/staging file must
match the earlier persisted metadata. A matching canonical file needs no install;
staging-only recovery uses the existing exclusive commit with `overwrite=False`.
Missing both files, conflicting extensions/content/ownership, or changed paths
stop without candidate completion. Filesystem safety is checked again around
installation and before completion persistence. Existing Order-finalization checks
remain in place. Local reconciliation neither regenerates nor downloads from Flow,
changes project/UUID identity, increments attempt counters, nor consumes retries.
When another variant still needs downloading, only that variant enters the normal
download path and receives the existing new-download attempt increment.

States without pending records retain the existing untracked-file guards. Legacy
records without saved project proof cannot reconcile automatically. Generic legacy
adapters without UUID evidence retain their established download path and do not
gain orphan adoption. An active pending record cannot be bypassed with `--overwrite`.
An interrupted explicit replacement that retains conflicting old completion
metadata also fails closed, as required by the validator contract.

### Regression refinement and validation

The saved partial draft initially failed import. After the indentation correction,
the validator contract still failed as expected, and the two real-file recovery
regressions demonstrated the original gap before integration. An initial recovery
test invocation encountered sandbox default-temp permissions; subsequent commands
and new fixtures put synthetic temporary data inside the public ignored `.tmp`.

Additional adversarial tests exposed and corrected two completion issues during
implementation. First, completion must persist the same metadata observation that
matched the pending evidence, rather than an earlier independent canonical read.
That test failed before the correction and now passes; completion also checks the
metadata against the record immediately before preparing its atomic save. Second,
staging paths registered as output and duplicate pending-file claims must be
rejected. Those focused negative cases failed before the checks were added and
now pass. The tests remain in the suite.

The first full run reported 50 errors from old project-owned fixtures using
non-UUID candidate names. Codex updated the synthetic adapters in `test_app.py`,
`test_flow_projects.py`, and `test_flow_explicit_failures.py` to use distinct
synthetic UUIDs; identity/forbidden-set assertions retain exact comparisons.
Production UUID validation was not relaxed. One obsolete completed-run version-4
assertion was updated to exactly version 6, reflecting the approved migration.
All 25 project tests then passed. These fixture corrections preserve the tested
generation, failure, retry, project, and cancellation behavior.

Offline validation before the final adversarial review:

- Focused validator, codec, pending recovery, and commit-boundary tests: 61 passed.
- Runner, interrupted Order-finalization, and post-generation cancellation: 99
  passed. The full suite also covers the corrected CLI, policy, unusual-activity,
  generic-failure, High Demand, project, and observer-recovery fixtures.
- Full suite: 1,032 passed in 106.722 seconds, zero failures, errors, or skips.
- Python compilation and `git diff --check`: passed. No embedded JavaScript was
  changed; the existing Node-based regressions passed in the full suite.
- Existing invalid-escape test-harness SyntaxWarning remains unchanged. Browser
  operations in tests are mocked; no real Chrome, CDP, or Flow was accessed.

### Limits and review boundary

The record binds expected content and logical ownership. It does not
cryptographically prove which physical process installed identical bytes. Repeated
path/content checks do not prevent an external filesystem mutation after the last
check. The sequential runner still assumes one writer for a run; concurrent-run
locking is not introduced here.

The separate non-atomic exclusive-copy fallback defect is unchanged. If interrupted
copying leaves a partial canonical file, recovery detects mismatching bytes and
protects the file even when valid staging survives. It never overwrites that file
to improve success rate. Recovery may leave old staging files/directories behind;
it does not perform recursive cleanup after clearing their receipt. Real Flow
acceptance remains unverified. No additional Stage 2.1 performance optimization
was implemented.

The public workspace and repository root were verified. Work remained on `main`
at `482909b`; the separate production checkout, private runtime data, profiles,
and live browser sessions were neither inspected nor modified. Nothing was staged,
committed, pushed, published, or connected to a remote. Stage 2.2 stops for the
developer's final diff review and explicit approval of a third Git commit.

### Final review correction: global file-claim ownership

During the developer-requested adversarial review, Codex reproduced a missing
ownership check: direct recovery could adopt a canonical path claimed by another
completed candidate, and sequential Resume could complete recovery while another
completed Order registered that same file in `output_files`. Unique UUID ownership
does not prove unique local file ownership. The independent developer reviewed the
finding, blocked the third commit, and authorized a narrow Stage 2.2 correction.

Before changing production recovery logic, Codex added two real-file regressions
through the existing synthetic fixture and real state codec. Both failed with
`ValueError not raised`, demonstrating the vulnerable paths. Both now reject the
conflicts before installing files, updating completion state, or calling a browser.

Codex added `_assert_pending_download_file_claims()` to the contextual validator.
It examines all saved Order outputs, other candidates' local/relative file claims
(including stale metadata on incomplete downloads), and other pending records.
Relative claims use the trusted run root. Absolute claims must stay within it;
malformed, traversing, drive-relative, escaping, or inconsistent claims fail closed.
Resolved aliases, Windows case differences, duplicate registrations of the target,
and competing canonical extensions cannot conceal ownership conflicts. Unrelated
valid completed metadata and its matching output registration remain legitimate.
The selected receipt is the permitted claim; completed metadata or premature
registration for that still-pending candidate remains prohibited.

Recovery now reloads saved state immediately before installation and completion
persistence, reruns ownership/path checks, and rejects a changed receipt. Completion
uses that fresh snapshot. Tests verify that a late conflicting claim blocks each
boundary and that a newly saved unrelated completion is preserved. No retry loop,
lock, generation change, or download behavior change was added.

The developer-written lookup, variant selection loop, non-unique-match rejection,
explicit candidate check, and UUID helper call are retained unchanged by this
correction. The replaced path-claim checks and recovery plumbing were Codex's
subsequent implementation. Codex implemented the global correction and new tests;
the developer's actual hands-on implementation and decision to withhold commit
approval remain separately attributed.

The expanded regressions also cover read-only validation, relative/backslash/case
aliases, simulated symlink/junction aliases, malformed and escaping claims, stale
completed-file metadata, pending/staging conflicts, legitimate unrelated downloads,
repeated rejection, and successful recovery idempotency. Existing pending-record,
integrity, atomic save, legacy orphan, no-overwrite, and cancellation tests remain.

Verification after the global ownership correction:

- The two original conflict scenarios: both failed before the production correction
  (0.668 seconds), then both passed afterward (0.450 seconds). Rejection assertions
  verify unchanged saved state/assets, no installation, and no browser calls.
- All Stage 2.2 validator, state codec, recovery, and commit-boundary regressions:
  78 passed in 15.825 seconds, including 17 added tests. Legitimate staging-only,
  canonical-only, partial-Order, and unrelated-completed-download recovery pass.
- Complete offline suite: 1,049 passed in 187.179 seconds; zero failures, errors,
  or skips. Flow/launcher/CLI and existing Node regressions pass in that suite.
- `compileall app.py nano_batch tests`: passed. `git diff --check` and separate
  `git diff --no-index --check` checks of all six untracked files passed.

The final adversarial review covered all eight tracked diffs and six untracked
files, completed and pending file claims, duplicated target registrations, aliases,
missing or inconsistent historical path evidence, fresh-state boundaries, receipt
identity, atomic persistence, no-overwrite protection, and idempotency. No additional
in-scope blocker was found. The source changes remain confined to the approved
Stage 2.2 ownership and recovery architecture; generation/retry code, Flow adapter,
and the existing file-installation helper were not changed by this correction.

The global check establishes ownership of the pending files, not integrity of every
unrelated historical asset. Completed candidates without sufficient path evidence,
escaping paths, or contradictory path claims stop recovery conservatively. Real
Windows symlink/junction creation was not exercised; alias resolution was simulated.
Bounded state reloads do not provide compare-and-swap persistence or prevent another
writer/filesystem mutation after the last check. The single-writer assumption,
physical-writer limitation, partial-copy protection, and staging leftovers described
above remain. Real Chrome/CDP/Flow behavior remains untested by this correction.

Stage 2.2 is ready for the developer's final diff review within those stated limits.
Workspace/root were verified again; branch remains `main`, HEAD remains `482909b`.
Only the public checkout and disposable synthetic fixtures were used. The original
project, private runtime data, credentials, profiles, and live browser sessions
were neither accessed nor modified. Nothing was staged, committed, pushed,
published, or connected to a remote. Work stops for explicit commit approval.

## Stage 3 — approved pre-publication corrections (2026-10-11)

### Audit evidence and the developer's decision

Codex's read-only audit of `80a772c` inspected all three committed trees and
identified no P0 secret exposure. It established that the declared Playwright
minimum was incompatible with the used CDP keywords, and identified misleading
setup, model, retry, atomicity, cleanup, and acceptance descriptions. An in-memory
control-flow probe also established that ordinary downloads omitted the global
file-claim guard: completion could be saved while another candidate retained a
claim to the same canonical path. Pending recovery rejected the identical claim.

The independent developer reviewed the audit and accepted READY AFTER FIXES. The
developer explicitly prioritized ordinary-download ownership, required a failing
offline regression before production changes, approved the Playwright 1.60 floor,
and directed precise documentation corrections and conservative boundaries. The
developer kept licensing, public Git identity, and the redirected-assets-root
correction separate and prohibited commits/publication in this checkpoint.

Codex wrote the Stage 3 regressions, implemented the narrow download guards, and
updated dependency/documentation text under that direction. No new hands-on Python
implementation by the developer is claimed for this checkpoint. The Stage 2.2
record of the developer's initial validator code, reasoning, collaborative design,
and subsequent Codex-assisted completion remains unchanged. The body of the
developer-originated contextual validator is unchanged by Stage 3.

### Regression-first reproduction and correction

Before editing production logic, the real synthetic-file execution regression
failed: `ValueError` was not raised and the normal download installed/completed a
file claimed by another candidate's stale paths. The first 14-test suite produced
15 assertion failures (including subtests), zero errors, and zero skips against
the original code. Two nonconflicting behavior tests passed. The corrected
14-test suite then passed. The same regression assertions were retained.

The expanded tests cover candidate local/relative claims, Order outputs, competing
pending records, alternate canonical extensions, malformed/escaping/contradictory
paths, actual staging collisions, late claims before receipt/installation/completion,
and valid independent completed assets. Further adversarial inspection added
next-candidate, Order-finalization, and failure-status persistence checks, plus
partial x2 Resume, repeated skips, explicit overwrite, and duplicate registrations.

The shared global file-claim helper now serves ordinary planning as well as pending
recovery. Planning checks the deterministic canonical stem before any download
status write. Once staging is allocated, its actual path is checked before browser
download. Ordinary skip/explicit overwrite may retain exactly one own-Order output
registration only when the selected candidate's recorded paths agree with that
canonical file; other candidates, Orders, extensions, or duplicate registrations
cannot use that exception. Recovery retains its stricter default and permits no
completed output registration of pending files.

Bounded saved-state rereads precede attempt/candidate status writes, browser
download, pending-receipt persistence, canonical installation, candidate completion,
failure persistence, and final Order completion. A changed snapshot is rejected
under the existing single-writer contract rather than merged or overwritten.
Private ownership errors leave changed durable evidence untouched; an existing
pending receipt is preserved for fail-closed recovery. Cancellation checkpoints
surround the added local checks. These checks do not constitute locking or
compare-and-swap persistence and cannot eliminate a mutation after the final check.

Adversarial review also reproduced a cleanup consequence of the new rejection
path: normal temporary-directory cleanup deleted staging newly claimed by a
competing pending record. Its real-file regression failed before the cleanup
correction. An ownership-rejection exception now retains the staging directory
and detaches its GC cleanup finalizer, so disputed bytes survive even when the selected
candidate has not yet persisted its own receipt. This remains a stop, not adoption
or automatic retry; the separate redirected-assets-root issue is unchanged.

The pending record still precedes installation, and candidate completion/removal
of its pending record still share one atomic JSON replacement. No generation,
retry-budget, Flow adapter, state-schema, or file-installation algorithm was changed.

### Compatibility and documentation

`requirements.txt` now declares `playwright>=1.60,<2`. Both existing attachment
calls pass `is_local=True` and `no_defaults=True`; official API/tagged-source review
established introduction in 1.58 and 1.60 respectively. Playwright was not installed
or upgraded in this checkpoint. Offline browser behavior uses doubles; it is not
live compatibility evidence.

README setup now names the documented Windows/Python 3.12 target and the actual
offline Python/Pillow/Node versions, creates `.venv` before installing dependencies,
explains optional activation and `run.bat`, and identifies Node as an observer-test
prerequisite. Browser-free examples no longer imply completed generation state is
distributed. Model names/hints, bounded failure-specific retries, usage-limit
waiting, ambiguity, decoding/revalidation, cancellation cleanup, atomic JSON versus
non-atomic fallback, and historical versus current acceptance are distinguished.
Trusted local CDP, private screenshots, single-writer/race/staging limitations,
baseline-scan latency, and the separate redirected-assets-root issue are disclosed.
The independent/unofficial status is explicit, without claiming legal compliance.

### Remaining boundary

The older ordinary assets-root helper is unchanged; staging can begin in a resolved
redirected directory before pending-path rejection. Trusted, unredirected run
directories remain required. Non-atomic fallback partial files, staging leftovers,
physical-writer uncertainty, filesystem races, and real Windows alias/live Flow
acceptance remain limitations. No production performance optimization was added.
License and public identity decisions remain with the developer; no LICENSE file,
Git identity change, history rewrite, remote, or release was created.

### Final validation and review checkpoint

- Focused ordinary-download ownership suite: 21 passed in 4.345 seconds. The
  original failing execution/planning assertions and the subsequently failing
  disputed-staging cleanup assertion pass without being removed or weakened.
- Earlier combined download/recovery/state/runner checks: 188 passed. The final
  complete offline suite on the completed production changes: 1,070 passed in
  219.917 seconds, zero failures, errors, or skips. Existing Flow, launcher, CLI,
  cancellation, Resume, Node observer, and state-codec regressions are included.
- The default sandbox temporary directory produced Windows access-denied errors
  during an initial broader run. Reruns used ignored public-checkout scratch space;
  no test was skipped or changed to hide those environmental errors. Intermediate
  full-suite runs were restarted after further production safeguards so the final
  result covers the completed implementation.
- `compileall app.py nano_batch tests`: passed. Tracked `git diff --check` and
  separate whitespace/UTF-8 checks including the new untracked test file passed.
- Four publication security checks passed: eligible content, synthetic UUIDs,
  sensitive-runtime ignore rules, and public-file inclusion. The current eligible
  inventory contains no new private or sensitive files.
- Fresh synthetic CLI inspection, dry-run, generation preview, batch preview,
  and prompt preview succeeded without creating a run directory/state. Candidate
  download preview without persisted generation state correctly failed with no
  writes. These were not browser/Flow acceptance checks.
- Static comparison confirms both existing CDP calls match the documented 1.60
  floor, the contextual validator body is unchanged, and prior engineering-log
  content is preserved verbatim. No package was installed/upgraded. The audit
  runtime used Python 3.12.14, Pillow 12.3.0, and Node.js 24.20.0; Playwright was
  absent and browser/CDP interactions in tests were mocked.

Final adversarial review covered own versus competing output registrations,
alternate extensions, changed snapshots before writes, failed-download saves,
candidate-to-candidate transitions, Order finalization, and retention of disputed
staging. No further in-scope P0/P1 implementation blocker was identified. Licensing
and public identity decisions are still pending with the developer. Live Flow,
fresh dependency installation, real Windows junction creation, and the documented
filesystem/latency limitations remain outside this verification.

The final inventory is README, requirements, runner, this appended log, and the
new ordinary-download ownership test file. Branch remains `main` at `80a772c`;
the index is unchanged and no remote is configured. Only the public checkout and
synthetic offline fixtures were used; the original project, private runtime data,
real profiles, browser/CDP sessions, and live Flow were not accessed. Nothing was
staged, committed, pushed, published, or rewritten. Work stops for the developer's
final pre-publication diff review and subsequent Git decision.

## Stage 3 — owner-selected MIT licensing update (2026-10-11)

The developer subsequently selected the MIT License and explicitly approved a
root LICENSE containing the complete standard text and the notice
`Copyright (c) 2026 Amarsan Tsyrendorzhiev`. This supersedes the pending licensing
instruction recorded at the earlier checkpoint above; that earlier historical
record is retained. Public Git identity presentation remains a separate decision.

Codex added LICENSE, checked its permission/notice/disclaimer body against the
standard MIT text, and updated README's current status and license link. The prior
ordinary-download correction, tests, dependency floor, validator implementation,
and engineering attribution were preserved. No Git authorship setting or history
was changed, and no production code was changed for this licensing update.

The eligible public files were checked for binary/vendor assets, symlinks,
copyright/license banners, copied/adapted-source markers, and attribution URLs.
No identifiable vendored implementation, third-party images/fonts/logos, or
copied-source attribution notice was found. Third-party service names, selectors,
and exact Flow UI message text are used for compatibility/detection; the project
license does not establish rights to Google's service or trademarks.

The separately installed dependencies retain their upstream terms: Playwright's
1.60 license is Apache 2.0, and Pillow 12.3.0 is MIT-CMU, confirmed against upstream
license files and Pillow's local metadata. They are not relicensed by this LICENSE.
No dependency was installed or upgraded. Their own notices must remain applicable
if a later release bundles third-party software; this source-only checkpoint does
not prepare such a bundle.

No affirmative third-party licensing conflict was identified. The repository
begins with a sanitized snapshot, so these checks cannot prove the provenance or
licensing rights of every earlier line or exclude unmarked copied code. Earlier
code authorship remains uncertain where the record does not establish it; the new
copyright notice is the developer's explicit licensing decision, not a fabricated
claim of exclusive historical line-by-line authorship. Trademark/Flow terms
compliance and ownership of future generated assets were not established here.

Licensing-update verification: 21 focused ownership tests passed (5.773 seconds);
the complete offline suite passed 1,070 tests (242.286 seconds), with zero failures,
errors, or skips. Four publication security checks, Python compilation, whitespace
checks including untracked files, standard MIT body/copyright comparison, README
license links, the dependency-floor check, and validator-preservation checks passed.
The public eligible inventory contains 60 UTF-8 files and no binaries or symlinks.
The intended author copyright notice is public information expressly supplied by
the developer; no private credentials or runtime material were added.

The cumulative Stage 3 inventory is four tracked modifications (README,
engineering log, runner, requirements) and two untracked additions (LICENSE and
the ordinary-download ownership test file). The licensing update itself changes
only LICENSE, README, and this appended log. All earlier engineering-log text and
the developer's validator code are preserved. Branch remains `main` at `80a772c`,
the index is unchanged, no remote is configured, and nothing was committed,
rewritten, pushed, or published. Original/private projects, real profiles,
Chrome/CDP sessions, and live Flow were not accessed. Work stops for review.

## Post-publication P1 correction — unknown failure-text privacy (2026-10-14)

The public checkout was verified clean on `main` at
`4b84be430ad476858b8d36dd7479fb864717d1dc`, with the six published commits intact.
The developer reviewed the offline audit and authorized only the unknown
generation-failure privacy correction and its regressions, leaving the other
P2/P3 findings for separate decisions. Codex implemented this patch and tests
under that direction. The earlier record of the developer's direct Python work
and collaborative engineering decisions is preserved without new authorship claims.

The reproduced path was qualified observer evidence -> Python failure text ->
terminal exception -> saved Order error -> CLI/preview output. Label truncation
and email/account redaction did not remove arbitrary prompt text, query-bearing
URLs, filesystem paths, or credential-like text. This establishes a synthetic
privacy gap, not evidence that actual Flow credentials were exposed.

Before production changes, the new end-to-end regression failed in all ten
subcases: five synthetic UI canary types with diagnostics disabled and enabled.
It ran as one test with ten assertion failures, zero errors or skips, in 3.395
seconds. The production diff was empty at that point. Those scenarios and
rejection assertions remain; subsequent checks also cover JSON-escaped canaries
and individual fake authentication/query values.

Only two adapter functions changed:

- `_safe_current_generation_failure()` uses existing recognized signatures to
  return fixed canonical text. Unrecognized qualified failures return fixed
  unknown-failure semantics. No arbitrary UI text leaves this helper.
- `_raise_for_current_generation_attempt_failure()` retains the existing typed
  classifiers and emits a fixed terminal/manual-Retry message for its unknown
  fallback, without interpolating failure text.

No new exception type, state field, failure code, browser interaction, navigation,
Start, diagnostic collection, or recovery loop was introduced. Existing safe
phase/count/timing metadata and the diagnostic category prefix remain available.
Unknown failures retain the ordinary `FlowAutomationError` path, failed Order,
consumed submitted attempt, unset confirmed generation-error code, and explicit
manual Retry protection. Automatic retry budgets remain unchanged.

The eight tests in `tests/test_flow_failure_privacy.py` cover the real embedded
observer, classification, generation failure diagnostics, runner persistence,
exception representations, CLI and saved-error preview output; all five canary
types; known policy/generic/unusual/high-demand/usage classifications; historical
and remounted ambiguous messages; similar unknown wording; and successful manual
Retry with the original project, preserved counts, and unique candidate identities.
The synthetic UI canaries differ from the fixture's source prompts; intentional
source-prompt persistence is unchanged.

Validation:

- Eight new focused tests passed in 5.781 seconds, zero failures/errors/skips.
- Existing attribution, recovery-boundary, exact failure/retry, project, policy
  prompt-edit, persistence, CLI, and public-security regressions: 222 passed in
  65.726 seconds, zero failures/errors/skips.
- Complete offline suite: 1,078 passed in 312.832 seconds, zero failures/errors/skips.
- `compileall app.py nano_batch tests`, actual observer begin/arm/poll Node syntax,
  and whitespace/UTF-8 checks passed. AST comparison confirms only the two adapter
  functions changed; the observer JavaScript and runner/state/download/CLI
  implementations are unchanged. No existing test was removed or weakened.

The raw DOM still exists and is read transiently for detection. This is a boundary
for qualified failure output, not a sanitizer for every transport exception or
historical saved error. Older runtime errors are not retroactively rewritten;
private source prompts and runtime data must still remain private. Live Flow
acceptance was not performed. The separate filesystem, latency, and diagnostic
label findings were not addressed.

Only the public checkout and synthetic offline fixtures were used. The private
original, private runtime/profile data, real Chrome/CDP/Flow sessions, and external
APIs were not accessed. No files were staged, no commit or push was made, and the
published repository remains unchanged. This local correction stops for review.

## Post-publication P2 correction — early assets confinement (2026-10-14)

The developer authorized this narrow correction and offline regressions after
reviewing the audit finding. Codex verified the public checkout clean on `main`
at `56ca47d17872cb51f43000fc7f09191c4a1c9f27` and implemented the change under
that direction. The P1 adapter/privacy patch, earlier developer-written validator,
state codec, generation behavior, and previous Git history are unchanged.

The existing pending-download contract defines the resolved run directory as the
trusted root, with literal `assets/<canonical filename>` and
`assets/.staging-<suffix>/v<variant>.download` locations. Resolving an alias of the
run root is supported; redirects of assets, canonical names, and staging paths
are not, including redirects to another location inside the run. Supported legacy
ordinary downloads use that same assets layout without gaining automatic pending
reconciliation when project evidence is absent.

The original helper instead trusted the resolved assets directory as its own root.
Before production edits, the new regression used the real local allocator/state
writes and a synthetic adapter. It failed with `(download attempts, adapter calls)`
equal to `(1, 1)` rather than `(0, 0)` in 0.207 seconds: rejection occurred after
the demonstrated side effects. That regression and its assertions were retained.

`downloads.py` now resolves existing ancestry strictly, permits genuinely missing
suffixes, rejects dangling links/resolution errors/disagreement, and requires the
exact assets, canonical, and staging locations. Ordinary planning validates all
candidate extensions and staging destinations before mutation. Its transient plan
chooses a UUID hex staging suffix without creating a directory; execution rechecks
paths before accounting and creates that exact directory exclusively, with private
POSIX permissions. An existing name is rejected without reuse or a recovery loop.
The suffix remains compatible with the existing receipt layout and old receipts.

The staging context manager preserves its original unplanned helper interface and
cancellation tests. Production supplies the prevalidated name. Normal cleanup
uses the existing cancellation wrapper; cancellation, pending receipts, and
disputed ownership retain staging without a GC finalizer deleting it. No pending
ownership/content check, global file-claim guard, canonical no-overwrite check, or
atomic state/pending-completion boundary was removed. An existing simulated pending
redirection regression exposed the need to compare whole-path and strict ancestor
resolution; it passes unchanged with that conservative consistency check.

An intermediate 203-test run on the earlier helper revision reported three
existing staging-interface errors. The original unplanned interface and cleanup
hooks were restored without editing those tests; all nine post-generation
cancellation tests then passed. Final review also added a missing-file staging
claim regression: it failed before the extra guard with allocation already
entered (one failure, zero errors/skips, 0.270 seconds). Passing the planned
staging destination to the existing global file-claim guard at planning and
pre-accounting boundaries corrects this case without removing its later checks.

The new synthetic tests cover ordinary project-owned and projectless legacy
success, idempotent Resume, missing assets without planning writes, assets and
canonical/staging escapes, alternate extensions, conflicting in-run aliases,
supported run-root aliases, occupied staging names, dangling links, and resolution
errors. Rejection checks protect state bytes and counters, UUID/ownership metadata,
existing files and unrelated assets, and prohibit allocation/save/install/adapter
calls. Native Windows junction creation was denied by the sandbox; the same safe
fixture tests succeeded outside it, entirely within ignored public-checkout scratch
directories. At least the assets-escape case explicitly requires and verifies an
actual Windows junction. Portable resolution simulations remain distinct evidence;
these checks do not establish every Windows reparse-point or file-symlink variant.

README and the validator guide now describe the corrected boundary and planned
staging suffix, while retaining filesystem-race, single-writer, non-atomic copy,
staging-leftover, and live-acceptance limitations. Earlier historical log entries
remain verbatim. Filesystem changes after validation remain possible; this is
defense in depth, not handle-based confinement or a concurrent-writer transaction.

The final focused confinement, ordinary ownership, pending ownership/recovery, and
post-generation cancellation suite passed 99 tests in 28.587 seconds, zero
failures/errors/skips. It includes 18 new confinement regressions. An earlier full
1,095-test snapshot passed in 318.033 seconds; the additional early staging-claim
case and final production guard require the fresh full-suite result recorded below.

Final complete offline suite on the finished production/test changes: 1,096 passed
in 318.292 seconds, zero failures, errors, or skips. This includes legacy/Resume,
state persistence, pending receipts, ordinary global ownership, generation/retry,
P1 privacy, cancellation, launcher/CLI, Node observer, and publication checks.
Four standalone publication-security checks, `compileall app.py nano_batch tests`,
tracked/untracked UTF-8 and whitespace checks, and `git diff --check` passed.
AST inspection limits production function edits to the path helpers and ordinary
planning/staging/execution functions; existing pending validators/installation
checks and all old test files remain unchanged. No embedded JavaScript changed.

The final inventory is two source files, one new 18-test module, README, the
validator guide, and this appended log. Only the public checkout and synthetic
scratch fixtures were used; launcher/browser behavior in tests was mocked. No
private original, authenticated profile, actual production run, Chrome/CDP/Flow
session, or external API was accessed. Nothing was staged, committed, or pushed.
Branch remains `main` at the P1 commit above. Work stops for developer review.

## P2 follow-up — Windows staging cleanup compatibility (2026-10-14)

The final pre-commit review blocked the P2 commit after Codex reproduced a
regression in the planned-directory cleanup it had introduced. A valid read-only
JPEG could be committed with persisted candidate completion, but direct
`shutil.rmtree()` raised `PermissionError` and prevented Order finalization. The
earlier 1,096-test result remains evidence for that snapshot, not coverage of this
missing case. No P2 commit was created. The developer then authorized this narrow
cleanup correction, preserving the early confinement design and deferring the
separate copy-fallback backlog. Earlier direct developer contributions and their
attribution are unchanged.

Before production changes for this follow-up, two new native Windows regressions
failed (two failures, zero errors/skips, 0.545 seconds): standalone read-only JPEG
cleanup and full ordinary-download finalization. The latter confirmed a valid
canonical image and completed candidate while the Order download remained running.
Both assertions were retained. They passed after correction in 0.762 seconds.

Codex inspected the installed Python 3.12 `TemporaryDirectory` implementation. Its
permission-error handler clears read-only permissions and retries deletion; bare
`rmtree()` lacked that behavior. Blindly repairing a staged hard link after commit
could also change the canonical file's attributes. The correction therefore clears
Windows read-only on a verified regular, single-link staging file before pending
receipt persistence and canonical installation. It changes no image bytes, saved
integrity metadata, candidate identity, retry budget, or canonical commit strategy.

Cleanup retains standard-library walking/deletion, rather than adding a recursive
deletion framework. It checks the allocated directory's device/inode identity,
exact resolved paths, and entries without following links; reparse points and
unsafe shared-file permission repair fail closed. The planned-directory handler
retries only a verified read-only regular-file unlink, once, after checking sole
link ownership. Other permission/ACL errors and a failed retry propagate. No
unconditional `ignore_errors` or catch-all suppression was added. The historical
unplanned `TemporaryDirectory.cleanup()` hook is retained behind the preflight;
shared files are rejected before that hook could repair their permissions.

The temporary-directory GC finalizer is detached before explicit cleanup decisions,
so it cannot bypass a refusal or erase cancelled, pending, or disputed evidence.
Planned paths still validate before exclusive allocation; ordinary planning's
zero-attempt/zero-adapter/no-state-write rejection remains intact. The existing
pending validators, global claims, canonical no-overwrite/content checks, and
state/pending-completion boundaries are preserved. No production module besides
`runner.py` changed in this follow-up; the earlier P2 `downloads.py`, README, and
validator-guide edits remain unchanged.

The regression module now contains 34 tests: the original 18 confinement tests and
16 cleanup/lifecycle additions. Coverage includes project-owned and legacy
read-only success, idempotent Resume without another attempt, unchanged canonical
attributes/bytes, cancellation and pending/disputed retention, replaced roots,
nested native Windows junctions, real shared hard links, simulated file aliases,
GC refusal protection, and bounded genuine deletion-error reporting. Read-only
behavior, hard links, and directory junctions were exercised natively on Windows;
file-alias and persistent ACL-error injections are simulations, not native ACL or
file-symlink acceptance. All fixtures stay in ignored public-checkout scratch space.

Final focused cleanup/confinement, ordinary and pending ownership/recovery,
interrupted-commit and cancellation suite: 145 passed in 34.558 seconds, zero
failures/errors/skips. Full-suite and final publication verification follow below.

Directory identities and path/attribute checks are snapshots, not filesystem locks.
Mutation after validation remains a TOCTOU limitation. Unknown/reparse-point or
shared-file cases fail closed, and genuine cleanup failures may retain staging.
Other filesystems/storage providers and real Flow acceptance are not established
by these tests. The single-writer assumption and non-atomic copy-fallback limitation
remain; that backlog was not changed.

Final complete offline suite: 1,112 passed in 308.257 seconds, zero failures,
errors, or skips. Four standalone publication-security checks, Python compilation,
tracked/untracked UTF-8 and whitespace checks, and `git diff --check` passed.
Production/test content hashes match the full-suite snapshot. Existing tracked
tests, the P1 privacy patch, the developer's validator, state models/codecs, and
earlier engineering history are preserved. No embedded JavaScript changed.

The cumulative P2 inventory remains five tracked modifications and one untracked
test file; this follow-up changes only runner, that test module, and this appended
log. Branch remains `main` at `56ca47d17872cb51f43000fc7f09191c4a1c9f27`, with
seven existing commits and nothing staged. No private original/runtime data,
authenticated profile, real Chrome/CDP/Flow session, or external API was accessed.
No Git commit or push was made. Work stops for the final commit review.

## P2 correction — remove direct-to-canonical copy fallback (2026-10-14)

The developer accepted Codex's read-only design review and authorized this
implementation, offline tests, and documentation, without a commit or push.
Codex verified the clean public checkout on `main` at
`173d4b2655aa3ef3d42ae0cd38dca0a5c2c2ea82`, with eight existing commits.
The earlier developer-written validator and its engineering attribution remain
unchanged; this section records Codex's implementation under the approved design.

The design probe forced hard-link failure, interrupted the real fallback after
50 bytes, and found a partial canonical file beside a valid 632-byte staging JPEG
and durable pending receipt. Resume correctly refused the partial canonical
without state writes. Exclusive canonical creation prevented overwrites but did
not prevent partial visibility; `KeyboardInterrupt` bypassed `except Exception`,
and process termination cannot rely on Python cleanup at all.

Before production edits, three new desired-behavior regressions failed in 1.357
seconds (three failures, zero errors/skips): no partial canonical on interruption,
flush/closed-handle/rename ordering, and rejection of unsafe non-Windows fallback.
Their assertions were retained. The initial 15-test implementation suite passed
in 5.506 seconds after the installer correction. An expanded fixture run had one
setup error because it attempted to remove existing project ownership; the fixture
was corrected to create a separate synthetic legacy run, preserving that guard.
The subsequent 196-test focused suite passed in 48.119 seconds.

`commit_staged_file()` preserves successful hard-link installation and the existing
explicit-overwrite branch. Its fallback now requires Windows and matching source
and destination-parent device IDs, opens the existing staging file with `r+b`
without truncation, flushes/fsyncs it, closes that handle, then calls `os.rename()`.
Windows CPython uses `MoveFileExW` without replacement or cross-volume-copy flags.
The installer no longer opens canonical with `xb`, copies into it, or unlinks it
after failure. Non-Windows hard-link failures and flush/publication failures
propagate. No chmod, copy-enabled move, new retry loop, or new temporary file was
introduced. The device comparison is a snapshot, not a filesystem lock.

An additional integration probe found that existing legacy cleanup could erase
staging after publication failed because those candidates have no receipt. A
retained regression reproduced that gap (one failure, zero errors/skips, 0.318
seconds). A transient flag around the existing installer call now retains staging
when installation raises, including legacy candidates and latched cancellation.
Success clears the flag before normal validation/finalization and cleanup. Legacy
failed-state accounting and lack of automatic orphan-adoption rights are unchanged.
This narrow addition changes only `execute_download_one()` in the runner. A full
suite started before this extra regression was superseded; it does not validate
the final snapshot. Fresh final verification is recorded below.

Current focused coverage contains 22 new tests: 13 portable simulations and nine
native Windows tests. Simulated rename uses exclusive creation only for disposable
fixtures; it is explicitly not evidence of native atomicity. Native tests exercise
hard-link success, forced-link-failure followed by real rename, complete ordinary
finalization/cleanup, existing read-only browser-download compatibility, read-only
pending refusal, shared-hardlink attribute protection, post-rename interruption
and canonical-only recovery, late destination collision, and delete-sharing denial.
Simulations cover flush/unsupported/cross-volume/unexpected errors, non-Windows
rejection, explicit overwrite compatibility, durable receipts, legacy retention,
and idempotent Resume without another download/attempt. Existing historical-partial
and filename-only orphan guards remain unchanged and are included in verification.

The fresh ordinary pipeline already normalizes verified single-link read-only
staging before receipt/install, so that successful behavior remains intact.
Historical read-only staging with unavailable hard linking now fails closed when
the fallback cannot open a writable flush handle. Native tests preserve its
bytes, attributes, receipt, and counters; restoring this disposable fixture's
permissions permits recovery without a new download. The installer never changes
permissions to obtain access, including on a shared hard link. Unknown providers,
ACLs, and sharing restrictions may similarly prevent publication.

The existing staging filename and receipt schema are reused. Rename may consume
staging; the existing canonical-only receipt recovery handles interruption before
candidate completion persistence, followed by unchanged Order finalization.
Completion and pending-record removal still share one atomic state replacement.
Global file claims, UUID identity, path confinement, expected content metadata,
retry budgets, and cancellation guards are preserved. Old partial canonical files
remain rejected and protected; this change grants no right to delete or overwrite
them. Retained legacy staging lacks automatic reconciliation evidence.

Rejected alternatives were direct copying/copy-enabled moves, check-then-replace
or `os.replace()` for ordinary installation, cleanup-only exception handling,
redundant destination-side temporary copies, and a broader transactional-filesystem
design. README and the validator guide now describe the revised installation and
permission limitations; historical log entries remain verbatim.

This removes the application's direct-copy partial-canonical window. It does not
prove universal namespace atomicity or power-loss durability across NTFS filters,
cloud/network providers, FAT/exFAT/ReFS/SMB, or hardware caches. Native evidence was
collected on the fixture's local Windows filesystem, with forced hard-link errors
distinguished from real rename/permission behavior. Cross-volume failures are
simulated; no other volume or private folder was accessed. Existing single-writer,
TOCTOU, physical-writer ambiguity, and staging-leftover limitations remain. Real
Chrome/Flow acceptance and actual power-loss tests were not performed.

Final focused suite: 197 passed in 48.083 seconds, zero failures, errors, or skips,
including all 22 new tests and existing ordinary/pending ownership, Resume,
confinement, cleanup, cancellation, legacy, and P1 privacy regressions. The fresh
complete offline suite passed 1,134 tests in 344.040 seconds, zero failures,
errors, or skips. Production and new-test SHA-256 snapshots remained unchanged
throughout that run. The earlier superseded run's snapshot guard rejected its
result after the additional legacy regression changed the tested files; it is
not counted as final verification.

Four standalone publication-security checks, Python compilation, tracked/untracked
UTF-8 and whitespace inspection, and `git diff --check` passed. All existing
tracked tests and earlier engineering-log content are preserved. Only the two
installer/caller functions above changed in production; no embedded JavaScript,
browser adapter, ownership validator, state codec, or dependency changed.
The final inventory is five tracked modifications and one new test file. No
credentials, private runtime data, or generated assets are commit-eligible.
Branch remains `main` at the verified eight-commit baseline, with nothing staged.
Only the public checkout, attached instructions, and synthetic offline fixtures
were used. The private original and real Chrome/CDP/Flow were not accessed; no
commit, push, or publication was performed. Work stops for developer review.

## P3 correction — distinguish ambiguous failure diagnostics (2026-10-14)

The developer authorized this diagnostic correction, regression-first offline
verification, and one local commit/publication only after all safety checks pass.
Codex verified the clean public checkout on `main` at
`fee4be146f6d26be6a361334e48b15b1c4db61f5`, synchronized with the local
`origin/main` tracking ref, and implemented this correction. Earlier direct
developer contributions and their recorded attribution are unchanged.

The defect was diagnostic, not a demonstrated retry-policy failure:
`_wait_for_generation_start()` caught every `FlowAutomationError`, including
`FlowFailureAttributionAmbiguous`, and unconditionally recorded `explicit_failure`
with `exact_explicit_failure_detected=true`. The recorder consequently emitted an
exact-failure timestamp even though the original ambiguity exception still stopped
the attempt and required explicit Retry. Raw poll metadata could separately claim
an exact failure when qualified items coexisted with an ambiguity flag.

Before production edits, eight new tests produced six failing assertions/subtests
in 1.927 seconds, zero errors/skips. These reproduced remounted historical-card
mislabeling with debug off/on, persisted mislabeling, missing poll metadata, and
mixed qualified/ambiguous evidence. Assertions were retained. All eight tests
passed after correction in 4.528 seconds. Submission assertions use the existing
adapter checkpoint and persisted failed-state fields; no state schema change was
required.

Only two adapter methods changed. The Python reader reuses the observer's existing
ambiguity flag to suppress the raw poll's exact-failure Boolean. The ACK exception
handler distinguishes the existing ambiguity subclass and records
`ack_signal="failure_attribution_ambiguous"` with that Boolean false, then reraises
the same exception. The existing recorder omits `explicit_failure_ms` naturally;
no new marker, state field, observer pass, browser/CDP call, polling, navigation,
submission, or retry was added. Successful ACKs and confirmed exact failures keep
their prior labels. Qualified unknown current-attempt failures keep their existing
explicit-failure diagnostic semantics and fixed privacy-safe error text; they are
not promoted to a named failure classification or an automatic retry budget.

Consumer review covered the recorder's bounded state trace/first-signal marks,
snapshot and terminal formatters, generation-failure observation serialization,
persisted Order errors/result details, and CLI diagnostic rendering. Repository
consumers display/store the signal string without a closed enum or retry decision
based on it. Keys, types, trace limits, and
`nano-banana-post-start-ack-timing/v1` remain unchanged. This is an additional
bounded signal value; external consumers that assumed a closed list must accept
it. README documents this consequence and that terminal-ambiguity `ack_ms` marks
the wait stopping, not proof of a successful submission acknowledgement.

The eight regressions exercise the real observer scripts through the existing
offline Node DOM harness where relevant, plus controlled mixed/lost-observation
payloads. They cover exact known failures, privacy-filtered unknown failures,
unchanged historical/stale cards, successful progress ACK, raw poll and timing
trace consistency, persisted diagnostics/preview output, debug off/on, consumed
submission accounting, blocked ordinary Resume, and explicit manual Retry without
resetting project ownership or automatic budgets. Direct ACK cases verify the
same single observer read and no downstream polls/actions after terminal failure.

The embedded observer JavaScript is unchanged. Source inspection limits production
edits to those two Python methods; observer provenance, failure classification,
exactly-once Start, retry/accounting, and the P1 privacy sanitizer remain intact.
All three observer scripts passed standalone Node syntax checks via stdin. Native
browser behavior and current real Flow acceptance are not established by these
synthetic tests; existing observer/ACK latency and filesystem limitations remain
outside this diagnostic correction.

Final focused acknowledgement/attribution, retry, cancellation, diagnostic,
privacy, and CLI suite: 323 passed in 59.819 seconds, zero failures, errors, or
skips. The complete offline suite passed 1,142 tests in 315.679 seconds, zero
failures, errors, or skips. Production/new-test SHA-256 snapshots remained
unchanged during the full run. Four standalone publication-security checks,
Python compilation, relevant Node syntax checks, UTF-8/privacy review, and Git
whitespace checks passed. No existing tracked test was edited or weakened.

The reviewed inventory is the adapter, one new eight-test module, six README
clarification lines, and this appended record. Earlier engineering history is
preserved verbatim. Implementation/verification used public source and disposable
synthetic fixtures; the private original, runtime assets, authenticated Chrome,
and real Flow sessions were not accessed. Pre-publication checks verified the
GitHub account `Amarsan-T` and unchanged remote baseline. The authorized single
commit and normal push must still pass staged-content review and a fresh fetch
without unexpected remote changes; their final SHA/status are reported separately.

## Callback-local observer text reuse (2026-10-14)

The developer reviewed the read-only performance investigation and authorized one
correctness-first optimization, with one commit and normal publication only if
equivalence, operation-count, regression, and privacy gates pass. Codex implemented
the change in the sanitized public checkout at
`37d9a0301a1722dd73f15893981183a0a9c055f9`. Earlier direct developer contributions
and their historical attribution are preserved.

Inspection of all callback paths found native DOM reads and observer bookkeeping,
with no DOM text/attribute writes, event dispatch, application callbacks, awaits,
or other yielding work. Under the existing standard DOM API contract, JavaScript
runs this callback synchronously against the already-applied DOM mutations; other
page script and mutation callbacks cannot interleave. Text arrays are consumed
read-only. The optimization does not support side-effecting replacements of
native DOM getters, nor establish untested live Flow behavior.

Only the JavaScript installed by
`_begin_current_generation_attempt_observation()` changes. `normalizedTexts()`
reuses its already-filtered array, including its independent `ariaLabel` channel,
from an element-keyed WeakMap during the active mutation callback. The callback
creates a new map inside `try` and clears the closure reference in `finally`,
including exceptional exits. It is null outside the callback and cannot be reused
by baseline capture, polling, direct helper reads, later callbacks, re-arms, or
replacement. Python cancellation adds no cleanup RPC: an already-running browser
callback owns its map only until that callback exits or its realm is discarded.
This does not promise immediate interruption or garbage collection.

No visibility, geometry, mutation provenance, historical evidence, or
classification decisions are cached. Record order, every record, all qualification
steps, and existing evidence limits remain intact. Duplicate visibility reads
remain for separate review. No RPC, poll, retry, navigation, Start action,
diagnostic field/signal, persistent schema, candidate ownership, download, or
filesystem behavior changes.

Regression-first evidence: the initial 13-test module produced nine failing
assertions/subtests in 14.091 seconds, with zero errors/skips, before production
edits. The failures demonstrated repeated computation and missing callback-local
reuse. The production correction then passed all 13 tests. Additional assertions
strengthened coverage without removing the original performance assertions.

The differential harness freezes the original normalization and callback blocks
from the baseline above, keeping the other observer logic shared. Before edits,
those blocks reproduced the complete installed script byte-for-byte. After edits,
restoring them and removing the unused cache declaration again reproduces the
original script exactly. Only that one Python method's embedded JavaScript changed.

Fixtures compare failure/ambiguity outputs, historical signatures, per-element
baselines, mutation sources, retained ordering, added-root and progress provenance,
local resets, truncation and classification counters. They cover nested/overlapping
records, historical/remounted/detached cards, identical wording, progress
transitions, independent labels, visibility changes, all exact classes, unknown
failure privacy, fresh polling and separate callbacks, normal replacement/cleanup,
and exception cleanup. Existing suites also exercise submission/candidate
ownership, explicit Retry, automatic budgets, cancellation, and persisted ambiguity.

Paired benchmarks use Node.js v24.20.0, synthetic fixtures only, one warm-up per
version and three measured samples per version in alternating order. Setup and
Python/process transport are excluded. Timing runs use uninstrumented production
blocks; separate counter runs instrument requests, actual computations, getter
character totals, and temporary cache entries. Both versions must produce equal
attribution snapshots. Counts below distinguish calls requesting normalized text
(which remain unchanged) from computations avoided through reuse.

| Fixture | Computations before -> after (unique elements) | Aggregate characters before -> after | Callback median ms before -> after |
| --- | --- | --- | --- |
| Flat insertion, 1,000 descendants | 3,002 -> 1,001 (1,001) | 67,264,998 -> 132,999 | 361.466 -> 4.444 |
| Flat insertion, 2,000 descendants | 6,002 -> 2,001 (2,001) | 268,529,998 -> 265,999 | 1,305.035 -> 12.172 |
| Deep insertion, 500 descendants | 6,447 -> 501 (501) | 109,502,641 -> 8,424,749 | 601.660 -> 64.686 |
| Deep insertion, 500, overlapping records | 12,893 -> 501 (501) | 218,971,783 -> 8,424,749 | 1,487.685 -> 94.920 |
| Deep insertion, 1,000 descendants | 12,947 -> 1,001 (1,001) | 436,774,391 -> 33,599,499 | 3,520.493 -> 333.726 |
| 100 incremental records, 2,000-deep fixture | 1,200 -> 12 (12) | 131,400 -> 1,314 | 11.475 -> 8.675 |
| 100 historical cards, debug off | 400 -> 201 (201) | 1,905,100 -> 55,299 | 19.076 -> 2.048 |
| 100 current cards, debug off | 500 -> 201 (201) | 1,923,500 -> 55,299 | 23.469 -> 2.956 |
| 100 historical cards, debug on | 400 -> 201 (201) | 1,905,100 -> 55,299 | 26.325 -> 2.590 |
| 100 current cards, debug on | 500 -> 201 (201) | 1,923,500 -> 55,299 | 25.101 -> 2.606 |

Cached aggregate strings are prepared outside the insertion measurements;
incremental/card cases use iterative getters. This is a synthetic model, not
Chromium's text/layout implementation. Timing noise remains: one current-card
sample was 46.687 ms after optimization. Operation counts and equivalence, rather
than speedup ratios, are the acceptance evidence. Style/geometry and selector work
are unchanged. Historical cards produced no failures; current cards produced the
same deduplicated exact failure as the reference.

Temporary cache peaks were 2,001 entries / 62,000 logical stored string characters
for flat 2,000, 501 / 22,459 for deep 500, 12 / 1,306 for incremental records, and
201 / 18,400 for card cases. Each stored channel obeyed the existing 1,200-character
filter. These totals are not heap-byte measurements; map/array overhead is
additional. Cache size grows with elements visited in one callback, with no new
arbitrary evidence cap. Every measured callback released its cache reference.

There is no benefit where reuse is absent. Deep baseline capture retained 1,001
computations and 33,599,499 aggregate characters in both versions
(206.956 -> 209.434 ms); it allocated no callback cache. A single irrelevant
attribute mutation retained two computations and 32 characters
(0.022 -> 0.066 ms), with a two-entry cache and no hits. Deep distinct-ancestor
aggregation, baseline cost, selector work, duplicate visibility reads, and
large-callback temporary memory remain limitations. No browser/CDP wall time or
live Flow speedup was measured. This does not identify or resolve the previously
recorded 6-8-second Flow main-thread long task.

The public test helper supports reproduction, for example from the repository
root with Node available and tests on PYTHONPATH:

`python -B -c "from tests.test_flow_observer_text_reuse import probe,bulk_fixture; print(probe(benchmark=True, **bulk_fixture(500,deep=True,overlap=True)))"`

Final focused acknowledgement/attribution, retry, cancellation, diagnostics,
privacy, and CLI suite: 336 passed in 76.185 seconds. Full offline suite: 1,155
passed in 331.940 seconds. Both had zero failures, errors, or skips; the full run
also had zero expected failures or unexpected successes, and source/test hashes
remained unchanged throughout. Afterward, only documentation and one trailing
blank line in the new test changed. That test's AST was verified identical, and
the final 13 tests plus four publication-security checks passed together: 17 in
15.700 seconds, zero failures, errors, or skips. No existing test was weakened.

Python compilation and syntax checks for the three production observer scripts
plus the uncached reference passed. Final whitespace and privacy checks include
the new test file. README's latency note now acknowledges the callback-only
optimization while preserving the baseline and real-Flow limitations. Earlier
engineering-log text remains unchanged. Only public source and synthetic fixtures
were used; no private original, runtime data, authenticated Chrome, or real Flow
was accessed.

Publication remains conditional on the final staged inventory/privacy review,
unchanged remote baseline, and fast-forward ancestry. The authorized single commit
and normal push are verified and reported separately with their final SHA.

## Remove duplicate observer visibility reads (2026-10-14)

The developer authorized a separate, narrowly scoped visibility optimization after
reviewing the callback-local text reuse work. Codex verified the sanitized public
checkout on main at `ec335f29144736ad1c63fcfe3b8c5bc077aae7f2`, initially clean
and matching its origin/main tracking reference, and implemented this correction.
Earlier direct developer contributions and historical attribution are unchanged.

Call-site inspection found only two production calls to `state.detectFailureIn()`:
the debug and ordinary branches of `scanFailureContainers()`. Each follows the
same element's successful visibility check in the same synchronous stack. Between
that check and detection there are only a counter increment and optional numeric
timing bookkeeping. There is no DOM write, event dispatch, application callback,
await, or yielding operation. Under the existing standard native DOM API contract,
other page script and mutation callbacks cannot interleave.

The detection body is now a private `detectFailureIn(element, visibilityVerified)`
helper. Only the scan passes true, immediately after its fresh visibility check.
The existing `state.detectFailureIn(element)` entry point forwards no proof and
always performs its own visibility check; extra caller arguments cannot bypass
it. The helper still checks element type and connectivity, including the second
connectivity check after scan visibility. Nothing stores visibility across polls,
callbacks, attempts, replacement, or asynchronous boundaries.

Visibility keeps its exact prior meaning: display is not none, visibility is not
hidden, and both rectangle dimensions are positive. It does not test viewport
intersection, occlusion, or hit-test ownership. Those existing observer semantics
are preserved, without altering stricter actionability checks elsewhere.
Historical attribution, independent label channels, all exact classifications,
ambiguity and privacy protections remain unchanged. The preceding text cache,
baseline capture, retry budgets, submission/candidate ownership, navigation,
downloads, filesystem behavior, and state schemas are untouched.

Before production edits, 12 new tests plus the updated slow-geometry regression
ran 13 tests in 13.947 seconds, with four failing assertions/subtests and zero
errors/skips. They reproduced 400 style/geometry reads for 200 visible containers
and the old two-read diagnostic expectation. After implementation, all 33 new
visibility/existing observer-read timing tests passed in 15.024 seconds. Additional
zero-width/height coverage was added before the final verification runs.

Differential fixtures restore the original visibility guard/calls from the
baseline while retaining the same text-cache and attribution implementation.
Restoration reproduces the entire original installed JavaScript exactly.
Comparisons cover failure/ambiguity outputs, historical signatures, per-element
baselines, mutation sources, retained order, added-root/progress provenance,
counters, hidden/revealed/zero-geometry/detached/remounted elements, nested exact
failures, incomplete and unknown messages, all exact classes, independent direct
calls, re-arm, cleanup, replacement, and debug off/on. A synthetic rectangle
getter that detaches its element verifies that the retained connectivity recheck
still rejects it. No change relies on a new visibility cache.

For 200 connected visible retained containers, both debug modes now perform 200
style reads and 200 geometry reads, instead of 400 each. Hidden retained containers
still perform 200 of each; detached containers perform zero. Independent calls for
200 visible elements still perform 200 of each. Text computation, selectors,
connectivity reads, qualification results and semantic counters match the reference.

Diagnostic keys, limits and stage names are unchanged. The style_read,
geometry_read and visibility_test stage call totals report the actual single
check instead of two for visible containers. Inner failure_detect time no longer
includes a duplicate visibility read; the scan's original check remains measured.
The existing slow-geometry test therefore expects one charged read and 7,000
synthetic browser-clock ms instead of two and 14,000. Its slow-stage dominance
assertion remains intact. Those charged values are not real execution timings.

Paired Node.js v24.20.0 measurements used five warm-up runs per version and ten
measured runs per version, alternating order. Only the original poll or direct
calls were timed, excluding DOM setup, fixture snapshot construction, Python
transport and process startup. The existing harness's warm-up/sample loops were
adjusted only in memory. Separate counter runs verified equivalent snapshots.

| 200-container case | Style reads before -> after | Geometry reads before -> after | Median JS ms before -> after |
| --- | --- | --- | --- |
| Current visible, debug off | 400 -> 200 | 400 -> 200 | 2.211 -> 2.173 |
| Historical visible, debug off | 400 -> 200 | 400 -> 200 | 0.688 -> 0.716 |
| Current visible, debug on | 400 -> 200 | 400 -> 200 | 3.349 -> 3.247 |
| Historical visible, debug on | 400 -> 200 | 400 -> 200 | 1.922 -> 1.511 |
| Hidden historical, debug off | 200 -> 200 | 200 -> 200 | 0.027 -> 0.027 |
| Hidden historical, debug on | 200 -> 200 | 200 -> 200 | 0.368 -> 0.396 |
| Detached, debug off | 0 -> 0 | 0 -> 0 | 0.019 -> 0.019 |
| Detached, debug on | 0 -> 0 | 0 -> 0 | 0.342 -> 0.230 |
| Independent direct calls, debug off | 200 -> 200 | 200 -> 200 | 1.514 -> 1.621 |
| Independent direct calls, debug on | 200 -> 200 | 200 -> 200 | 2.182 -> 2.312 |

Empty polls made zero style/geometry reads in both versions: 0.003 -> 0.003 ms
with debug off and 0.011 -> 0.012 ms with debug on. Timing ranges overlapped and
some medians increased; the proven benefit is elimination of redundant API calls,
not a reliable wall-clock speedup with cheap synthetic getters. No browser layout,
CDP round trip, or live Flow performance was measured. This neither establishes
fewer forced layouts nor explains or resolves the previously observed 6-8-second
Flow main-thread task. Baseline/ancestor text aggregation and other existing
latency limitations remain.

The new offline helper supports a quick paired probe with the default one warm-up
and three samples: `probe(benchmark=True, **cards_fixture())` from
`tests.test_flow_observer_visibility`. The five/ten protocol above uses the same
fixtures and production scripts with only those benchmark loop counts changed.

Final focused observer/attribution, privacy/ambiguity, lifecycle/cancellation,
retry, diagnostics and CLI suite: 348 passed in 97.552 seconds. Complete offline
suite: 1,167 passed in 353.485 seconds. Both had zero failures, errors or skips;
the full suite also had zero expected failures/unexpected successes. The adapter,
new regression module and modified timing-test SHA-256 snapshots stayed unchanged
throughout the full run. No source or test changes followed that verification.

Four publication-security checks, Python compilation, syntax checks for all three
production observer scripts plus the original-visibility reference, whitespace,
full-diff and privacy review passed. The reviewed scope is the adapter, one new
12-test module, two intended geometry-count/charged-clock assertion updates, and
this appended log entry. Existing test assertions were otherwise preserved.
README remains accurate and unchanged; previous engineering history is preserved
byte-for-byte. Only public source and synthetic fixtures were used, without
accessing the private original, runtime assets, authenticated Chrome or real Flow.

Publication is conditional on exact staged-content review and a fresh fetch with
unchanged remote baseline and fast-forward ancestry. The single authorized commit
and normal push are verified separately and reported with their final SHA/status.

## Read-only staging inventory (2026-10-15)

After the staging-lifecycle audit, the developer approved a metadata-first,
strictly read-only inventory and explicitly deferred cleanup/deletion. Codex
implemented the isolated inventory, thin CLI integration, synthetic regressions,
and documentation under that direction. Earlier direct developer contributions,
the collaboratively developed ownership validator, and historical provenance
limitations remain unchanged. No existing download/recovery implementation was
modified for this feature.

`inspect-staging --run-dir <directory>` requires one explicit root. It neither
discovers runs nor loads prompt files/configuration or initializes browser/API
connections. Its JSON report uses report-local opaque IDs, bounded numeric
Order/variant references, fixed status/issue vocabulary, and overlapping labels:
A receipt reference; B potentially needed interruption evidence; C disputed or
ambiguous evidence; D a metadata-supported file-identity relationship to a
completed canonical record; E apparently orphaned with ownership unproven.
There is no disposable/safe-to-delete category. Canonical presence is metadata
only, and even matching file sizes cannot validate bytes. Independent Batch/source
identity and image integrity remain responsibilities of normal Resume.

This tool does not delete files. Results are not cleanup authorization.
Apparently orphaned does not mean disposable. Recovery eligibility remains
determined by normal Resume. The inventory never saves state, installs/adopts
assets, invokes recovery, changes permissions or ownership, allocates staging,
submits browser actions, or changes attempt/retry accounting. General-purpose
JSON parsing does read embedded prompts into memory; it is not described as
avoiding that read. No prompt, URL, original filename, absolute path, content
hash, credential, raw exception, or serialized state is returned or rendered.
Expected CLI argument failures and unexpected command failures have fixed safe
messages, including the root parser's extra-argument failure path.

Inspection caps state at 2 MiB and 2,048 Orders (two candidates/eight outputs per
Order), the assets scan at 512 entries, staging at 64 directories/64 immediate
entries each, canonical metadata at 256 references, and output owners at eight
per entry. Root ancestry is bounded to 64 components. Truncation and skipped
nested directories remain explicit; no arbitrary crawl, image decoding/hashing,
or filename/content-based orphan adoption occurs. Missing/corrupt/oversized state
does not prevent a bounded filesystem metadata report, but evidence stays
uncertain. Exit 0 means a complete metadata inspection, not safe ownership or
recoverability; exit 2 means invalid arguments or incomplete inspection.

Windows uses read-only native handles, opens reparse points themselves for
rejection, checks normalized final paths, and holds the validated directory
ancestry without delete sharing. A native competing-actor regression exposed
that attribute-only directory handles did not prevent rename. The test failed
with changed fixture metadata; requesting directory-list access corrected that
assumption and the original probe now passes without filesystem changes. The
state handle is checked against the original metadata before reading, including
Python 3.12's full volume serial and 128-bit file identity. Shared state files,
unknown reparse points, UNC/device roots, mapped network drives, short-name/other ambiguous aliases,
sharing denial, and unavailable required metadata fail closed. POSIX has a
descriptor-relative no-follow backend; it was reviewed but not natively exercised
in this Windows environment.

Handles can briefly deny concurrent rename/deletion or state replacement; this
command is intended for inactive runs. It does not establish inactivity, acquire
a run-writer lock, prevent file-content changes, or provide a global atomic
snapshot. Metadata rechecks identify observed changes but do not remove all TOCTOU
or storage-provider uncertainty. Access timestamps may change through OS/provider
behavior; program-directed state, asset bytes, permissions and attributes are
unchanged. Real ACL configurations, cloud/network providers, other filesystems,
and power-loss behavior are not certified by the local fixtures.

Regression-first: before production implementation, the 37 initial tests failed
42 assertions/subtests with zero errors/skips under native Windows execution.
An initial restricted run also encountered seven fixture-setup errors from native
filesystem restrictions; those are not counted as application defects. Four
additional focused checks reproduced missing canonical-size warnings, malformed
completed metadata, disappearing-entry status, and final canonical-disappearance
reporting before their corrections. Further retained checks caught a missing
completed-association flag and unsupported completed-format handling. No failing
assertion was removed or weakened to obtain a green run.

Final classifier review retained file claims from candidate metadata and Order
outputs even without a pending receipt. Two failing regressions reproduced the
initial false-orphan labels, then passed with explicit nonreceipt-claim ambiguity.
A native Windows case-alias regression also failed before deduplication and
reference-label correction. Observed noncanonical spelling is still rejected,
without reporting the same directory twice or losing its saved receipt reference.
Mapped network drives are rejected before directory opening. The documented CLI
uses Python's `-B` option to avoid interpreter bytecode-cache writes as well.

The inventory regression module contains 55 tests. Native Windows fixtures cover
junctions, real hard links, read-only files, file IDs, and denied directory
replacement. Unknown-reparse attributes, ACL/provider errors and concurrent
metadata changes are injected simulations. File-symlink coverage uses the native
operation where permitted and a separately identified metadata simulation when
privileges are unavailable. Tests guard state/recovery/browser/network and
low-level mutation entry points and compare all fixture state/media bytes,
permissions, attributes and relevant metadata before/after inspection; OS-managed
access times are excluded deliberately. Privacy canaries cover prompts, URLs,
filenames, malformed records, argument errors and provider exceptions. The last
standalone inventory run passed 55 tests in 3.398 seconds, zero failures/errors/skips.

A preliminary focused run passed 371 tests in 164.405 seconds before the last
four classifier/platform regressions and their corrections. It is not the final
snapshot verification; the frozen final-source runs are recorded below.

Final frozen-source focused suite: 375 passed in 166.678 seconds, including the
55 inventory tests, path confinement, ordinary/pending ownership, receipt codec
and recovery, Resume/finalization, cancellation/cleanup, CLI and four publication
security checks. Complete offline suite: 1,222 passed in 231.828 seconds. Both had
zero failures, errors, skips, expected failures or unexpected successes. The CLI,
inventory module and new test module SHA-256 snapshots stayed unchanged through
both runs. The earlier 371-test run is superseded as final evidence.

Python compilation passed. An initial compilation attempt used an overly nested
scratch cache prefix and hit Windows path-length errors; compilation succeeded
using standard ignored cache locations without source changes. CLI help passed
without browser/config initialization. A supplementary completed-candidate probe
confirmed that its stored digest and private paths never enter JSON output. AST
review found no mutation/generation/download calls in the inventory. Existing
download, runner, state, model, observer, AGENTS and LICENSE files are unchanged;
all pre-existing CLI functions other than the parser and dispatch setup are
AST-identical. Previous engineering-log content remains unchanged.

Final staged-content/privacy/whitespace review and a fresh fetch with unchanged
remote baseline gate the single authorized commit and normal push. Their final
SHA/publication status is reported separately. Only public source and disposable
synthetic runs were used; no private original, real run, profile, image/prompt
file, authenticated browser or Flow operation was accessed.
