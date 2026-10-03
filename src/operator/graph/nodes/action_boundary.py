"""Check control commands between atomic actions and checkpoint pause overlays."""

from src.operator.contracts import JobStatus

from ..runtime import (
    GraphState,
    Services,
    active_job,
    read_run,
    update,
    validate_command,
)


def action_boundary(state: GraphState, services: Services) -> dict:
    """Never interrupt between a ledger claim and its browser operation."""
    run = read_run(state)
    job = active_job(run)
    command = services.control_command() if services.control_command else None
    extra = {}
    if command:
        command = validate_command(command.model_dump(mode="json"), run)
        extra["command"] = command.model_dump(mode="json")
        if command.action == "pause":
            run.paused = job.paused = True
        elif command.action == "cancel":
            job.status = JobStatus.CANCELLED
            return update(run, route="end", **extra)
        else:
            raise PermissionError("action-boundary control accepts pause/cancel only")
    route = (
        "execute_fill"
        if run.paused or state.get("action_cursor", 0) < len(job.actions)
        else "verify_fill"
    )
    return update(run, route=route, **extra)
