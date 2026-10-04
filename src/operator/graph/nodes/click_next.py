"""Safe multi-step progression; unknown click outcomes cannot be replayed."""

from src.operator.contracts import JobStatus

from ..runtime import GraphState, Services, active_job, read_run, update


def click_next(state: GraphState, services: Services) -> dict:
    """Adapter checks reversible vocabulary; ledger protects step transitions."""
    run = read_run(state)
    job = active_job(run)
    step = state.get("step", 0)
    if step >= 20:
        job.status = JobStatus.NOT_SUPPORTED
        job.blockers.append("Form exceeds 20 steps")
        return update(run, route="end")
    key = str(step)
    moved = services.ledger.action_result(run.run_id, job.job_id, "next", key)
    if moved is None:
        if not services.ledger.claim_action(run.run_id, job.job_id, "next", key):
            job.status = JobStatus.NOT_SUPPORTED
            job.blockers.append(
                "Next-step result was not durably observed; automatic continuation "
                "stopped without re-clicking. Inspect the visible browser manually."
            )
            return update(run, route="end")
        moved = services.call(services.browser.click_next())
        services.ledger.mark_success(run.run_id, job.job_id, "next", key, result=moved)
    if moved:
        job.repair_attempts = 0
    return update(
        run, route="classify_page" if moved else "build_review", step=step + int(moved)
    )
