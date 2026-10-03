"""Pydantic models for the shared data shapes (Command, Event, ReviewSnapshot).

Built by hand from the JSON Schemas exported by the contracts package
(Command.schema.json, Event.schema.json, ReviewSnapshot.schema.json) and from the
hash algorithm written down in CONTROL_PLANE_API.md section 2.3 (W5).

SWAP POINT: every schema-derived model lives in this single module. Once the
contracts are merged to main, replace the class bodies below with
``from src.operator.contracts import Command, Event, ReviewSnapshot, RunStatus``
(a one-file change). Nothing else in control_plane/ defines these shapes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

HEX64_PATTERN = r"^[0-9a-f]{64}$"


class Contract(BaseModel):
    """Every schema has additionalProperties=false."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


EVENT_IDS = tuple(f"E{n:02d}" for n in range(1, 16))

COMMAND_ACTIONS = (
    "approve",
    "edit",
    "reject",
    "skip",
    "pause",
    "resume",
    "cancel",
    "answer",
    "handoff_done",
)


class FieldResult(Contract):
    field_key: str = Field(min_length=1)
    intended: JsonValue = None
    actual: JsonValue = None
    matched: bool = False
    escalated: bool = False
    generated: bool = False
    reason: str | None = None


class Upload(Contract):
    field_key: str
    name: str
    sha256: str = Field(pattern=HEX64_PATTERN)


class ReviewSnapshot(Contract):
    fields: list[FieldResult] = Field(default_factory=list)
    uploads: list[Upload] = Field(default_factory=list)
    generated_texts: dict[str, str] = Field(default_factory=dict)
    unanswered: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> "ReviewSnapshot":
        for items in (self.fields, self.uploads):
            keys = [item.field_key for item in items]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate review field key")
        return self

    def content_hash(self) -> str:
        """Identity of what the human reviews; `screenshots` is excluded (W5)."""
        content = self.model_dump(mode="json", exclude={"screenshots"})
        content["fields"] = sorted(content["fields"], key=lambda i: i["field_key"])
        content["uploads"] = sorted(content["uploads"], key=lambda i: i["field_key"])
        content["unanswered"] = sorted(content["unanswered"])
        encoded = json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class Event(Contract):
    event_id: Literal[
        "E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08",
        "E09", "E10", "E11", "E12", "E13", "E14", "E15",
    ]  # fmt: skip
    run_id: str
    job_id: str | None = None
    message: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class Command(Contract):
    command_id: str = Field(min_length=1)
    run_id: str
    job_id: str | None = None
    action: Literal[
        "approve", "edit", "reject", "skip", "pause", "resume", "cancel",
        "answer", "handoff_done",
    ]  # fmt: skip
    token_hash: str | None = Field(default=None, pattern=HEX64_PATTERN)
    snapshot_hash: str | None = Field(default=None, pattern=HEX64_PATTERN)
    field_key: str | None = None
    value: JsonValue = None

    @model_validator(mode="after")
    def _required_bindings(self) -> "Command":
        # Mirrors what the worker's own Command validator demands (schema notes).
        if self.action == "approve" and not all(
            (self.job_id, self.token_hash, self.snapshot_hash)
        ):
            raise ValueError("approve requires job_id, token_hash, snapshot_hash")
        if self.action in {"edit", "answer"} and not all((self.job_id, self.field_key)):
            raise ValueError("edit/answer requires job_id and field_key")
        return self
