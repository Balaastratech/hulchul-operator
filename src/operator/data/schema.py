"""Candidate and run data schemas, compatible with Codex contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Contract(BaseModel):
    """Base contract rejecting unknown fields."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Profile(Contract):
    """Explicit candidate facts; absent answers remain unknown."""
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    phone: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    education: list[dict[str, Any]] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)
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
    """Untrusted posting data."""
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
