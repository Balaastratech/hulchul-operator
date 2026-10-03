"""Bounded retries restricted to mismatched controls."""

from ..runtime import GraphState, Services
from ..runtime import active_job, read_run, update


def repair(state: GraphState, services: Services) -> dict:
    """Fresh ledger revision ensures original successful actions stay untouched."""
    run = read_run(state)
    job = active_job(run)
    failed = set(job.fill_report.unresolved)
    job.actions = [action for action in job.actions if action.field_key in failed]
    job.repair_attempts += 1
    return update(run)
