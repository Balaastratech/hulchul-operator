"""Exactly-once intent semantics with persistent ledger and synthetic browser."""

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.types import Command as Resume
from src.operator.contracts import JobStatus, RunState
from src.operator.graph import sqlite_graph
from src.operator.graph.nodes.submit import submit
from src.operator.graph.tests.fakes import services_at


def at_approval(graph, services):
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    result = graph.invoke({"run": RunState(run_id="r", goal="Fill a fixture").model_dump(mode="json")}, config)
    gate = result["__interrupt__"][0].value
    services.ledger.record_approval("r", "fixture", "a" * 64, gate["snapshot_hash"],
                                   datetime.now(timezone.utc) + timedelta(minutes=5))
    command = {"command_id": "approve", "run_id": "r", "job_id": "fixture", "action": "approve",
               "token_hash": "a" * 64, "snapshot_hash": gate["snapshot_hash"]}
    return config, command


def test_fixture_click_once_and_verified_after_restart(tmp_path):
    services = services_at(tmp_path)
    services.submit_handler = submit
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        config, command = at_approval(graph, services)
        result = graph.invoke(Resume(resume=command), config)
        assert result["run"]["status"] == "COMPLETED"
        assert services.browser.submissions == 1
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        graph.invoke(None, config)
        assert services.browser.submissions == 1
    services.close()


def test_crash_after_durable_intent_before_click_never_reclicks(tmp_path):
    services = services_at(tmp_path)
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        config, command = at_approval(graph, services)
        assert services.ledger.consume_approval("r", "fixture", "a" * 64, command["snapshot_hash"])
        assert services.ledger.begin_submission("r", "fixture", command["snapshot_hash"])
        # Reconstruct the last serializable job state as a worker would after intent.
        state = dict(graph.get_state(config, subgraphs=True).tasks[0].state.values)
        state["run"]["active_job_id"] = "fixture"
        result = submit(state, services)
        assert result["run"]["jobs"]["fixture"]["status"] == "SUBMITTED_UNVERIFIED"
        assert services.browser.submissions == 0
    services.close()


def test_crash_after_click_only_verifies(tmp_path):
    services = services_at(tmp_path)
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        config, command = at_approval(graph, services)
        assert services.ledger.consume_approval("r", "fixture", "a" * 64, command["snapshot_hash"])
        assert services.ledger.begin_submission("r", "fixture", command["snapshot_hash"])
        services.call(services.browser.submit())
        state = dict(graph.get_state(config, subgraphs=True).tasks[0].state.values)
        result = submit(state, services)
        assert result["run"]["jobs"]["fixture"]["status"] == "SUBMITTED_VERIFIED"
        assert services.browser.submissions == 1
    services.close()


def test_missing_live_target_guard_blocks_click(tmp_path):
    services = services_at(tmp_path)
    services.submission_urls = None
    services.submit_handler = submit
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        config, command = at_approval(graph, services)
        with pytest.raises(PermissionError):
            graph.invoke(Resume(resume=command), config)
        assert services.browser.submissions == 0
        assert services.ledger.get_status("r", "fixture") == JobStatus.APPROVED
    services.close()
