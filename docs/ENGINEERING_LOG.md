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
