"""Deliver the computed truthful result without changing application outcomes."""

from ..runtime import GraphState, Services, read_run, update


def final_report(state: GraphState, services: Services) -> dict:
    """Every incomplete job retains its reason and evidence in the report."""
    run = read_run(state)
    services.emit(
        "E14",
        run,
        f"Run result: {run.status.value}",
        payload={
            "jobs": {key: job.model_dump(mode="json") for key, job in run.jobs.items()},
            "notes": run.goal_parsed.notes if run.goal_parsed else [],
        },
    )
    return update(run)
