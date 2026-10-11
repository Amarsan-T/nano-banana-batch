from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .models import (
    CandidateRecord, FlowProjectReference, PendingDownloadCommit,
    PENDING_DOWNLOAD_COMMIT_STATE_VERSION, RunItemState, RunState,
)


class StatePersistenceError(RuntimeError):
    """Raised when an atomic run-state replacement cannot be completed safely."""


_ATOMIC_REPLACE_RETRY_DELAYS_SECONDS = (0.05, 0.10, 0.20)


class RunStateStore:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def _replace_atomically(self, tmp_name: str) -> None:
        """Retry only transient Windows access failures around atomic replacement."""
        attempts = len(_ATOMIC_REPLACE_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempts):
            try:
                os.replace(tmp_name, self.path)
                return
            except OSError as exc:
                transient_access_error = (
                    isinstance(exc, PermissionError)
                    or getattr(exc, "winerror", None) == 5
                )
                if not transient_access_error:
                    raise
                if attempt >= len(_ATOMIC_REPLACE_RETRY_DELAYS_SECONDS):
                    raise StatePersistenceError(
                        "Atomic run-state persistence failed after "
                        f"{attempts} attempts because Windows kept the state file locked; "
                        "the previous run_state.json was left untouched."
                    ) from exc
                time.sleep(_ATOMIC_REPLACE_RETRY_DELAYS_SECONDS[attempt])

    def save(self, state: RunState) -> None:
        has_pending_download = False
        for item in state.items:
            for candidate in item.candidates:
                if candidate.pending_download_commit is not None:
                    candidate.validate()
                    has_pending_download = True
        if state.flow_project is not None:
            state.flow_project.validate()
        if state.flow_project is not None and not state.flow_project_required:
            raise ValueError("Flow project metadata requires flow_project_required=true.")
        if self.path.exists():
            existing_raw = json.loads(self.path.read_text(encoding="utf-8"))
            existing_required = bool(existing_raw.get("flow_project_required", False))
            existing_project_raw = existing_raw.get("flow_project")
            existing_items = {item["order"]: item for item in existing_raw["items"]}
            for item in state.items:
                previous = existing_items.get(item.order, {})
                source = previous.get("source_prompt")
                if source is not None and item.source_prompt != source:
                    raise ValueError("Original prompt binding cannot be replaced or removed.")
                if source is None and item.source_prompt is not None and (
                    item.source_prompt != previous.get("prompt")
                ):
                    raise ValueError("Original prompt binding must match the stored source prompt.")
            if existing_required and not state.flow_project_required:
                raise ValueError("Flow project ownership cannot be removed from a run.")
            if isinstance(existing_project_raw, dict):
                existing_project = FlowProjectReference(**existing_project_raw)
                existing_project.validate()
                if state.flow_project != existing_project:
                    raise ValueError("Flow project ownership cannot be replaced or removed.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = asdict(state)
        if has_pending_download:
            if type(state.version) is not int:
                raise ValueError("Pending download state version must be an integer.")
            document["version"] = max(state.version, PENDING_DOWNLOAD_COMMIT_STATE_VERSION)
        # Omit the new optional field for ordinary/legacy candidates. An active
        # record and its version migration are installed in the same replacement.
        for item in document["items"]:
            for candidate in item["candidates"]:
                if candidate["pending_download_commit"] is None:
                    del candidate["pending_download_commit"]
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_atomically(tmp_name)
            state.version = document["version"]
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def load(self) -> RunState:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw_flow_project = raw.get("flow_project")
        flow_project = (
            FlowProjectReference(**raw_flow_project)
            if isinstance(raw_flow_project, dict)
            else None
        )
        if flow_project is not None:
            flow_project.validate()
        items = []
        for raw_item in raw["items"]:
            item = dict(raw_item)
            candidates = []
            for raw_candidate in item.get("candidates", []):
                candidate = dict(raw_candidate)
                pending = candidate.get("pending_download_commit")
                if pending is not None:
                    if (
                        type(raw["version"]) is not int
                        or raw["version"] < PENDING_DOWNLOAD_COMMIT_STATE_VERSION
                    ):
                        raise ValueError("Pending download records require run-state version 6 or later.")
                    if not isinstance(pending, dict):
                        raise ValueError("Pending download metadata must be a JSON object or null.")
                    try:
                        record = PendingDownloadCommit(**pending)
                    except TypeError as exc:
                        raise ValueError("Pending download record fields are malformed.") from exc
                    record.validate()
                    candidate["pending_download_commit"] = record
                candidates.append(CandidateRecord(**candidate))
            item["candidates"] = candidates
            items.append(RunItemState(**item))
        state = RunState(
            schema=raw["schema"],
            version=int(raw["version"]),
            batch_id=raw["batch_id"],
            items=items,
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            flow_project=flow_project,
            flow_project_required=bool(
                raw.get("flow_project_required", flow_project is not None)
            ),
        )
        if state.flow_project is not None and not state.flow_project_required:
            raise ValueError("Flow project metadata requires flow_project_required=true.")
        for item in state.items:
            item.validate()
        return state
