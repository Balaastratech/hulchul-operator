"""Sequential application dispatch: never more than one active browser form."""

from ..runtime import GraphState, Services
from ..runtime import read_run, update


def next_job(state: GraphState, services: Services) -> dict:
    """Pop a stable checkpointed queue."""
    run = read_run(state)
    queue = list(state.get("queue", []))
    run.active_job_id = queue.pop(0) if queue else None
    return update(run, queue=queue, route="application" if run.active_job_id else "aggregate", step=0)
