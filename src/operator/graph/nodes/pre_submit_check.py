"""Live read-back is compared immediately before durable submit intent."""

from src.operator.contracts import JobStatus
from src.operator.policy.authority import authorize_submit

from ..runtime import GraphState, Services, active_job, read_run, update


def pre_submit_check(state: GraphState, services: Services) -> dict:
    """A stale form gets another review; a recorded submit can only be verified."""
    run = read_run(state)
    job = active_job(run)
    durable = services.ledger.get_status(run.run_id, job.job_id)
    if durable in {
        JobStatus.SUBMITTING,
        JobStatus.SUBMITTED_VERIFIED,
        JobStatus.SUBMITTED_UNVERIFIED,
    }:
        return update(run, route="submit")
    live = services.call(services.browser.read_review())
    if live.content_hash() != job.review_snapshot_hash:
        return update(run, route="build_review")
    authorize_submit(
        url=job.url,
        allowlist=services.allowlist,
        goal=run.goal_parsed,
        approved_hash=job.review_snapshot_hash,
        live_hash=live.content_hash(),
    )
    if durable != JobStatus.APPROVED:
        raise PermissionError("gate was not atomically approved")
    return update(run, route="submit")
