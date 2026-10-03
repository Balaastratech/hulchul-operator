"""Candidate snapshots, review binding and serializable graph state."""

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .primitives import (
    Contract,
    FieldResult,
    FieldSpec,
    FillAction,
    FillReport,
    JobStatus,
    PageState,
)


class RunStatus(StrEnum):
    """Active run states and AGENT_GRAPH section 5 result vocabulary."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class Goal(Contract):
    """Validated model interpretation of the human's goal."""

    max_apply: int = Field(default=1, ge=1)
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    mode: Literal["dry_run", "normal"] = "dry_run"
    notes: list[str] = Field(default_factory=list)
    permits_filling: bool = True


class Profile(Contract):
    """Explicit candidate facts; absent answers remain unknown."""

    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    phone: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    education: list[dict[str, JsonValue]] = Field(default_factory=list)
    experience: list[dict[str, JsonValue]] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    sponsorship: bool | None = None
    relocation: bool | None = None
    notice_period: str | None = None
    location_prefs: list[str] = Field(default_factory=list)


class Rules(Contract):
    """Authoritative hard constraints; missing values grant no authority."""

    prose: str = ""
    min_salary: float | None = Field(default=None, ge=0)
    salary_expectation: str | None = None
    remote_only: bool = False
    blocked_companies: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    max_applications_per_run: int = Field(default=1, ge=1)
    eeo_policy: Literal["ask_user", "leave_blank", "decline"] = "ask_user"
    allowed_uploads: list[str] = Field(default_factory=lambda: ["resume"])
    confirm_shortlist: bool = False


class Answer(Contract):
    """One source-attributed answer library row."""

    pattern: str = Field(min_length=1)
    answer: str
    sensitivity: Literal["normal", "sensitive", "legal"] = "normal"
    source: str = Field(min_length=1)
    updated: datetime | None = None


class AnswerLibrary(Contract):
    """Explicit answers, never inferred demographic facts."""

    answers: list[Answer] = Field(default_factory=list)


class JobPosting(Contract):
    """Untrusted posting data; fixture authority is supplied separately."""

    job_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    company: str = Field(min_length=1)
    title: str
    apply_url: str | None = None
    description: str = ""
    remote: bool | None = None
    salary: float | None = Field(default=None, ge=0)
    reasons: list[str] = Field(default_factory=list)
    quarantined: bool = False


class DataSnapshot(Contract):
    """Validated immutable source bundle used for one run."""

    profile: Profile
    rules: Rules
    answer_library: AnswerLibrary
    resume_path: str
    resume_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    jobs: list[JobPosting] = Field(default_factory=list)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_hashes: dict[str, str] = Field(default_factory=dict)


class Upload(Contract):
    """Reviewed upload identity binds the actual file's content hash."""

    field_key: str
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewSnapshot(Contract):
    """Canonical read-back values used to bind human approval."""

    fields: list[FieldResult] = Field(default_factory=list)
    uploads: list[Upload] = Field(default_factory=list)
    generated_texts: dict[str, str] = Field(default_factory=dict)
    unanswered: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)

    def content_hash(self) -> str:
        """Hash values/uploads/flags; evidence filenames do not change form identity."""
        content = self.model_dump(mode="json", exclude={"screenshots"})
        content["fields"] = sorted(
            content["fields"], key=lambda item: item["field_key"]
        )
        content["uploads"] = sorted(
            content["uploads"], key=lambda item: item["field_key"]
        )
        content["unanswered"] = sorted(content["unanswered"])
        encoded = json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def unique_keys(self) -> "ReviewSnapshot":
        """Duplicate read-back keys cannot identify a reviewed control."""
        for items in (self.fields, self.uploads):
            keys = [item.field_key for item in items]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate review field key")
        return self


class ApprovalState(Contract):
    """Only a capability digest and consumption timestamp enter checkpoints."""

    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    used_at: datetime | None = None


class JobState(Contract):
    """AGENT_GRAPH section 3 plus explicit serializable resume metadata."""

    job_id: str
    url: str
    status: JobStatus = JobStatus.QUEUED
    page_state: PageState = PageState.UNKNOWN
    fields: list[FieldSpec] = Field(default_factory=list)
    actions: list[FillAction] = Field(default_factory=list)
    fill_report: FillReport | None = None
    review_snapshot_hash: str | None = None
    approval: ApprovalState | None = None
    evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    paused: bool = False
    review_snapshot: ReviewSnapshot | None = None
    repair_attempts: int = Field(default=0, ge=0, le=2)
    browser_target_id: str | None = None


class Usage(Contract):
    """Visible resource usage, never credentials."""

    llm_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_inr: float = Field(default=0, ge=0)


class RunState(Contract):
    """Checkpointable state containing no browser objects or raw capabilities."""

    run_id: str = Field(min_length=1)
    goal: str
    goal_parsed: Goal | None = None
    data_snapshot_hash: str | None = None
    mode: Literal["dry_run", "normal"] = "dry_run"
    status: RunStatus = RunStatus.QUEUED
    shortlist: list[JobPosting] = Field(default_factory=list)
    jobs: dict[str, JobState] = Field(default_factory=dict)
    cdp_endpoint: str | None = None
    active_job_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    events_cursor: int = Field(default=0, ge=0)
    paused: bool = False


class Event(Contract):
    """E01-E15 delivery envelope; callers redact sensitive payloads."""

    event_id: Literal[
        "E01",
        "E02",
        "E03",
        "E04",
        "E05",
        "E06",
        "E07",
        "E08",
        "E09",
        "E10",
        "E11",
        "E12",
        "E13",
        "E14",
        "E15",
    ]
    run_id: str
    job_id: str | None = None
    message: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class Command(Contract):
    """Verified control-plane command; raw action tokens never enter state."""

    command_id: str = Field(min_length=1)
    run_id: str
    job_id: str | None = None
    action: Literal[
        "approve",
        "edit",
        "reject",
        "skip",
        "pause",
        "resume",
        "cancel",
        "answer",
        "handoff_done",
    ]
    token_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    field_key: str | None = None
    value: JsonValue = None

    @model_validator(mode="after")
    def required_bindings(self) -> "Command":
        """Approval and edits must identify exactly what they authorize."""
        if self.action == "approve" and not all(
            (self.job_id, self.token_hash, self.snapshot_hash)
        ):
            raise ValueError("approve requires job_id, token_hash, snapshot_hash")
        if self.action in {"edit", "answer"} and not all((self.job_id, self.field_key)):
            raise ValueError("edit/answer requires job_id and field_key")
        return self
