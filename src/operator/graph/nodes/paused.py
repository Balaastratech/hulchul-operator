"""Pause overlay with no browser side effects."""

from langgraph.types import interrupt

from src.operator.contracts import JobStatus

from ..runtime import (
    GraphState,
    Services,
    active_job,
    read_run,
    update,
    validate_command,
)


def paused(state: GraphState, services: Services) -> dict:
    """Resume returns to the pending gate; cancel terminates this job."""
    run = read_run(state)
    job = active_job(run)
    command = validate_command(
        interrupt({"kind": "paused", "run_id": run.run_id, "job_id": job.job_id}), run
    )
    if command.action == "cancel":
        job.status = JobStatus.CANCELLED
        route = "end"
    elif command.action == "resume":
        run.paused = job.paused = False
        route = (
            "ask_user"
            if job.status == JobStatus.NEEDS_ANSWER
            else "human_handoff"
            if job.status == JobStatus.NEEDS_HUMAN
            else "build_review"
        )
    else:
        raise PermissionError("pause requires resume or cancel")
    return update(run, route=route, command=command.model_dump(mode="json"))
