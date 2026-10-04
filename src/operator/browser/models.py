"""Browser primitives and data models, matching canonical contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    """Base contract rejecting unknown fields and validating assignments."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PageState(StrEnum):
    """Deterministic page classifications."""
    FORM = "FORM"
    LOGIN = "LOGIN"
    CAPTCHA = "CAPTCHA"
    CLOSED = "CLOSED"
    UNSUPPORTED = "UNSUPPORTED"
    CONFIRMATION = "CONFIRMATION"
    UNKNOWN = "UNKNOWN"


class FieldSpec(Contract):
    """Extracted form control with stable identifier key."""
    id: str
    key: str = Field(min_length=1)
    label: str
    group: str = ""
    type: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    required: bool = False
    current_value: JsonValue = None
    selector: str | None = None
    is_combobox: bool = False
    placeholder: str | None = None
    pattern: str | None = None
    min: str | None = None
    max: str | None = None
    maxlength: int | None = None


class FillAction(Contract):
    """Action to execute on a form control."""
    field_key: str = Field(min_length=1)
    action: Literal["fill", "select", "check", "upload_resume", "ask_user", "skip"]
    value: JsonValue = None
    question: str | None = None
    generated: bool = False
    derived: bool = False
    source: str | None = None

    @model_validator(mode="after")
    def validate_question(self) -> "FillAction":
        if self.action == "ask_user" and not self.question:
            raise ValueError("ask_user requires question")
        return self


class ActionResult(Contract):
    """Structured outcome of executing an individual action."""
    field_key: str
    success: bool
    actual: JsonValue = None
    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)


class FieldResult(Contract):
    """Verification outcome comparing intended vs actual field value."""
    field_key: str = Field(min_length=1)
    intended: JsonValue = None
    actual: JsonValue = None
    matched: bool = False
    escalated: bool = False
    reason: str | None = None
    generated: bool = False
    derived: bool = False
    source: str | None = None


class FillReport(Contract):
    """Verification report over all actions executed."""
    fields: list[FieldResult] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all(item.matched for item in self.fields)

    @property
    def unresolved(self) -> list[str]:
        return [item.field_key for item in self.fields if not item.matched]
