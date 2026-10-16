# Six-issue reliability audit

Baseline: public `main` at `7d32596e3d4c023c073012ac5161f3c611cb6328`.
The baseline was clean. This audit used public source, deterministic DOM doubles
and disposable synthetic filesystem fixtures. No real runs, profiles, images,
authenticated browser or private checkout were inspected. The developer's reported
two-Order acceptance and successful flag verification are testimony, not artifacts
independently inspected by Codex. These corrections require user-operated acceptance
before extrapolating to larger batches.

| Issue | Baseline finding | Correction or existing protection | Regression evidence |
|---|---|---|---|
| 1. Historical Failed cards | Partially protected: content baselines/remount ambiguity worked, but novel failure text on a known historical UUID could be accepted as current. | Retain bounded historical IDs at Start. A contradictory historical owner reports terminal attribution ambiguity; it cannot authorize policy/generic/unusual-activity recovery or usage-limit waiting. | `test_flow_historical_uuid_failures`, existing attribution/boundary/privacy/diagnostic tests. |
| 2. Interrupted JPEG download | Prior receipt recovery already covers the canonical-install/state-save gap. | No pipeline rewrite. Saved ownership, both counters, project, paths and expected integrity remain mandatory. Candidate completion and receipt removal share one atomic state replacement. | `test_download_commit_recovery`, `test_pending_download_recovery`, `test_download_finalization_reconciliation`. |
| 3. Playwright floor | The declared 1.60 API minimum is correct for inspected calls. | Keep `playwright>=1.60,<2`; no packages installed/upgraded and no exact dependency pin added. | Official 1.60.0 source/signature audit and existing mocked attachment tests; limitations below. |
| 4. Ctrl+C | Batch active-operation handling existed, but single-Order CLI dispatch lacked its latch. Translated generation errors could be persisted as ordinary failures; late final-batch cancellation could report normal exit. | Reuse the latch and blocking-call boundary for standalone commands, both generation-adapter dispatch paths, and batch command execution. | `test_single_order_cancellation`, existing authoritative/post-generation cancellation, launcher and CLI tests. |
| 5. Atomic JPEG installation | Previously corrected; no direct-to-canonical fallback copying remains. | Hard link first; Windows-only same-volume rename fallback after staging flush/handle close. No overwrite on ordinary install; unsupported platforms fail closed. | `test_download_installation_fallback`, confinement/cleanup and pending recovery tests, including native Windows fixtures. |
| 6. Conflicting UUIDs on completed Orders | `skip_valid` skipped the UUID guard; direct download selected the first matching Order. | Check identity/ownership before every candidate action, reject duplicate matching Orders, malformed/missing IDs and contradictory legacy identity; compare UUID hex case equivalently. | `test_completed_candidate_ownership`, existing ownership, pending receipt and completed-finalization tests. |

## Failure attribution boundaries

The existing 10,000-element Start-boundary traversal retains known media IDs
alongside failure evidence. Only the nearest `flow-image-tile` may supply descendant
IDs; ancestor elements with an explicit `data-media-id` supply their own identity.
Shared history containers cannot supply ownership by aggregating unrelated tiles.
UUID hex case is normalized for comparison only. The set remains page-local,
bounded by the existing traversal and preserved through legitimate re-arming.
Missing provenance remains terminal ambiguity. UUIDs are not added to diagnostics.

A changed failure on a historical owner is not silently ignored: it stops as
`FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS`. The existing exception, privacy-safe labels,
persisted protection and lack of an automatic retry budget remain authoritative.
Unchanged historical text remains excluded, while qualified new failures retain
their existing classifications. This does not cryptographically bind a DOM error
to a server request or identify every previously unseen unrelated asynchronous
failure. Single-writer and trusted-session assumptions remain necessary.

## Download interruption boundaries

Normal completed-asset planning always retains all saved ownership claims. The
separate explicit candidate-repair planner first proves that its disputed target
has neither canonical assets/metadata nor a pending receipt. It then validates
earlier assets against a deep-copied post-repair projection with only that target's
candidate claims removed. The shared read-only planner still checks other UUID and
file claims and image integrity. The real state is untouched until the existing
explicit repair transaction; no repair projection is available to normal Resume.
Malformed non-string IDs, including false/zero values, cannot become legacy URL
fallbacks. A true absent ID with sufficient legacy URL evidence remains supported.

| Interruption boundary | Safe outcome on restart |
|---|---|
| Valid staging bytes, before a pending record is saved | No filename/hash-only adoption. Generation/candidate ownership already persisted can permit a new download of that UUID, without regeneration. Unreceipted staging is not automatic recovery proof. |
| Receipt saved, before canonical installation | Matching staging may be installed with no overwrite, using saved project/ownership/integrity evidence and unchanged attempt counters. |
| Canonical installed, candidate completion not saved | Matching canonical bytes can complete the candidate from the earlier receipt. Hard-link installs may retain both names; rename may leave canonical only. Conflicting/partial files block recovery. |
| Candidate completion saved and receipt removed, Order finalization interrupted | Validate all expected candidates/assets, UUID uniqueness, outputs and variant evidence; finalize only stale Order metadata. No generation or download is needed. |
| Order completion saved | Validate and skip existing results. A late Ctrl+C still reports cancellation but never undoes durable completed work. |

Legacy records without project or sufficient pending evidence remain loadable
but cannot acquire automatic orphan adoption. An acknowledged generation that
stopped before saving candidate ownership still has no restart-safe reconciliation
receipt: keep it blocked. Retry is a new generation, not recovery of an existing
image. See the README's acknowledged-generation guidance; never edit ownership
or move an external image into canonical assets by hand.

## Playwright compatibility evidence

Both attachment calls use `timeout`, `is_local=True` and `no_defaults=True`.
[Official API documentation](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
places `is_local` in 1.58 and `no_defaults` in 1.60. The
[1.60.0 generated API](https://github.com/microsoft/playwright-python/blob/v1.60.0/playwright/sync_api/_generated.py)
accepts both calls; its implementation forwards the corresponding protocol fields.
A static audit checked 212 call shapes across 30 public method names against that
version's signatures. Receiver review distinguishes `Locator.evaluate(timeout=...)`
from `Page.evaluate` and excludes application helper methods. Existing offline
attachment tests verify the actual application's selected options.

This is source/signature compatibility evidence, not execution of an installed
1.60 package or a browser/driver/Flow compatibility matrix. Playwright is absent
from the Codex audit runtime and mocked in the offline suite. The developer reports
live acceptance with Playwright 1.64.0 and Python 3.12.10; it was not independently
repeated here. Installed Chrome/CDP behavior and future versions below 2 are not
guaranteed by matching Python signatures. No full clean-machine Windows installation
was performed. The README retains direct virtual-environment interpreter commands
and the API-based range rather than inventing an exact-version support guarantee.

## Remaining limitations

- Single writer only. Validation is not a lock; filesystem changes after checks
  remain possible. Coherently tampered trusted state is not authenticated by a
  signature, and a pending receipt does not identify the physical writer.
- Atomic name visibility/state replacement does not prove survival through power
  loss, provider synchronization or every Windows filesystem implementation.
  JSON and fallback staging files are flushed, but no complete storage durability
  protocol is claimed. Explicit overwrite uses a separate replacement contract.
- Sharing violations, ACLs, read-only historical staging, disputed files and
  unsupported environments can block recovery. Preserve evidence; do not force
  overwrite, chmod unrelated hard links or automatically delete leftovers.
- Cancellation stops at existing synchronous-call boundaries. It cannot promise
  an immediate return while a driver/browser call has not returned. Completed
  work remains completed, incomplete canonical files are not newly streamed into
  existence, and native process termination has no graceful-cleanup guarantee.
- Steady minimized operation remains unsupported. Window restoration, current
  Flow layouts, interruption recovery and all newly hardened paths need bounded
  user-operated acceptance before large-batch production claims.

Final test totals, static/security checks and engineering attribution are recorded
in [ENGINEERING_LOG.md](ENGINEERING_LOG.md). No live actions were performed.
