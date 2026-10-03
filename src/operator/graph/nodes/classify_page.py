"""Page state controls code-defined routes, never model-selected edges."""

from src.operator.contracts import JobStatus, PageState

from ..runtime import GraphState, Services, active_job, read_run, update


def classify_page(state: GraphState, services: Services) -> dict:
    """Unknown/challenge pages hand off without any interaction."""
    run = read_run(state)
    job = active_job(run)
    job.page_state = services.call(services.browser.classify_page())
    if job.page_state == PageState.FORM:
        job.status = JobStatus.FILLING
        route = "extract_fields"
    elif job.page_state in {PageState.LOGIN, PageState.CAPTCHA, PageState.UNKNOWN}:
        job.status = JobStatus.NEEDS_HUMAN
        route = "human_handoff"
    else:
        job.status = JobStatus.NOT_SUPPORTED
        job.blockers.append(f"Unexpected page state: {job.page_state.value}")
        route = "end"
    return update(run, route=route)
