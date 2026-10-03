"""Sequential application dispatch: never more than one active browser form."""

from src.operator.contracts import JobStatus

from ..runtime import GraphState, Services, read_run, update


def next_job(state: GraphState, services: Services) -> dict:
    """Pop a stable checkpointed queue."""
    run = read_run(state)
    queue = list(state.get("queue", []))
    if any(job.status == JobStatus.CANCELLED for job in run.jobs.values()):
        for job_id in queue:
            run.jobs[job_id].status = JobStatus.CANCELLED
        queue = []
    run.active_job_id = queue.pop(0) if queue else None
    return update(
        run,
        queue=queue,
        route="application" if run.active_job_id else "aggregate",
        step=0,
        planned_actions={},
        current_keys=[],
    )
