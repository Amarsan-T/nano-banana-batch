# Development history and provenance

Nano Banana Batch is a developer-led, AI-assisted Python automation project for sequential image generation and managed downloads through Google Flow. This document records **product history**, not an invented Git history. Dates below describe historical work and observations; Git commit timestamps reflect when snapshots and fixes were actually committed.

The public repository is a **sanitized copy**, maintained separately from the private production workspace. It does not include private browser profiles, Google sessions, production runs, generated assets, or original chat exports.

## Origins and authorship

The project originated in the developer's earlier work on automating a YouTube visual-production workflow, including a separate tool known as YouTube Factory. The developer sought to separate image generation from video editing: prepare ordered visual prompts, process them through Google Flow, retain stable visual identities, and later import the resulting images into an editing workflow.

The developer reports that, before the earliest surviving Nano Banana Batch ZIP, he had personally developed earlier software iterations and supplied related code and project materials to ChatGPT. Those earlier source snapshots are no longer available for comparison. **Their existence and the developer's account are recorded here as firsthand testimony; the exact extent of code reuse cannot be independently established from the surviving files.** This does not erase the developer's earlier work, nor does it establish that every line of the surviving scaffold was written personally by the developer.

On **September 5, 2026**, at the developer's explicit request, ChatGPT delivered the first surviving standalone `nano_banana_batch_v0_1.zip` scaffold, to be developed further using Codex. The surviving ZIP has SHA-256 `1efab36c90d1c983a77d800e4875de4646415bb948ccde1760d48d8d305a20a6`. It contains a real Python program, not merely a design document, with CLI commands, batch validation, deterministic identity, local state persistence, a browser-adapter boundary, sample input, and **12 passing offline tests**. **It did not yet submit prompts to Flow, generate images, or download results automatically.**

The developer designed product requirements, participated in architecture and technical decisions, personally operated real tests, made debugging observations, reviewed proposed changes, and also made direct code contributions during later work. ChatGPT assisted with planning and the initial surviving scaffold; Codex assisted extensively with implementation and automated tests. Descriptions of specific direct code contributions are maintained separately in the engineering log. No blanket percentage or exclusive line-by-line authorship claim is made for historical code.

## Dated project milestones

### September 2–4, 2026 — From YouTube workflow to standalone automation

- Studied practical YouTube-production workflows and identified the repetitive image-generation bottleneck.
- Explored an end-to-end flow connecting scripts, visual prompts, generated images, edit plans, and rendering.
- Developed and tested the wider YouTube Factory concept and decided the Google Flow image-generation component should remain a standalone tool rather than be inseparably embedded in video editing.
- Prioritized a predictable batch pipeline and state preservation rather than attempting advanced video generation or GUI functionality immediately.

### September 5, 2026 — Initial Nano Banana Batch v0.1 scaffold

- The developer asked to begin the separate Nano Banana project using prior ideas and examples, and later explicitly asked ChatGPT, rather than Codex, to assemble the initial standalone folder before Codex continued development.
- The surviving scaffold introduced batch JSON input, contiguous order and unique `visual_id` checks, deterministic semantic batch IDs, resumable local state structure, dry-run/inspect tools, and an interface boundary for browser work.
- The scaffold's sample file used a YouTube Factory-compatible external visual-batch schema. Compatibility reflects architectural continuity; it is not a line-by-line source-code provenance finding.
- Subsequent live Flow exploration encountered genuine image-mode/DOM verification failures, leading to iterative browser-automation development.

### September 6–7, 2026 — First real pipeline and larger acceptance

- Development added real sequential generation, candidate identification via `data-media-id` UUIDs, Playwright/Chrome DevTools Protocol download handling, `1K Original size` selection, JPEG validation, and stateful resume/retry behavior.
- A three-order run demonstrated that completed generations and valid downloads could be preserved while only incomplete candidate downloads were resumed.
- Historical terminal output and acceptance reports recorded **51/51 orders completed and 102/102 canonical JPEG assets**, with unique candidate identities and no missing or extra assets in the checked run. This is **archived acceptance evidence**, not a fresh execution from the sanitized public checkout.

### September 7–8, 2026 — Continuity handoff, window states, and usability

- The developer deliberately started a second ChatGPT conversation with a precise handoff of the same project, preserving the accepted 51/102 state and engineering constraints. Both historical conversations overlapped; they are not one continuous chat thread or two unrelated projects.
- When Chrome downloads failed with a permission message, the developer reported the browser evidence and performed controlled Windows-user write and download tests. Running downloads from ordinary interactive PowerShell subsequently succeeded without changing the core pipeline. This supported an execution-environment difference, but did **not** prove a particular ACL rule was the sole root cause.
- A clean three-order run produced six JPEGs while Chrome was reportedly minimized after the first Start acknowledgement. Later **x1 + minimized** tests exposed additional submenu failures, so the earlier acceptance must not be described as a guarantee for every configuration or environment.
- At the developer's request, the tool gained convenience commands for one prompt and then a UTF-8 text file containing ordered prompts, with **one generated image per prompt** (`x1`) rather than two. A clean historical file-based x1 run finished **3/3** with three JPEG assets.
- Repeated `Download` submenu failures led to narrowly scoped DOM/ARIA and physical-hover handoff corrections, bounded reacquisition, and regression tests, while retaining conservative candidate ownership checks.

### September 9–30, 2026 — Day-to-day operation and hardening

- The developer exercised ordinary batch workflows and launch/resume flows, requested simpler Chrome-launch controls, and provided repeated real-world observations about window visibility and download-menu behavior.
- The developer emphasized preserving completed work, distinguishing retryable failures from usage limits, avoiding uncontrolled automatic retries, and not switching projects or browser settings without clear authorization.
- New diagnostics and hardening addressed candidate reacquisition, constrained retry/resume paths, browser menu changes, and failure-state handling. Historical accounts of larger batches represent archived operator reports, not validation of every corresponding asset in this repository.

### October 1–7, 2026 — Reliability and interruption boundaries

- Attention shifted to Ctrl+C cancellation, UI latency, accurate generation-failure attribution, safe editing of failed prompts, and whether state persisted at the correct point after a download.
- The developer identified an important interruption hypothesis: a candidate image could already be downloaded while the logical order had not yet been finalized. Investigations distinguished candidate-completion, order-completion, and download-commit boundaries. These are related but not interchangeable failure modes.

### October 8–10, 2026 — Sanitized public Git repository

- The developer decided to create a separately maintained **sanitized public copy**, leaving the private production project and runtime data untouched.
- Publication preparation included repository boundaries, exclusion of private data, an initial Git snapshot, documentation, and offline-only verification.
- The surviving public Git history starts with the sanitized snapshot. It is **not** a record of every development action performed since September.

### October 2026 — Public-repository corrections and release preparation

The following are the three previously reported local commits, retained in their existing chronological Git order:

| Existing commit | Subject | Significance |
| --- | --- | --- |
| `cd85c7e` | `Initial sanitized project snapshot` | First tracked public-code snapshot; does not imply development began here. |
| `482909b` | `fix(flow): prevent historical failures from affecting new attempts` | Historical Failed-card attribution isolated from a current generation's evidence, with conservative ambiguous-state behavior and offline regressions. |
| `80a772c` | `fix(download): recover interrupted candidate commits safely` | Recovery for interruption between download/canonical asset handling and persisted candidate state, with regression coverage. |

These identifiers are documented from the previously reported local history; a release review must verify that they still match the current repository before publishing. Additional Stage 3 publication hardening, an MIT `LICENSE` with the developer's copyright notice, and related documentation were subsequently **reported as uncommitted local changes**. Their exact content and test results must be checked against the present working tree before making new commits or pushing.

The developer's **direct Python work on the download ownership validator** is recorded in the existing engineering log, with Codex's subsequent integration and tests distinguished from the developer-authored fragments. Earlier component authorship is not inferred from these later contributions.

## Reading this history correctly

- **Project milestone dates are not commit dates.** The September work predates the public Git repository. No historical commit was fabricated or backdated.
- **A user-role transcript entry can contain copied Codex output.** It is not automatically proof that the developer personally typed every listed implementation change.
- **Historical acceptance results are evidence from archived logs.** They are not a promise that live Google Flow continues to behave identically or that the sanitized checkout was retested against real Flow.
- **A successful atomic operation does not imply atomicity of every filesystem fallback.** See the README and engineering log for currently disclosed limits.
- **Origin and licensing deserve care.** The earliest surviving source was supplied by ChatGPT at the developer's request and is connected to earlier developer work. Lost earlier source snapshots prevent full line-by-line verification; third-party dependency licenses apply independently of this repository's license. Historical uncertainty is disclosed rather than silently erased.
- **Private evidence is deliberately excluded.** Full ChatGPT exports, detailed message indexes, private paths, session data, and local production artifacts are retained outside the public repository.

## Where to read more

- [`ENGINEERING_LOG.md`](ENGINEERING_LOG.md) — specific investigated failures, regression evidence, direct contributions, and implementation decisions.
- [`../README.md`](../README.md) — supported functionality and limitations in the present checkout.
- [`../LICENSE`](../LICENSE) — terms for code that the copyright holder is entitled to license.

This document was reconstructed from preserved early-project conversations, the surviving original September 5 scaffold, and later local engineering reports. It is a **transparent historical narrative**, not a reconstructed commit graph or an independent legal determination of source ownership.
