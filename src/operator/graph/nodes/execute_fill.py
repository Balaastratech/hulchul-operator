"""Ledger-owned reversible actions; uncertain outcomes require observation."""

from ..runtime import GraphState, Services
from langgraph.types import interrupt
from src.operator.contracts import JobStatus
from ..runtime import active_job, read_run, update, validate_command


def execute_fill(state: GraphState, services: Services) -> dict:
    """Pause gates precede actions; successful actions skip across re-entry."""
    run = read_run(state)
    job = active_job(run)
    if run.paused or job.paused:
        command = validate_command(interrupt({"kind": "paused", "run_id": run.run_id, "job_id": job.job_id}), run)
        if command.action != "resume":
            raise PermissionError("paused run requires resume")
        run.paused = job.paused = False
    for action in job.actions:
        if action.action in {"ask_user", "skip"}:
            continue
        key = f"{state.get('step', 0)}:{job.repair_attempts}:{action.field_key}"
        if services.ledger.is_done(run.run_id, job.job_id, action.action, key):
            continue
        if not services.ledger.claim_action(run.run_id, job.job_id, action.action, key):
            report = services.call(services.browser.verify([action]))
            if report.fields and report.verified:
                services.ledger.mark_success(run.run_id, job.job_id, action.action, key)
                continue
            job.status = JobStatus.NEEDS_HUMAN
            job.blockers.append("Uncertain fill action: inspect browser before continuing")
            return update(run, route="human_handoff")
        result = services.call(services.browser.execute(action))
        job.evidence.extend(result.evidence)
        if result.success:
            services.ledger.mark_success(run.run_id, job.job_id, action.action, key)
        else:
            job.notes.append(result.reason or "Field action failed")
    return update(run, route="verify_fill")
