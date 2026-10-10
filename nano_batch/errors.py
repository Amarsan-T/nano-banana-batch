from __future__ import annotations


FLOW_USAGE_LIMIT_REACHED = "FLOW_USAGE_LIMIT_REACHED"
FLOW_POLICY_REJECTED = "FLOW_POLICY_REJECTED"
FLOW_UNUSUAL_ACTIVITY = "FLOW_UNUSUAL_ACTIVITY"
FLOW_GENERIC_GENERATION_FAILED = "FLOW_GENERIC_GENERATION_FAILED"
FLOW_HIGH_DEMAND = "FLOW_HIGH_DEMAND"
FLOW_PROJECT_CREATE_CONTROL_UNPROVEN = "FLOW_PROJECT_CREATE_CONTROL_UNPROVEN"
FLOW_PROJECT_CREATION_FAILED = "FLOW_PROJECT_CREATION_FAILED"
FLOW_PROJECT_IDENTITY_UNPROVEN = "FLOW_PROJECT_IDENTITY_UNPROVEN"
FLOW_PROJECT_REOPEN_FAILED = "FLOW_PROJECT_REOPEN_FAILED"
FLOW_GENERATION_NOT_SUBMITTED = "FLOW_GENERATION_NOT_SUBMITTED"
CANDIDATE_CORRELATION_UNPROVEN = "CANDIDATE_CORRELATION_UNPROVEN"
CANDIDATE_UUID_OWNERSHIP_CONFLICT = "CANDIDATE_UUID_OWNERSHIP_CONFLICT"
WAITING_FOR_FLOW_LIMIT_RESET = "waiting_for_flow_limit_reset"
GENERATION_SUBMISSION_NOT_SUBMITTED = "not_submitted"
GENERATION_SUBMISSION_AMBIGUOUS_OR_SENT = "ambiguous_or_sent"
GENERATION_INTERRUPT_EVIDENCE_SCHEMA = "nano-banana-generation-interrupt/v1"


class GenerationPreflightError(RuntimeError):
    """A coded failure proven to occur before generation activation."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.order: int | None = None


class TemporaryGenerationBlock(RuntimeError):
    """A persisted, auto-resumable external block on generation."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        state_status: str,
        attempt_activated: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.state_status = state_status
        self.attempt_activated = attempt_activated
        self.order: int | None = None


class PolicyGenerationRejected(RuntimeError):
    """The exact current attempt was rejected by Flow's policy card."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{FLOW_POLICY_REJECTED}: {message}")
        self.code = FLOW_POLICY_REJECTED
        self.order: int | None = None


class UnusualActivityGenerationFailure(RuntimeError):
    """The exact current attempt was rejected by Flow for unusual activity."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{FLOW_UNUSUAL_ACTIVITY}: {message}")
        self.code = FLOW_UNUSUAL_ACTIVITY
        self.order: int | None = None


class GenericGenerationFailure(RuntimeError):
    """The exact current attempt ended with Flow's generic generation failure."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{FLOW_GENERIC_GENERATION_FAILED}: {message}")
        self.code = FLOW_GENERIC_GENERATION_FAILED
        self.order: int | None = None


class HighDemandGenerationFailure(RuntimeError):
    """The current attempt failed due to demand; only explicit manual Retry."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{FLOW_HIGH_DEMAND}: {message}")
        self.code = FLOW_HIGH_DEMAND
        self.order: int | None = None
