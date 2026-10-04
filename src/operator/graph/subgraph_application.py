"""Explicit application edges; model output never selects a node."""

from importlib import import_module

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.operator.contracts import JobStatus

from .runtime import (
    ControlUnavailable,
    GraphState,
    Services,
    active_job,
    read_run,
    update,
)


def build_application(services: Services) -> CompiledStateGraph:
    """Return an application subgraph; parent checkpointer propagates to gates."""
    graph = StateGraph(GraphState)
    names = [
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
    ]
    for name in names:
        function = getattr(import_module(f"src.operator.graph.nodes.{name}"), name)

        def guarded(state, fn=function, node=name):
            try:
                return fn(state, services)
            except (GraphInterrupt, ControlUnavailable):
                raise
            except Exception as error:  # noqa: BLE001 - Port errors fail closed without secret-bearing messages
                run = read_run(state)
                job = active_job(run)
                job.status = JobStatus.FAILED
                job.blockers.append(
                    f"{node}: {type(error).__name__}; inspect node and resume manually"
                )
                return update(run, route="end")

        graph.add_node(name, guarded)

    def submit(state):
        if services.submit_handler is None:
            from .nodes.submit import submit as submit_node

            return submit_node(state, services)
        return services.submit_handler(state, services)

    graph.add_node("submit", submit)
    graph.add_edge(START, "open_application")
    for source, target in [
        ("extract_fields", "plan_answers"),
        ("plan_answers", "policy_check"),
        ("policy_check", "action_boundary"),
        ("repair", "action_boundary"),
    ]:
        graph.add_conditional_edges(
            source,
            lambda state, next_node=target: (
                state.get("route") if state.get("route") == "end" else next_node
            ),
            {target: target, "end": END},
        )
    routes = {name: name for name in names + ["submit"]}
    routes["end"] = END
    for name in [
        "open_application",
        "classify_page",
        "execute_fill",
        "verify_fill",
        "click_next",
        "build_review",
        "review_gate",
        "human_handoff",
        "ask_user",
        "paused",
        "apply_edit",
        "pre_submit_check",
        "action_boundary",
    ]:
        graph.add_conditional_edges(name, lambda state: state["route"], routes)
    graph.add_edge("submit", END)
    return graph.compile()
