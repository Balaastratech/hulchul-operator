"""Truthful terminal results, with every incomplete job still visible."""

from src.operator.contracts import JobStatus, RunStatus

from ..runtime import GraphState, Services, read_run, update


def aggregate(state: GraphState, services: Services) -> dict:
    """User rejection counts as a chosen completed outcome, never a submission."""
    run = read_run(state)
    statuses = [job.status for job in run.jobs.values()]
    successful = sum(status == JobStatus.SUBMITTED_VERIFIED for status in statuses)
    if statuses and all(
        status in {JobStatus.SUBMITTED_VERIFIED, JobStatus.REJECTED_BY_USER}
        for status in statuses
    ):
        run.status = RunStatus.COMPLETED
    elif any(status == JobStatus.CANCELLED for status in statuses):
        run.status = RunStatus.CANCELLED
    elif successful:
        run.status = RunStatus.PARTIAL
    else:
        run.status = RunStatus.BLOCKED
    return update(run)
