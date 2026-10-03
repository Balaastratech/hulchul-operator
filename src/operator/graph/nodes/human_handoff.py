"""Hands off login/challenges and uncertain browser outcomes without touching them."""

from langgraph.types import interrupt

from src.operator.contracts import JobStatus, PageState

from ..runtime import (
    GraphState,
    Services,
    active_job,
    read_run,
    update,
    validate_command,
)


def human_handoff(state: GraphState, services: Services) -> dict:
    """Resume by classification for login/challenges; no assumed success."""
    run = read_run(state)
    job = active_job(run)
    event = "E05" if job.page_state == PageState.CAPTCHA else "E04"
    services.emit(
        event, run, "Complete the required action in the visible browser; press done"
    )
    command = validate_command(
        interrupt(
            {
                "kind": "handoff",
                "run_id": run.run_id,
                "job_id": job.job_id,
                "page_state": job.page_state.value,
                "blockers": job.blockers,
            }
        ),
        run,
    )
    if command.action == "cancel":
        job.status = JobStatus.CANCELLED
        return update(run, route="end")
    if command.action != "handoff_done":
        raise PermissionError("handoff requires done")
    for action in job.actions:
        if action.action == "ask_user":
            action.action = "skip"
    was_form = job.page_state == PageState.FORM
    job.page_state = services.call(services.browser.classify_page())
    route = (
        "verify_fill"
        if was_form and job.page_state == PageState.FORM
        else "classify_page"
    )
    return update(run, route=route, command=command.model_dump(mode="json"))
