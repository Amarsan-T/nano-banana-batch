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
