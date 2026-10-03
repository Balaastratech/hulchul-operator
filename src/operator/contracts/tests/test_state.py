"""Serializable state and exact review/approval binding tests."""

import copy
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from src.operator.contracts import (
    BrowserPort, Command, DataSourcePort, Goal, JobState, ReviewSnapshot, RunState, Rules,
)


def test_run_json_roundtrip_has_no_handles_or_raw_tokens():
    run = RunState(run_id="synthetic-run", goal="Fill one fixture",
                   jobs={"fixture": JobState(job_id="fixture", url="http://localhost:8000/apply")})
    assert RunState.model_validate_json(run.model_dump_json()) == run
    with pytest.raises(ValidationError):
        RunState(run_id="r", goal="g", browser=object())
    with pytest.raises(ValidationError):
        Command(command_id="c", run_id="r", action="resume", token="raw capability")


def test_defaults_are_independent_and_safe():
    first, second = Rules(), Rules()
    first.blocked_companies.append("Fictional Company")
    assert second.blocked_companies == []
    assert Goal().mode == "dry_run"
    assert Rules().eeo_policy == "ask_user"
    with pytest.raises(ValidationError):
        Goal(max_apply=0)


def test_review_hash_is_order_independent_and_changes_with_actual_values():
    review = ReviewSnapshot(fields=[dict(field_key="b", actual=False),
                                    dict(field_key="a", actual="Synthetic")],
                            generated_texts={"essay": "Draft"})
    changed = copy.deepcopy(review)
    changed.fields.reverse()
    changed.screenshots = ["new-local-evidence.png"]
    assert changed.content_hash() == review.content_hash()
    changed.fields[0].actual = "Edited"
    assert changed.content_hash() != review.content_hash()
    changed = copy.deepcopy(review)
    changed.generated_texts["essay"] = "Another draft"
    assert changed.content_hash() != review.content_hash()


def test_duplicate_review_keys_rejected():
    with pytest.raises(ValidationError):
        ReviewSnapshot(fields=[dict(field_key="same"), dict(field_key="same")])


def test_approval_requires_exact_binding():
    with pytest.raises(ValidationError):
        Command(command_id="c", run_id="r", action="approve")
    command = Command(command_id="c", run_id="r", job_id="j", action="approve",
                      token_hash="a" * 64, snapshot_hash="b" * 64)
    assert Command.model_validate_json(command.model_dump_json()) == command


def test_port_forward_types_resolve_for_adapter_introspection():
    assert get_type_hints(BrowserPort.read_review)["return"] is ReviewSnapshot
    assert get_type_hints(DataSourcePort.load)["return"].__name__ == "DataSnapshot"
