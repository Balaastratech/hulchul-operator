"""Native subgraph gates and durable state restoration with fake Ports."""

from langgraph.types import Command as Resume
from src.operator.contracts import RunState
from src.operator.graph import sqlite_graph
from src.operator.graph.tests.fakes import services_at


def start(graph):
    return graph.invoke({"run": RunState(run_id="r", goal="Fill one fixture").model_dump(mode="json")},
                        {"configurable": {"thread_id": "r"}, "recursion_limit": 150})


def test_review_gate_survives_connection_restart_without_refills(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
        assert services.browser.submissions == 0
    services.llm.calls = 0
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = graph.invoke(Resume(resume={"command_id": "reject-1", "run_id": "r", "job_id": "fixture",
                                            "action": "reject"}), config)
        assert result["run"]["status"] == "COMPLETED"
        assert result["run"]["jobs"]["fixture"]["status"] == "REJECTED_BY_USER"
        assert services.browser.executions == ["name"]
        assert services.llm.calls == 0
    services.close()


def test_pause_gate_never_touches_browser_until_resume(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        start(graph)
        result = graph.invoke(Resume(resume={"command_id": "pause", "run_id": "r", "action": "pause"}), config)
        assert result["__interrupt__"][0].value["kind"] == "paused"
        assert services.browser.executions == ["name"]
        result = graph.invoke(Resume(resume={"command_id": "resume", "run_id": "r", "action": "resume"}), config)
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
    services.close()


def test_captcha_handoff_has_no_executor_attempts(tmp_path):
    services = services_at(tmp_path)
    from src.operator.contracts import PageState
    services.browser.page_state = PageState.CAPTCHA
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "handoff"
        assert services.browser.executions == []
        services.browser.page_state = PageState.FORM
        result = graph.invoke(Resume(resume={"command_id": "done", "run_id": "r", "action": "handoff_done"}),
                              {"configurable": {"thread_id": "r"}, "recursion_limit": 150})
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
    services.close()
