"""Contract rejection paths and deterministic schema export."""

import json

import pytest
from pydantic import ValidationError

from src.operator.contracts import (
    BrowserPort, ChannelPort, DataSourcePort, FillAction, FillReport, LedgerPort, LLMPort,
)
from src.operator.contracts.export_schema import export_schemas


@pytest.mark.parametrize("action", ["submit", "solve_captcha", "send_message", "payment"])
def test_planner_cannot_encode_irreversible_or_forbidden_actions(action):
    with pytest.raises(ValidationError):
        FillAction(field_key="button", action=action)


def test_question_requires_explicit_prompt():
    with pytest.raises(ValidationError):
        FillAction(field_key="sponsorship", action="ask_user")
    assert FillAction(field_key="sponsorship", action="ask_user",
                      question="Will you need sponsorship?").question


def test_escalation_is_truthfully_unverified():
    report = FillReport(fields=[dict(field_key="legal", escalated=True, reason="Human action")])
    assert not report.verified
    assert report.unresolved == ["legal"]


def test_protocols_are_runtime_checkable_structural_ports():
    for protocol in (BrowserPort, ChannelPort, DataSourcePort, LedgerPort, LLMPort):
        assert not isinstance(object(), protocol)


def test_schema_export_is_reproducible_and_contains_validation(tmp_path):
    paths = export_schemas(tmp_path)
    first = {path.name: path.read_bytes() for path in paths}
    assert first == {path.name: path.read_bytes() for path in export_schemas(tmp_path)}
    schema = json.loads((tmp_path / "FillAction.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert "submit" not in schema["properties"]["action"]["enum"]
