"""Read-back verification and bounded repair routing."""

from src.operator.contracts import JobStatus

from ..runtime import GraphState, Services, active_job, read_run, update


def verify_fill(state: GraphState, services: Services) -> dict:
    """Escalations become human gates; missing result keys fail verification."""
    run = read_run(state)
    job = active_job(run)
    if any(action.action == "ask_user" for action in job.actions):
        job.status = JobStatus.NEEDS_ANSWER
        return update(run, route="ask_user")
    expected = [action for action in job.actions if action.action != "skip"]
    job.fill_report = services.call(services.browser.verify(expected))
    if {item.field_key for item in job.fill_report.fields} != {
        item.field_key for item in expected
    }:
        raise ValueError("verification omitted an intended field")
    if not job.fill_report.verified:
        route = "repair" if job.repair_attempts < 2 else "human_handoff"
    else:
        route = "click_next"
    return update(run, route=route)
