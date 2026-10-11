from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import FLOW_USAGE_LIMIT_REACHED, WAITING_FOR_FLOW_LIMIT_RESET


VALID_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    WAITING_FOR_FLOW_LIMIT_RESET,
}
VALID_DOWNLOAD_STATUSES = {"not_started", "running", "completed", "failed"}
PENDING_DOWNLOAD_COMMIT_STATE_VERSION = 6
_FLOW_PROJECT_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True)
class BatchItem:
    order: int
    visual_id: str
    prompt: str
    mode: str | None = None
    aspect_ratio: str | None = None
    preferred_model_hint: str | None = None
    variants: int | None = None


@dataclass(frozen=True)
class Batch:
    batch_id: str
    source_schema: str
    items: tuple[BatchItem, ...]
    source_batch_revision: str | None = None
    source_visual_manifest_revision: str | None = None


@dataclass(frozen=True)
class GenerationSettings:
    mode: str = "image"
    aspect_ratio: str = "16:9"
    variants: int = 2
    preferred_model_hint: str | None = None
    result_timeout_seconds: int = 300

    def validate(self) -> None:
        if self.mode != "image":
            raise ValueError("Only image generation is supported in v0.1.")
        if not self.aspect_ratio.strip():
            raise ValueError("aspect_ratio cannot be empty.")
        if self.variants <= 0:
            raise ValueError("variants must be positive.")
        if self.preferred_model_hint is not None and (
            not isinstance(self.preferred_model_hint, str) or not self.preferred_model_hint.strip()
        ):
            raise ValueError("preferred_model_hint must be a non-empty string or null.")
        if self.result_timeout_seconds <= 0:
            raise ValueError("result_timeout_seconds must be positive.")


@dataclass(frozen=True)
class DiscoveredCandidate:
    source_kind: str
    candidate_id: str | None = None
    accessible_name: str | None = None
    asset_url: str | None = None
    signal: str | None = None


@dataclass(frozen=True)
class PendingDownloadCommit:
    """Expected content and logical ownership, persisted before file installation.

    Shape validation is deliberately separate from the owner's contextual
    ownership/path validator. This record does not prove the physical writer.
    """

    record_version: int
    batch_id: str
    project_id: str | None
    order: int
    visual_id: str
    variant: int
    candidate_id: str
    generation_attempt_count: int
    download_attempt_count: int
    canonical_relative_path: str
    staging_relative_path: str
    sha256: str
    byte_size: int
    image_format: str
    mime_type: str
    width: int
    height: int
    download_mechanism: str
    browser_suggested_filename: str | None = None
    browser_download_guid: str | None = None

    def validate(self) -> None:
        """Reject malformed fields without granting recovery eligibility."""
        if type(self.record_version) is not int or self.record_version != 1:
            raise ValueError("Unsupported pending download record version.")
        for name in (
            "batch_id", "visual_id", "candidate_id", "canonical_relative_path",
            "staging_relative_path", "sha256", "image_format", "mime_type",
            "download_mechanism",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Pending download {name} must be a non-empty string.")
        for name in (
            "order", "variant", "generation_attempt_count", "download_attempt_count",
            "byte_size", "width", "height",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"Pending download {name} must be a positive integer.")
        if not _FLOW_PROJECT_ID_PATTERN.fullmatch(self.candidate_id):
            raise ValueError("Pending download candidate identity must be a canonical UUID.")
        if self.project_id is not None and (
            not isinstance(self.project_id, str)
            or not _FLOW_PROJECT_ID_PATTERN.fullmatch(self.project_id)
        ):
            raise ValueError("Pending download project identity must be a canonical UUID or null.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Pending download SHA-256 must contain 64 lowercase hex digits.")
        format_mimes = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
        if format_mimes.get(self.image_format) != self.mime_type:
            raise ValueError("Pending download image format and MIME type must be supported and match.")
        for name in ("browser_suggested_filename", "browser_download_guid"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Pending download {name} must be a string or null.")


@dataclass
class CandidateRecord:
    variant: int
    suggested_filename: str
    source_kind: str
    accessible_name: str | None = None
    asset_url: str | None = None
    signal: str | None = None
    download_status: str = "not_started"
    candidate_id: str | None = None
    browser_suggested_filename: str | None = None
    browser_download_guid: str | None = None
    download_mechanism: str | None = None
    local_path: str | None = None
    local_relative_path: str | None = None
    sha256: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    image_format: str | None = None
    downloaded_at: str | None = None
    download_error: str | None = None
    pending_download_commit: PendingDownloadCommit | None = None

    def validate(self) -> None:
        if self.download_status not in VALID_DOWNLOAD_STATUSES:
            raise ValueError(f"Invalid candidate download status: {self.download_status}")
        if self.pending_download_commit is not None:
            if not isinstance(self.pending_download_commit, PendingDownloadCommit):
                raise ValueError("Pending download metadata must be a PendingDownloadCommit record.")
            self.pending_download_commit.validate()


@dataclass(frozen=True)
class GenerationResult:
    candidates: tuple[DiscoveredCandidate, ...]
    output_files: tuple[str, ...] = ()
    details: str | None = None


@dataclass(frozen=True)
class BrowserDownloadResult:
    candidate_id: str
    staged_path: str
    browser_suggested_filename: str | None = None
    browser_download_guid: str | None = None
    mechanism: str = "cdp"


@dataclass(frozen=True)
class FlowProjectReference:
    project_id: str
    project_url: str
    created_for_batch: bool = True

    def validate(self) -> None:
        if not _FLOW_PROJECT_ID_PATTERN.fullmatch(self.project_id):
            raise ValueError("Flow project metadata contains an invalid project UUID.")
        expected_url = f"https://flow.google.com/project/{self.project_id}"
        if self.project_url != expected_url:
            raise ValueError(
                "Flow project metadata must contain the exact normalized project URL."
            )
        if self.created_for_batch is not True:
            raise ValueError("Run-owned Flow project metadata must be created_for_batch=true.")


@dataclass
class RunItemState:
    order: int
    visual_id: str
    prompt: str
    status: str = "pending"
    attempt_count: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    generation_error_code: str | None = None
    automatic_policy_retry_count: int = 0
    automatic_generic_failure_retry_count: int = 0
    output_files: list[str] = field(default_factory=list)
    candidates: list[CandidateRecord] = field(default_factory=list)
    result_details: str | None = None
    download_status: str = "not_started"
    download_attempt_count: int = 0
    download_started_at: str | None = None
    download_completed_at: str | None = None
    download_error: str | None = None
    # Original semantic-batch binding; prompt remains the run's effective prompt.
    source_prompt: str | None = None

    def matches_source(self, item: BatchItem) -> bool:
        source = self.prompt if self.source_prompt is None else self.source_prompt
        return (self.order, self.visual_id, source) == (
            item.order, item.visual_id, item.prompt,
        )

    def validate(self) -> None:
        if self.source_prompt is not None and self.automatic_policy_retry_count != 1:
            raise ValueError("Edited policy prompt requires the consumed automatic retry budget.")
        if self.source_prompt is not None and (
            not isinstance(self.source_prompt, str) or not self.source_prompt.strip()
            or not isinstance(self.prompt, str) or not self.prompt.strip()
        ):
            raise ValueError("Edited prompt and original source binding must be non-empty strings.")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")
        if (
            self.status == WAITING_FOR_FLOW_LIMIT_RESET
            and self.generation_error_code != FLOW_USAGE_LIMIT_REACHED
        ):
            raise ValueError(
                "Flow-limit waiting state requires generation_error_code="
                f"{FLOW_USAGE_LIMIT_REACHED}."
            )
        if (
            isinstance(self.automatic_policy_retry_count, bool)
            or not isinstance(self.automatic_policy_retry_count, int)
            or self.automatic_policy_retry_count not in {0, 1}
        ):
            raise ValueError("Automatic policy retry count must be 0 or 1.")
        if self.automatic_policy_retry_count and self.attempt_count < 1:
            raise ValueError(
                "An automatic policy retry cannot be consumed before an attempt exists."
            )
        if (
            isinstance(self.automatic_generic_failure_retry_count, bool)
            or not isinstance(self.automatic_generic_failure_retry_count, int)
            or not 0 <= self.automatic_generic_failure_retry_count <= 2
        ):
            raise ValueError("Automatic generic-failure retry count must be 0, 1, or 2.")
        if self.automatic_generic_failure_retry_count and self.attempt_count < 1:
            raise ValueError(
                "An automatic generic-failure retry cannot be consumed before "
                "an attempt exists."
            )
        if self.download_status not in VALID_DOWNLOAD_STATUSES:
            raise ValueError(f"Invalid item download status: {self.download_status}")
        for candidate in self.candidates:
            candidate.validate()


@dataclass
class RunState:
    schema: str
    version: int
    batch_id: str
    items: list[RunItemState]
    created_at: str
    updated_at: str
    flow_project: FlowProjectReference | None = None
    flow_project_required: bool = False

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in sorted(VALID_STATUSES)}
        for item in self.items:
            item.validate()
            result[item.status] += 1
        return result
