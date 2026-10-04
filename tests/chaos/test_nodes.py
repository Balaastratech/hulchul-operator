"""Every executable graph node, before execution and after return before checkpoint."""

import json

import pytest

from scripts.demo_g3 import exercise
from tests.chaos.flow import RecoveryFailure, finish, reach_review, safety
from tests.chaos.harness import ChaosScenario

NODES = [
    "intake",
    "load_data",
    "select_jobs",
    "next_job",
    "aggregate",
    "final_report",
    "application",
    "open_application",
    "classify_page",
    "extract_fields",
    "plan_answers",
    "policy_check",
    "execute_fill",
    "verify_fill",
    "repair",
    "click_next",
    "build_review",
    "review_gate",
    "human_handoff",
    "ask_user",
    "paused",
    "apply_edit",
    "pre_submit_check",
    "action_boundary",
    "submit",
]


KNOWN_RECOVERY_FAILURES = {
    ("apply_edit", "before"): "RT-08",
    ("apply_edit", "after"): "RT-08",
    ("repair", "before"): "RT-05",
    ("repair", "after"): "RT-05",
    ("click_next", "before"): "RT-05",
    ("click_next", "after"): "RT-07",
    ("build_review", "before"): "RT-05",
    ("build_review", "after"): "RT-05",
    ("review_gate", "before"): "RT-05",
    ("paused", "before"): "RT-06",
}


def test_node_inventory(tmp_path):
    """The parent application node is a container; every executable child is listed."""
    from src.operator.graph import sqlite_graph
    from src.operator.graph.subgraph_application import build_application
    from src.operator.graph.tests.fakes import services_at

    services = services_at(tmp_path)
    try:
        with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
            parent = set(graph.get_graph().nodes) - {"__start__", "__end__"}
            child = set(build_application(services).get_graph().nodes) - {
                "__start__",
                "__end__",
            }
            assert parent | child == set(NODES)
    finally:
        services.close()


@pytest.mark.chaos
@pytest.mark.parametrize("crash", ["before_claim", "after_claim"])
def test_submit_claim_window(tmp_path, crash):
    """Actual SIGKILL-equivalent before/after durable submit intent, never a retry click."""
    with ChaosScenario(tmp_path) as scenario:
        # Original harness implements the two internal submit barriers.
        from scripts.demo_g3 import Scenario

        scenario.worker = Scenario.worker.__get__(scenario)
        original_post = scenario.post
        filled = []

        def post(action, fields, expected=200):
            if action == "approve" and expected == 200:
                filled.append(
                    (tmp_path / "operations.txt")
                    .read_text()
                    .splitlines()
                    .count("execute")
                )
            return original_post(action, fields, expected)

        scenario.post = post
        exercise(scenario, crash=crash, timeline=lambda _: None)
        assert (tmp_path / "operations.txt").read_text().splitlines().count(
            "execute"
        ) == filled[0]
        safety(scenario)


@pytest.mark.chaos
@pytest.mark.parametrize(
    "node,phase",
    [
        pytest.param(
            node,
            phase,
            marks=(
                pytest.mark.xfail(
                    strict=True,
                    raises=RecoveryFailure,
                    reason=KNOWN_RECOVERY_FAILURES[(node, phase)]
                    + ": recovery fails to reach terminal status",
                )
                if (node, phase) in KNOWN_RECOVERY_FAILURES
                else ()
            ),
        )
        for node in NODES
        for phase in ("before", "after")
    ],
)
def test_boundary_restart(tmp_path, node, phase):
    """Kill/restart real worker; observe actual DOM inputs, counter and token replay."""
    with ChaosScenario(tmp_path) as scenario:
        scenario.node, scenario.phase = node, phase
        scenario.variant = (
            "repair"
            if node == "repair"
            else "answer"
            if node == "ask_user"
            else "normal"
        )
        try:
            reach_review(scenario)
            finish(scenario, pause=node == "paused", edit=node == "apply_edit")
            assert (tmp_path / "fault-fired").read_text() == node + ":" + phase
        finally:
            outcome = safety(scenario, edited=node == "apply_edit")
            (tmp_path / "outcome.json").write_text(json.dumps(outcome))
            assert (tmp_path / "fault-fired").read_text() == node + ":" + phase
