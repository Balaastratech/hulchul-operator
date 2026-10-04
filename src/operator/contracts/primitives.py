"""Serializable browser contracts; no browser handles or executable plans."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    """Reject unknown fields and validate assignments at every boundary."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PageState(StrEnum):
    """Documented deterministic page classifications."""

    FORM = "FORM"
    LOGIN = "LOGIN"
    CAPTCHA = "CAPTCHA"
    CLOSED = "CLOSED"
    UNSUPPORTED = "UNSUPPORTED"
    CONFIRMATION = "CONFIRMATION"
    UNKNOWN = "UNKNOWN"


class JobStatus(StrEnum):
    """AGENT_GRAPH section 4; pause is a separate overlay."""

    QUEUED = "QUEUED"
    OPENED = "OPENED"
    FILLING = "FILLING"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    NEEDS_ANSWER = "NEEDS_ANSWER"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED_VERIFIED = "SUBMITTED_VERIFIED"
    SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    QUARANTINED = "QUARANTINED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    REJECTED_BY_USER = "REJECTED_BY_USER"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    CANCELLED = "CANCELLED"


class FieldSpec(Contract):
    """One extracted control with an extractor-assigned stable key."""

    id: str
    key: str = Field(min_length=1)
    label: str
    group: str = ""
    type: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    required: bool = False
    current_value: JsonValue = None


class FillAction(Contract):
    """Planner proposal; policy must authorize it before execution."""

    field_key: str = Field(min_length=1)
    action: Literal["fill", "select", "check", "upload_resume", "ask_user", "skip"]
    value: JsonValue = None
    question: str | None = None
    generated: bool = False
    derived: bool = False
    source: str | None = None

    @model_validator(mode="after")
    def validate_question(self) -> "FillAction":
        """An escalation must carry a concrete question."""
        if self.action == "ask_user" and not self.question:
            raise ValueError("ask_user requires question")
        return self


class FieldResult(Contract):
    """Read-back comparison, including a visible escalation reason."""

    field_key: str = Field(min_length=1)
    intended: JsonValue = None
    actual: JsonValue = None
    matched: bool = False
    escalated: bool = False
    reason: str | None = None
    generated: bool = False


class FillReport(Contract):
    """Verification result; unresolved fields cannot be called verified."""

    fields: list[FieldResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        """True when all reported fields match; escalation is not a match."""
        return all(item.matched for item in self.fields)

    @property
    def unresolved(self) -> list[str]:
        """Stable keys requiring repair or human action."""
        return [item.field_key for item in self.fields if not item.matched]


class ActionResult(Contract):
    """Executor outcome, safe to persist as evidence."""

    field_key: str
    success: bool
    actual: JsonValue = None
    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)


class SubmissionResult(Contract):
    """Observed confirmation; absence of evidence is never success."""

    verified: bool
    confirmation: str | None = None
    application_id: str | None = None
    evidence: list[str] = Field(default_factory=list)
