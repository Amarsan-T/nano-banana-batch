# Stage 2.2: collaborative pending download ownership validator

This contract was prepared for the owner's direct Python implementation. The
owner saved an initial validator draft, then delegated completion to Codex.
`validate_pending_download_ownership()` and recovery integration are now installed.
Codex retained the draft's source/state selection, variant-matching loop,
non-unique-match rejection, explicit identity check, and unique ownership helper.
`ENGINEERING_LOG.md` distinguishes the saved draft, the owner's reported discussion
work and technical decisions, and Codex's completion, corrections, and testing.

## Location and interface

In `nano_batch/runner.py`, the function is immediately after
`_validate_recorded_download()` and before `plan_download_one()`.
The existing imports already provide `Batch`, `RunState`, and `Path`.

The signature is:

```python
def validate_pending_download_ownership(
    *,
    batch: Batch,
    state: RunState,
    order: int,
    variant: int,
    run_dir: Path,
) -> tuple[Path, Path]:
```

The `*` requires keyword arguments. Return `(canonical_path, staging_path)` as
resolved `Path` objects, or raise `ValueError` for insufficient/conflicting
evidence. Do not return `False`, `None`, or an eligibility flag instead.

This function is read-only: no state/file writes, counter increments, downloads,
generation, or completion updates. It validates logical ownership and paths;
Pillow decoding and comparing actual bytes with expected metadata belong to the
later recovery plumbing.

## Inputs and current types

- `batch`: independently validated source batch. `Batch.batch_id` is a string;
  `Batch.items` is a tuple of immutable `BatchItem` objects.
- `state`: authoritative `RunState` snapshot. Resume must supply freshly loaded
  state. `state.items` is a list of mutable `RunItemState` objects.
- `order` and `variant`: intended target from the caller, independently of the
  pending record. Locate exactly one corresponding Order and candidate.
- `run_dir`: trusted selected run root. Resolve it rather than taking a root from
  record content.

The selected `RunItemState` has `order`, `visual_id`, `status`, `completed_at`,
`attempt_count`, `download_attempt_count`, `download_status`, `download_error`,
`output_files`, and `candidates`.

The selected `CandidateRecord` has `variant`, `candidate_id`, `asset_url`,
`suggested_filename`, `download_status`, `download_error`, completed-file metadata,
and `pending_download_commit`.

`pending_download_commit` is either `None` or an immutable `PendingDownloadCommit`
dataclass. Its fields are defined in `nano_batch/models.py`. Annotations do not
enforce types at runtime. `pending.validate()` enforces field shape only: version,
strings, UUID/hash syntax, positive integer counters/sizes/dimensions, and supported
format/MIME pairing. It does not check contextual ownership, path safety, or files.

The authoritative project is `state.flow_project`, a `FlowProjectReference`, with
`project_id`, normalized `project_url`, and `created_for_batch`. Its `validate()`
method checks that reference. New automatic reconciliation requires this saved
project and `state.flow_project_required=True`; null legacy project evidence is
representable but insufficient for automatic reconciliation.

## Existing helpers to reuse

- `resolve_item(batch, order)` finds the intended source Order.
- `_matching_run_item(state, source_item)` checks source identity. It uses
  `RunItemState.matches_source()`, preserving the original `source_prompt` binding
  when the effective prompt has been edited after policy rejection.
- `_candidate_ownerships(state)` maps a UUID to a list of `(Order, variant)` pairs.
- `_assert_unique_download_ownership(state, item, candidate)` requires that list
  to equal exactly `[(item.order, candidate.variant)]`. Do not convert it to a set:
  duplicate entries must remain detectable.
- `_persisted_candidate_id(candidate)` supports a legacy URL-derived fallback.
  For this new validator require an explicit canonical candidate UUID. Where a
  recorded asset URL also supplies identity, it must not conflict.
- `canonical_extension_for_image_format()` returns `jpeg` for JPEG, `png` for PNG,
  and `webp` for WEBP.
- `output_basename()` derives the filename from authoritative Order/visual/variant.
- `deterministic_candidate_path()` now requires the literal assets child of the
  resolved run root and rejects redirected candidate names. The post-publication
  P2 correction strengthened this helper; the independent pending path checks
  below remain required as defense in depth.

## Suggested validation order

1. Establish the intended source/state Order independently of the pending record.
   Require matching batch identity and unique Order/candidate selection. Do not
   use the record's own `order` to choose the Order it is supposed to prove.
2. Require a typed, well-formed pending record and completed generation with a
   completion timestamp. Require running Order/candidate download state without
   conflicting errors, generation error code, completed candidate metadata, or
  premature registration of its canonical or staging path in `output_files`.
3. Validate saved project identity and compare it with the record. Missing project
   proof, including a legacy run with no project metadata, must fail closed.
4. Compare batch, Order, visual ID, variant, and explicit candidate UUID. Reuse the
   unique ownership check and reject conflicting URL-derived identity.
5. Compare both attempt counters with authoritative state. Counters must be
   positive actual integers; Python considers `2.0 == 2` true and `bool` is an
   integer subclass, so equality alone is insufficient.
6. Establish both expected paths under the trusted root. Return them only after
   every required identity, state, and path check succeeds. Globally examine other
   candidates' `local_path`/`local_relative_path`, every Order's `output_files`, and
   competing pending records. None may claim either file or the canonical stem
   under another extension. The selected candidate's own pending record is the
   legitimate claim; its completed-file metadata or premature output registration
   remains a conflict.

Canonical naming uses the expected byte format, not the candidate's initial
suggested extension. A `.png` suggestion with validated JPEG content and a
canonical `.jpeg` destination is legitimate.

New record paths use forward-slash serialization. Canonical layout is exactly
`assets/<derived filename>`. Staging layout is exactly
`assets/.staging-<nonempty safe suffix>/v<variant padded to two digits>.download`.
The suffix is recorded because it cannot be independently derived from the Order.
Historically `TemporaryDirectory` chose it; ordinary planning now chooses a UUID
hex suffix before exclusive directory creation so staging paths can be validated
before side effects. Older recorded suffixes remain supported: lowercase letters,
digits, underscores, and hyphens are permitted.

Reject absolute/drive-relative paths, backslash aliases, traversal components,
wrong canonical names/extensions, wrong staging variants/layout, and overlapping
paths. Check resolved locations as well as lexical layout. Reject symlink/junction
redirection, including aliases inside the run and redirection outside the trusted
run or to another candidate filename.
The trusted root itself is resolved before defining containment.

Saved file claims use a separate normalization helper. Absolute local/output paths
must remain inside the trusted root; relative claims are anchored to that root,
never the process working directory. `local_relative_path` must actually be relative.
Drive-relative/rooted ambiguity, traversal, malformed paths, resolution failure,
escapes, and disagreeing local/relative claims fail closed. Resolved aliases cannot
hide a claim on either recovering file. Windows path comparisons retain their
normal case-insensitive behavior. Unrelated valid candidate metadata and its Order
output registration may legitimately describe the same unrelated file. Older
completed candidates with an absolute local path and no optional relative path
remain supported. This check does not decode unrelated files or validate their
entire history; it establishes competing claims relevant to the pending files.

Do not require either file to exist in this validator. In particular, missing
staging is allowed after canonical installation. If neither exists, the logical
validator may still return paths; recovery must separately stop without marking
completion. Resolution should support missing paths, including missing parents.

## Tests and reusable fixture

Run only the owner validator tests from the public repository in PowerShell:

```powershell
python -B -m unittest discover -s tests -p "test_pending_download_ownership.py" -v
```

Use the installed Python interpreter with the project's requirements if `python`
does not select it. These tests use disposable synthetic data and never contact
Chrome, CDP, or Flow. Fixtures stay in this public checkout's ignored `.tmp`
directory, including when you run the tests manually.

At the preparation checkpoint the 12 tests deliberately failed because the function
was absent. They now exercise the completed validator, with additional conflict
coverage. No skips, expected-failure decorators, or successful substitutes conceal
missing implementation.

`tests/pending_download_fixtures.py` supplies `make_pending_download_fixture()`.
It creates independent Batch/RunState identities, a saved project, unique candidate,
completed generation, running download, staged synthetic JPEG, and typed pending
record persisted through the real state codec. Canonical installation has not
happened. `fixture.item`, `fixture.candidate`, and `fixture.pending` expose the
relevant objects. `fixture.replace_pending(**changes)` uses `dataclasses.replace()`
to construct an altered immutable record for negative tests.

The tests cover valid ownership; missing staging; missing both files; edited source
binding; wrong batch/project/source/Order/variant/candidate; duplicate ownership;
attempt mismatches; missing/malformed evidence; unsafe paths; resolved redirection;
and conflicting state. Negative groups use `subTest(condition=...)` to identify
the failing condition. All calls also assert unchanged source/state/file contents.
Filesystem resolution is simulated for redirection tests so Windows symlink or
junction privileges are not required.

Supporting schema/codec tests should already pass:

```powershell
python -B -m unittest discover -s tests -p "test_pending_download_state.py" -v
```

## Version and crash-consistency boundaries

- Record version is 1; run-state support begins at version 6.
- Saving an active record atomically persists it and promotes the run-state version
  to at least 6. The in-memory version changes only after replacement succeeds.
- Old records without this field load as `None`; explicit null is accepted.
  Saves omit the field when empty and do not migrate ordinary old state files.
- Active records tagged with an older run-state version, malformed record objects,
  missing required/unknown fields, or unsupported record versions are rejected.
- Shape-valid but contextually wrong records remain your validator's responsibility.
- Clearing a record does not automatically downgrade a version-6 state.
- Older binaries are not promised compatibility with active new records.

Resume planning validates pending evidence without changing files or state. During
execution, recovery compares every available canonical/staging file against the
previously saved hash, size, format, MIME, and dimensions. A matching canonical file
needs no installation. A valid staging-only file is installed with `overwrite=False`.
Missing both files or any conflicting content stops recovery. Path safety is
rechecked around installation and before completion persistence. Completion uses
the metadata observation that matched the pending record, then atomically clears
the record with candidate metadata and `output_files` updates. Order completion
keeps its existing all-candidates checks. Recovery does not increment either
attempt counter or consume generation retry budgets.

Immediately before recovery installation and completion persistence, the runner
reloads authoritative state, reruns ownership/path validation, and requires the
pending record to equal the original receipt. Completion uses that fresh state
snapshot, preserving unrelated changes already persisted at the boundary. These
bounded reads do not add a retry/recovery loop or concurrent-run locking; a change
after the final check remains outside the single-writer guarantee.

New project-owned downloads save the pending record before installation; failed
completion saves preserve it and its staging directory. Records from legacy runs
without project proof cannot reconcile automatically. Existing generic legacy
adapters without UUID evidence retain their old download path and never gain
automatic orphan adoption. States without records retain the existing guards.
An active pending record cannot be bypassed with `--overwrite`; reconcile it first.
Interrupted explicit replacements with conflicting old completion metadata fail
closed under the same validator contract.

The record binds expected content and logical ownership. It does not
cryptographically prove which physical process installed identical bytes, prevent
external filesystem changes after the last validation, or fix the separate
copy-fallback atomicity defect. A partial canonical file left by that fallback
remains protected even when valid staging survives; recovery never overwrites it.
Successful recovery may leave the old staging directory behind. It is not adopted
or recursively removed after its receipt has been cleared.
