"""Parse the human goal into a typed proposal."""

from src.operator.contracts import Goal, RunStatus

from ..runtime import GraphState, Services, read_run, update


def intake(state: GraphState, services: Services) -> dict:
    """Human goal is trusted input; model cannot choose graph edges."""
    run = read_run(state)
    if run.goal_parsed is None:
        run.goal_parsed = services.call(services.llm.structured(run.goal, Goal))
        run.usage.llm_calls += 1
    run.mode = run.goal_parsed.mode
    run.status = RunStatus.RUNNING
    return update(run)
