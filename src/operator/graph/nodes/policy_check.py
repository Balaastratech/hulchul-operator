"""Every model action passes deterministic field and source policy."""

from ..runtime import GraphState, Services
from src.operator.contracts import DataSnapshot, FillAction
from src.operator.policy.authority import check_fill
from ..runtime import active_job, read_run, update


def policy_check(state: GraphState, services: Services) -> dict:
    """Reject duplicate/unknown keys and escalate omitted required controls."""
    run = read_run(state)
    job = active_job(run)
    data = DataSnapshot.model_validate(state["data"])
    fields = {item.key: item for item in job.fields}
    keys = [action.field_key for action in job.actions]
    if len(keys) != len(set(keys)) or any(key not in fields for key in keys):
        raise PermissionError("planner returned unknown or duplicate field keys")
    checked = []
    for action in job.actions:
        checked.append(check_fill(action, fields[action.field_key], url=job.url, allowlist=services.allowlist,
                                  goal=run.goal_parsed, profile=data.profile, rules=data.rules,
                                  answers=data.answer_library, resume_path=data.resume_path).action)
    for field in job.fields:
        if field.required and field.key not in keys:
            checked.append(FillAction(field_key=field.key, action="ask_user", question="Required field has no answer"))
    job.actions = checked
    return update(run)
