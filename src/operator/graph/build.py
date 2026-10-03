"""Synchronous LangGraph + SqliteSaver wiring with native subgraph interrupts."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.errors import GraphInterrupt
from .nodes.aggregate import aggregate
from .nodes.intake import intake
from .nodes.load_data import load_data
from .nodes.next_job import next_job
from .nodes.select_jobs import select_jobs
from .runtime import GraphState
from .runtime import read_run, update
from src.operator.contracts import Goal, RunStatus
from .subgraph_application import build_application


def build_graph(services, checkpointer):
    """Build fixed top-level edges and a sequential application subgraph."""
    graph = StateGraph(GraphState)
    for name, function in [("intake", intake), ("load_data", load_data), ("select_jobs", select_jobs),
                           ("next_job", next_job), ("aggregate", aggregate)]:
        def guarded(state, fn=function, node=name):
            try:
                return fn(state, services)
            except GraphInterrupt:
                raise
            except Exception as error:
                if node == "aggregate":
                    raise
                run = read_run(state)
                if run.goal_parsed is None:
                    run.goal_parsed = Goal()
                run.goal_parsed.notes.append(f"{node}: {type(error).__name__}; input or adapter invalid")
                run.status = RunStatus.BLOCKED
                return update(run, route="aggregate")
        graph.add_node(name, guarded)
    graph.add_node("application", build_application(services))
    graph.add_edge(START, "intake")
    for source, target in [("intake", "load_data"), ("load_data", "select_jobs"), ("select_jobs", "next_job")]:
        graph.add_conditional_edges(source, lambda state, next_node=target:
                                    "aggregate" if state.get("route") == "aggregate" else next_node,
                                    {target: target, "aggregate": "aggregate"})
    graph.add_conditional_edges("next_job", lambda state: state["route"],
                                {"application": "application", "aggregate": "aggregate"})
    graph.add_edge("application", "next_job")
    graph.add_edge("aggregate", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def sqlite_graph(services, checkpoint_path: str | Path):
    """Keep SQLite open for the compiled graph's lifetime."""
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        yield build_graph(services, SqliteSaver(connection))
    finally:
        connection.close()
