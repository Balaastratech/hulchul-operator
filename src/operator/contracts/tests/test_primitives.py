"""Validation of the documented shared browser primitives."""

import json

import pytest
from pydantic import ValidationError

from src.operator.contracts import FieldSpec, JobStatus, PageState


def test_field_round_trip_preserves_browser_readback():
    field = FieldSpec(
        id="location",
        key="Location|select|contact|0",
        label="Location",
        group="contact",
        type="select",
        options=["Remote", "Office"],
        required=True,
        current_value="Remote",
    )
    assert FieldSpec.model_validate_json(field.model_dump_json()) == field
    assert field.model_dump()["current_value"] == "Remote"


def test_field_rejects_missing_stable_key_and_unknown_attributes():
    with pytest.raises(ValidationError):
        FieldSpec(id="a", key="", label="A", type="text")
    with pytest.raises(ValidationError):
        FieldSpec(id="a", key="A|text||0", label="A", type="text", secret="bad")


def test_readback_allows_empty_and_multivalue_controls():
    for value in (None, "", False, ["Python", "SQL"]):
        field = FieldSpec(
            id="a", key="a|checkbox||0", label="A", type="checkbox", current_value=value
        )
        assert json.loads(field.model_dump_json())["current_value"] == value


def test_documented_status_vocabulary():
    assert {state.value for state in PageState} == {
        "FORM",
        "LOGIN",
        "CAPTCHA",
        "CLOSED",
        "UNSUPPORTED",
        "CONFIRMATION",
        "UNKNOWN",
    }
    assert JobStatus.SUBMITTING.value == "SUBMITTING"
    assert "PAUSED" not in {state.value for state in JobStatus}
    assert len(JobStatus) == 17
