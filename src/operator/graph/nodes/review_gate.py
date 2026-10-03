"""Native LangGraph interrupt, resume commands bound to this exact job."""

from ..runtime import GraphState, Services
from langgraph.types import interrupt
from src.operator.contracts import ApprovalState, JobStatus
from ..runtime import active_job, read_run, update, validate_command


def review_gate(state: GraphState, services: Services) -> dict:
    """Do not trust a chat 'yes'; approval consumes a registered capability."""
    run = read_run(state)
    job = active_job(run)
    command = validate_command(interrupt({"kind": "review", "run_id": run.run_id,
                                         "job_id": job.job_id, "snapshot_hash": job.review_snapshot_hash,
                                         "review": job.review_snapshot.model_dump(mode="json")}), run)
    if command.action == "approve":
        if command.snapshot_hash != job.review_snapshot_hash:
            raise PermissionError("stale approval command")
        # Recover only this exact atomic gate release after checkpoint failure.
        consumed = services.ledger.consume_approval(run.run_id, job.job_id, command.token_hash, command.snapshot_hash)
        if not consumed and not services.ledger.approval_released(run.run_id, job.job_id,
                                                                 command.token_hash, command.snapshot_hash):
            raise PermissionError("approval is missing, expired, used or revoked")
        job.approval = ApprovalState(token_hash=command.token_hash)
        job.status = JobStatus.APPROVED
        route = "pre_submit_check"
    elif command.action == "edit":
        route = "apply_edit"
    elif command.action in {"reject", "skip", "cancel"}:
        job.status = JobStatus.CANCELLED if command.action == "cancel" else JobStatus.REJECTED_BY_USER
        route = "end"
    elif command.action == "pause":
        job.paused = True
        route = "paused"
    else:
        raise PermissionError("unsupported review command")
    return update(run, route=route, command=command.model_dump(mode="json"))
