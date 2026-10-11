"""Reusable synthetic state and JPEGs for Stage 2.2 offline tests."""

import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import UUID

from PIL import Image

from nano_batch.downloads import validate_image_file
from nano_batch.models import (
    Batch, BatchItem, CandidateRecord, FlowProjectReference,
    PendingDownloadCommit, RunItemState, RunState,
)
from nano_batch.runner import output_basename
from nano_batch.state import RunStateStore


def pending_download_test_directory():
    """Keep disposable fixtures in this public checkout's ignored scratch area."""
    scratch = Path(__file__).resolve().parents[1] / ".tmp" / "s22-fixtures"
    scratch.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


@dataclass
class PendingDownloadFixture:
    batch: Batch
    state: RunState
    run_dir: Path
    store: RunStateStore
    canonical_path: Path
    staging_path: Path

    @property
    def item(self):
        return self.state.items[0]

    @property
    def candidate(self):
        return self.item.candidates[0]

    @property
    def pending(self):
        return self.candidate.pending_download_commit

    def replace_pending(self, **changes):
        self.candidate.pending_download_commit = replace(self.pending, **changes)


def make_pending_download_fixture(run_dir: Path) -> PendingDownloadFixture:
    """Persist independent source/state identities and a pending staged JPEG."""
    run_dir = run_dir.resolve()
    batch = Batch(
        batch_id="sha256:synthetic-pending-download", source_schema="test",
        items=(
            BatchItem(order=1, visual_id="synthetic_visual", prompt="synthetic source", variants=1),
            BatchItem(order=2, visual_id="synthetic_neighbor", prompt="synthetic neighbor", variants=1),
        ),
    )
    project_id = str(UUID(int=999))
    candidate_id = str(UUID(int=101))
    timestamp = "2026-01-01T00:00:00+00:00"  # Synthetic fixture time, not historical evidence.
    item = RunItemState(
        order=1, visual_id=batch.items[0].visual_id, prompt=batch.items[0].prompt,
        status="completed", attempt_count=2, started_at=timestamp, completed_at=timestamp,
        download_status="running", download_attempt_count=3, download_started_at=timestamp,
        result_details='{"configured_variants":1,"requested_variants":1}',
        candidates=[CandidateRecord(
            variant=1, suggested_filename=output_basename(1, batch.items[0].visual_id, 1, "png"),
            source_kind="rendered-image", candidate_id=candidate_id,
            asset_url=f"https://flow-content.google/image/{candidate_id}", download_status="running",
        )],
    )
    state = RunState(
        schema="nano-banana-batch-run-state", version=4, batch_id=batch.batch_id,
        items=[item, RunItemState(order=2, visual_id=batch.items[1].visual_id, prompt=batch.items[1].prompt)],
        created_at=timestamp, updated_at=timestamp, flow_project_required=True,
        flow_project=FlowProjectReference(project_id, f"https://flow.google.com/project/{project_id}"),
    )
    store = RunStateStore(run_dir / "run_state.json")
    store.save(state)
    staging = run_dir / "assets" / ".staging-synthetic" / "v01.download"
    staging.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 9), (20, 40, 60)).save(staging, format="JPEG")
    canonical = run_dir / "assets" / output_basename(1, item.visual_id, 1, "jpeg")
    metadata = validate_image_file(staging)
    item.candidates[0].pending_download_commit = PendingDownloadCommit(
        record_version=1, batch_id=batch.batch_id, project_id=project_id,
        order=1, visual_id=item.visual_id, variant=1, candidate_id=candidate_id,
        generation_attempt_count=item.attempt_count, download_attempt_count=item.download_attempt_count,
        canonical_relative_path=canonical.relative_to(run_dir).as_posix(),
        staging_relative_path=staging.relative_to(run_dir).as_posix(),
        download_mechanism="synthetic-offline-download",
        browser_suggested_filename="synthetic.jpeg", browser_download_guid="synthetic-guid",
        **asdict(metadata),
    )
    store.save(state)
    return PendingDownloadFixture(batch, store.load(), run_dir, store, canonical, staging)
