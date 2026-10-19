# Private GUI worker contract — version 1

Stage 4C infrastructure is separate from the Stage 4B Tk window. Its Start/Resume
and Stop buttons remain disabled without callbacks. There is no GUI batch worker,
browser launch, execution confirmation or active-run cancellation interface yet.
This protocol is private implementation detail, not a public automation API.

## Process and authority

`WorkerProcess` takes an absolute Python executable and starts
`python -B -u -m nano_batch.gui_worker` with `shell=False`, explicit arguments and
the repository as working directory. On Windows it creates no console window and
uses a separate process group. A future UI must create/supervise it away from Tk
callbacks and drain messages on the UI thread. The current GUI imports none of
these modules. Each worker accepts at most one explicitly submitted job.

Job input has exactly `v`, `type=job`, `mode=preview|execute`, `prompt_file`,
`run_dir`, and `config_file`. Paths are private inputs, not diagnostics; execution
requires absolute paths. There is no raw prompt payload, arbitrary shell command,
adapter selector, automatic Retry flag or configuration mutation message.
The worker reuses application configuration parsing, the x1 text parser, the
validated planner and `execute_sequential_batch()`. Flow and Playwright remain on
its main thread. Existing automatic retry budgets remain entirely runner-owned;
there is no interactive policy-recovery editor or manual Retry in this backend.

An execution job can consume credits. Merely starting a worker cannot. No current
GUI action sends an execution job, and tests inject fake adapters only.

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
as diagnostics. Messages contain no prompts, candidate/project UUIDs,
browser URLs, credentials or arbitrary paths. The supervisor never renders
malformed raw frames. A terminal frame alone is insufficient: EOF and matching
process exit are required; trailing output, a crash or exit mismatch reports lost
communication. There is no automatic worker restart, generation retry or state
adoption. Pipe loss requests cooperative Stop where the channel still works.

## Stop and cleanup

The control reader changes only a threading Event. Existing cancellation checks
consume it on the execution thread through the first-signal latch. A translated
SDK error after Stop remains interruption; real SIGINT still raises immediately
and retains the first signal. No asynchronous exception is injected into a later
operation. The completion boundary serializes final Stop observation with disabling
that request source; duplicate and late requests cannot cancel unrelated work.

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
