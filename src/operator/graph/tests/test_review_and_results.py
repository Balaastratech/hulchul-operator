"""Edit invalidation, malicious plans and truthful terminal summaries."""

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.types import Command as Resume

from src.operator.contracts import FillAction, JobState, JobStatus, RunState
from src.operator.graph import sqlite_graph
from src.operator.graph.nodes.aggregate import aggregate
from src.operator.graph.runtime import AnswerPlan
from src.operator.graph.tests.fakes import FakeLLM, services_at
from src.operator.graph.tests.test_graph import start


def test_one_field_edit_invalidates_old_approval_and_requires_new_hash(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        first = start(graph)["__interrupt__"][0].value
        services.ledger.record_approval(
            "r",
            "fixture",
            "a" * 64,
            first["snapshot_hash"],
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        edited = graph.invoke(
            Resume(
                resume={
                    "command_id": "edit",
                    "run_id": "r",
                    "job_id": "fixture",
                    "action": "edit",
                    "field_key": "name",
                    "value": "Edited Synthetic",
                }
            ),
            config,
        )
        gate = edited["__interrupt__"][0].value
        assert gate["snapshot_hash"] != first["snapshot_hash"]
        assert services.browser.executions == ["name", "name"]
        assert not services.ledger.consume_approval(
            "r", "fixture", "a" * 64, first["snapshot_hash"]
        )
        services.ledger.record_approval(
            "r",
            "fixture",
            "b" * 64,
            gate["snapshot_hash"],
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        result = graph.invoke(
            Resume(
                resume={
                    "command_id": "new-approval",
                    "run_id": "r",
                    "job_id": "fixture",
                    "action": "approve",
                    "token_hash": "b" * 64,
                    "snapshot_hash": gate["snapshot_hash"],
                }
            ),
            config,
        )
        assert result["run"]["status"] == "COMPLETED"
        assert services.browser.submissions == 1
    services.close()


def test_forced_invented_fact_is_escalated_before_any_executor_action(tmp_path):
    services = services_at(tmp_path)

    class MaliciousLLM(FakeLLM):
        async def structured(self, prompt, response_model):
            if response_model is AnswerPlan:
                return AnswerPlan(
                    actions=[
                        FillAction(
                            field_key="name",
                            action="fill",
                            value="Invented",
                            source="profile.name",
                        )
                    ]
                )
            return await super().structured(prompt, response_model)

    services.llm = MaliciousLLM()
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "answer"
        assert services.browser.executions == []
        assert services.browser.submissions == 0
    services.close()


@pytest.mark.parametrize(
    "statuses,result",
    [
        ([JobStatus.SUBMITTED_VERIFIED, JobStatus.FAILED], "PARTIAL"),
        ([JobStatus.FAILED], "BLOCKED"),
        ([JobStatus.REJECTED_BY_USER], "COMPLETED"),
        ([JobStatus.CANCELLED], "CANCELLED"),
    ],
)
def test_summary_never_hides_incomplete_jobs(tmp_path, statuses, result):
    services = services_at(tmp_path)
    run = RunState(
        run_id="r",
        goal="Synthetic",
        jobs={
            str(index): JobState(
                job_id=str(index),
                url="http://localhost:8000",
                status=status,
                blockers=["Synthetic blocker"] if status == JobStatus.FAILED else [],
            )
            for index, status in enumerate(statuses)
        },
    )
    final = aggregate({"run": run.model_dump(mode="json")}, services)
    assert final["run"]["status"] == result
    assert len(final["run"]["jobs"]) == len(statuses)
    services.close()


def test_review_edit_checks_boolean_control_instead_of_typing(tmp_path):
    from src.operator.contracts import FieldSpec

    services = services_at(tmp_path)
    browser = services.browser
    browser.values["remote"] = False
    original_extract = browser.extract_fields
    original_execute = browser.execute

    async def extract():
        return await original_extract() + [
            FieldSpec(
                id="remote",
                key="remote",
                label="Remote work preference",
                type="checkbox",
            )
        ]

    async def execute(action):
        if action.field_key == "remote":
            assert action.action == "check", "checkbox cannot accept text entry"
            assert isinstance(action.value, bool)
        return await original_execute(action)

    browser.extract_fields = extract
    browser.execute = execute
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        gate = start(graph)["__interrupt__"][0].value
        result = graph.invoke(
            Resume(
                resume={
                    "command_id": "edit-check",
                    "run_id": "r",
                    "job_id": "fixture",
                    "action": "edit",
                    "field_key": "remote",
                    "value": True,
                    "snapshot_hash": gate["snapshot_hash"],
                }
            ),
            config,
        )
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert browser.values["remote"] is True
        assert browser.executions == ["name", "remote"]
        assert (
            result["__interrupt__"][0].value["snapshot_hash"] != gate["snapshot_hash"]
        )
        assert browser.submissions == 0
        from worker.main import _current_run

        run = _current_run(graph.get_state(config, subgraphs=True))
        edited_action = next(
            item for item in run.jobs["fixture"].actions if item.field_key == "remote"
        )
        assert edited_action.action == "check"
        assert edited_action.value is True
        event = next(
            item for item in reversed(services.channel.events) if item.event_id == "E08"
        )
        assert (
            event.payload["snapshot_hash"] == run.jobs["fixture"].review_snapshot_hash
        )
        assert "review" in event.payload
    services.close()


def test_edit_command_for_old_snapshot_never_touches_current_form(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        start(graph)
        result = graph.invoke(
            Resume(
                resume={
                    "command_id": "stale-edit",
                    "run_id": "r",
                    "job_id": "fixture",
                    "action": "edit",
                    "field_key": "name",
                    "value": "Stale",
                    "snapshot_hash": "f" * 64,
                }
            ),
            config,
        )
        assert result["run"]["status"] == "BLOCKED"
        assert services.browser.executions == ["name"]
        assert services.browser.values["name"] == "Synthetic"
        assert services.browser.submissions == 0
    services.close()
