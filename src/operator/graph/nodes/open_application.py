"""Allowlisted navigation with resumable action intent."""

from ..runtime import GraphState, Services
from src.operator.contracts import JobStatus
from ..runtime import active_job, read_run, update


def open_application(state: GraphState, services: Services) -> dict:
    """Never navigate a completed open action again on checkpoint re-entry."""
    run = read_run(state)
    job = active_job(run)
    if not services.allowlist.permits(job.url):
        raise PermissionError("off-allowlist application")
    if run.cdp_endpoint:
        services.call(services.browser.attach(run.cdp_endpoint, job.browser_target_id))
    if services.ledger.claim_action(run.run_id, job.job_id, "open", ""):
        services.call(services.browser.navigate(job.url))
        services.ledger.mark_success(run.run_id, job.job_id, "open", "")
    elif not services.ledger.is_done(run.run_id, job.job_id, "open", ""):
        job.status = JobStatus.NEEDS_HUMAN
        job.blockers.append("Navigation outcome uncertain; inspect visible browser")
        return update(run, route="human_handoff")
    job.status = JobStatus.OPENED
    return update(run, route="classify_page")
