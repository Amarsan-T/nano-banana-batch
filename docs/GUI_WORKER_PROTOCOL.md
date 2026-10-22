# Private GUI worker contract — version 1

Stage 4D connects the Stage 4C infrastructure to the Tk window. Confirmed
Start/Resume uses one supervised worker; Stop stays cooperative. Source GUI Chrome
launch remains disabled. The internal frozen GUI invokes a separate, fixed CLI
companion action for Chrome bootstrap; the worker protocol has no launch operation. This protocol is
private implementation detail, not a public automation API.

## Process and authority

In source mode, `WorkerProcess` takes an absolute Python executable and starts
`python -B -u -m nano_batch.gui_worker` with `shell=False`, explicit arguments and
the repository as working directory. In the internal frozen build it resolves
the exact sibling `NanoBananaBatchWorker.exe`, without treating `sys.executable`
as Python or searching PATH. The three executables share one resource folder;
the console-mode worker retains its private pipe handles. On Windows supervision creates no console window and
uses a separate process group. The GUI creates/supervises it away from Tk
callbacks and drains bounded messages on the UI thread. Each worker accepts at
most one explicitly submitted job.

Job input has exactly `v`, `type=job`, `mode=preview|execute`, `prompt_file`,
`run_dir`, and `config_file`, plus optional `expected_snapshot`. Paths are private
inputs, not diagnostics; execution requires absolute paths. There is no raw prompt
payload, arbitrary shell command,
adapter selector, automatic Retry flag or configuration mutation message.
The worker reuses application configuration parsing, the x1 text parser, the
validated planner and `execute_sequential_batch()`. Flow and Playwright remain on
its main thread. Existing automatic retry budgets remain entirely runner-owned;
there is no interactive policy-recovery editor or manual Retry in this backend.

An execution job can consume credits. Merely starting a worker cannot. The GUI
sends execution only after fresh Preview and explicit credit/retry confirmation.
Automated tests inject fake adapters only. Later developer-reported source-GUI
acceptance is separate evidence; packaged live Flow acceptance remains pending.

The optional `expected_snapshot` is exactly 64 lowercase hexadecimal characters.
It binds the configured Preview's private prompt/state/configuration fingerprints,
run identity and displayed plan decisions. It is neither a credential nor a
persisted record and is never diagnostic output. GUI jobs always include it.
The supervisor revalidates before spawning; the worker verifies it again under
execution ownership, before adapter creation or any state write. Old internal
Stage 4C requests without the optional field remain decodable. Version 1, the
8 KiB frame bound, output schemas and existing exit codes are preserved.
`STALE_PREVIEW` is an additional allowlisted blocked code. Invalidated or corrupt
inputs fail closed; no manual Retry is implied. Configuration helpers are shared
without importing the browser adapter into the GUI.

Input/state/configuration changes clear Preview authorization; confirmation also
rechecks the current GUI selection after the modal dialog. Asset-dependent plans
are revalidated before execution. These checks do not eliminate nonparticipating
filesystem changes between validation and later operations.

## Framing and bounded reporting

Frames are UTF-8 JSON lines, at most 8,192 bytes including newline. Version `v=1`
and exact field sets are mandatory. Duplicate keys, unknown versions/types/fields,
malformed Unicode/JSON, inconsistent counts, invalid percentages, nonfinite or
out-of-range numbers and unapproved error codes are rejected. Job path strings
are bounded to 2,048 characters and must also fit the frame limit.

Worker message types:

- `ready`: no payload. Exactly one readiness frame precedes other messages.
- `preview`: total and counts for pending, completed, download-only, finalization
  pending, blocked, waiting and existing automatic retry eligibility.
- `progress`: existing event sequence/elapsed time/phase, numeric Order/variant/
  attempt, verified counts/percentage and an allowlisted error code. It contains
  no prompt identifier beyond numeric Order and never exposes a candidate UUID.
- `stop_ack`: `pending`, `duplicate`, or `finished`. None means rollback or safe
  regeneration. The supervisor sends at most one Stop per worker.
- `terminal`: bounded totals, fixed code, outcome and exit code. Outcomes are
  preview-complete/completed (0), waiting (3), interrupted (130), blocked/failed/
  communication-lost (2); wire values use underscores where needed.

Each direction rejects unexpected schemas. Output retains at most eight control
messages, one replaceable latest progress snapshot and one reserved terminal
message. Stop acknowledgements coalesce. Progress can omit intermediate milestones
under backpressure; completion counts come from the last validated runner
snapshot, not from counting received events. Interrupted counts can lag durable
completion; saved state remains authoritative. Pipe I/O runs on daemon transport
threads and never blocks the runner's progress callback. Worker exit allows a
bounded two-second terminal delivery drain, not a browser recovery wait.

The protocol uses a dedicated noninheritable descriptor. Both ordinary stdout
and stderr are redirected at the OS level, containing native writes as well as
Python prints. CLI prints, warnings, SDK output and raw exceptions are not relayed
as diagnostics. Worker diagnostics contain no prompts, candidate/project UUIDs,
browser URLs, credentials or arbitrary paths. The supervisor never renders
malformed raw frames. A terminal frame alone is insufficient: EOF and matching
process exit are required; trailing output, a crash or exit mismatch reports lost
communication. There is no automatic worker restart, generation retry or state
adoption. Pipe loss requests cooperative Stop where the channel still works.

## Setup failure codes

The exact `code` allowlist also contains 28 combinations of
`SETUP_<STAGE>_<CATEGORY>` in the existing field. Stage tokens are `WORKER`,
`PROJECT`, `DRIVER`, `RECONNECT`, `EDITOR`, `SETTINGS` and `CLEANUP`; category
tokens are `INVALID_INPUT`, `RESOURCE`, `IO` and `OPERATION`. The GUI maps these
to fixed stage/category labels and short guidance. No exception text or new
payload field is sent. Version 1, exact field validation, frame limits, outcomes
and exit codes remain unchanged. An older GUI rejects these new codes, so
rebuild/deploy all application components together; the new decoder still accepts
the existing codes. This private allowlist extension is not mixed-version
compatibility or a public schema change.

Observation is invocation-local. Nested project setup boundaries label only the
same exception that escapes to the worker's existing generic failure fallback.
Worker initialization covers failures before runner dispatch. Driver startup is
the existing import/start operation; project reconnection includes attachment to
the selected Flow tab and preparation of the saved project. Editor/settings and
cleanup boundaries apply only within project preparation, not later generation
or download work. Existing specific codes take precedence. Cancellation still
wins with exit 130, including translated errors after Stop.

Categories reflect only the escaping Python exception type: `ImportError` and
`FileNotFoundError` are resource failures; `ValueError`/`TypeError` are invalid
input; other `OSError` values are I/O; others are operation failures. A wrapped
browser error may therefore retain the generic operation category. A cleanup
exception can supersede an earlier error under normal Python semantics; the code
does not claim to identify that earlier root cause. Recording failure falls back
to existing generic handling. No exception, retry decision, saved state or browser
operation is changed by reporting. A setup label alone is not submission or
ownership evidence and never authorizes Retry or reconciliation.

## Stop and cleanup

The control reader changes only a threading Event. Existing cancellation checks
consume it on the execution thread through the first-signal latch. A translated
SDK error after Stop remains interruption; real SIGINT still raises immediately
and retains the first signal. No asynchronous exception is injected into a later
operation. The completion boundary serializes final Stop observation with disabling
that request source; duplicate and late requests cannot cancel unrelated work.

The GUI supervisor remains active until actual worker exit and closed handles,
including after protocol failure. It drains the bounded final mailbox even if
process exit preceded message consumption. 100% progress is not terminal success;
a crash after a success frame still means uncertain outcome. There is no automatic
restart or replacement invocation. Each invocation owns a fresh Stop Event.
Closing the window offers either continuing or Stop-and-close-after-exit; no
normal close abandons the worker. The supervisor thread is non-daemon, and an
exceptional GUI main-loop unwind requests Stop and waits for supervision cleanup.

An operation blocked inside the SDK may not return promptly. Stop acknowledgement
must remain pending until unwind actually completes. `WorkerProcess.close()`
returns false while the process is active; callers must keep supervising. It
does not terminate Chrome, the worker or its process tree. Durable ownership,
pending download receipts, atomic installation and existing ambiguous-submission
protection remain runner responsibilities. Acknowledged generation without durable
candidate ownership still cannot automatically Resume or be resubmitted.

## Shared execution ownership

CLI execution/browser commands hold a global session gate through cleanup.
Runner mutation boundaries additionally hold the selected run's gate; nested
calls on the same process/thread reuse ownership. A copied context in a different
process/thread cannot stand in for OS acquisition. Read-only planning takes no
execution locks and does not normalize state.

Windows uses nonblocking, OS-owned named mutexes in the Global namespace. The
coarse dedicated-browser gate deliberately serializes all participating executions,
even with different run folders, ports or profiles. Run names hash normalized
resolved paths, covering tested case, relative-path and junction aliases. The
coarse gate also excludes aliases that cannot be normalized reliably. Access or
ownership failures fail closed with fixed guidance. Windows releases abandoned
ownership after thread/process death; this does not prove state or file integrity.
No file-presence check, PID guess or stale-lock deletion establishes ownership.

POSIX falls back to per-user local `flock` ownership in a private temporary
directory; lock files remain and are never treated as proof of a live owner.
This is not cross-user, cross-host or distributed locking, and POSIX behavior is
not claimed as Windows acceptance. Nonparticipating older copies, manual browser
use/editors, malicious local processes and filesystem/provider races are outside
these cooperative guarantees. A stuck owner must finish/exit before another
participating writer proceeds; deleting a pathname is not recovery.
