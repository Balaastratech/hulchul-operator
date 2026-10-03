"""Unknown facts require an explicit, scoped human answer."""

from langgraph.types import interrupt

from src.operator.contracts import FillAction, JobStatus
from src.operator.policy.authority import CHALLENGE, EEO, LEGAL

from ..runtime import (
    GraphState,
    Services,
    active_job,
    read_run,
    update,
    validate_command,
)


def ask_user(state: GraphState, services: Services) -> dict:
    """Human answers grant only one reversible field action; legal/EEO stay manual."""
    run = read_run(state)
    job = active_job(run)
    pending = [action for action in job.actions if action.action == "ask_user"]
    action = pending[0]
    field = next(item for item in job.fields if item.key == action.field_key)
    if any(
        pattern.search(f"{field.label} {field.group} {field.type}")
        for pattern in (LEGAL, EEO, CHALLENGE)
    ):
        return update(run, route="human_handoff")
    services.emit(
        "E06",
        run,
        "An explicit field answer is needed",
        payload={"field_key": field.key, "question": action.question},
    )
    command = validate_command(
        interrupt(
            {
                "kind": "answer",
                "run_id": run.run_id,
                "job_id": job.job_id,
                "field_key": field.key,
                "question": action.question,
            }
        ),
        run,
    )
    if command.action in {"cancel", "reject", "skip"}:
        job.status = (
            JobStatus.CANCELLED
            if command.action == "cancel"
            else JobStatus.REJECTED_BY_USER
        )
        return update(run, route="end", command=command.model_dump(mode="json"))
    if command.action == "pause":
        job.paused = True
        return update(run, route="paused", command=command.model_dump(mode="json"))
    if command.action != "answer" or command.field_key != field.key:
        raise PermissionError("answer must target the asked field")
    if field.type in {"checkbox", "radio"}:
        if not isinstance(command.value, bool):
            raise PermissionError("human check answer must be boolean")
        if field.type == "radio" and command.value is not True:
            raise PermissionError("select the desired radio option explicitly")
        kind = "check"
    else:
        kind = "select" if field.options else "fill"
    replacement = FillAction(
        field_key=field.key, action=kind, value=command.value, source="human_command"
    )
    if field.options and command.value not in field.options:
        raise PermissionError("human answer must identify an existing option")
    job.actions = [
        replacement if item.field_key == field.key else item for item in job.actions
    ]
    planned = dict(state.get("planned_actions", {}))
    planned[field.key] = replacement.model_dump(mode="json")
    return update(
        run,
        route="action_boundary",
        action_cursor=0,
        command=command.model_dump(mode="json"),
        planned_actions=planned,
    )
