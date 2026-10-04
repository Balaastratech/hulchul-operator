"""Read the live form and publish a snapshot-bound gate."""

from src.operator.contracts import FillAction, JobStatus

from ..runtime import GraphState, Services, active_job, read_run, update


def build_review(state: GraphState, services: Services) -> dict:
    """Missing required values cannot be hidden behind a successful FillReport."""
    run = read_run(state)
    job = active_job(run)
    job.actions = [
        FillAction.model_validate(item)
        for item in state.get("planned_actions", {}).values()
    ]
    if services.restore_browser:
        # A completed edit may outlive its adapter cache but not its checkpointed
        # intent. Align intent before read-back without replaying any input.
        services.call(services.restore_browser(job))
    review = services.call(services.browser.read_review())
    actual = {item.field_key: item.actual for item in review.fields}
    missing = [
        item.key
        for item in job.fields
        if item.required
        and (
            item.key not in actual
            or actual[item.key] is None
            or actual[item.key] == ""
            or (item.type == "checkbox" and actual[item.key] is not True)
        )
    ]
    review.unanswered = sorted(set(review.unanswered + missing))
    if review.unanswered:
        job.status = JobStatus.NEEDS_HUMAN
        job.blockers.append("Required controls need human completion")
        return update(run, route="human_handoff")
    digest = review.content_hash()
    if (
        services.ledger.get_review_hash(run.run_id, job.job_id) != digest
        or services.ledger.get_status(run.run_id, job.job_id)
        != JobStatus.READY_FOR_REVIEW
    ):
        services.ledger.record_review(run.run_id, job.job_id, digest)
    job.review_snapshot = review
    job.review_snapshot_hash = digest
    job.status = JobStatus.READY_FOR_REVIEW
    links = {}
    if services.review_url:
        link = services.review_url(run.run_id, job.job_id, digest)
        if not services.allowlist.permits(link):
            raise PermissionError("review link is outside the control-plane allowlist")
        links["review"] = link
    edited = state.get("command", {}).get("action") == "edit"
    services.emit(
        "E08" if edited else "E07",
        run,
        "Edited one field; new approval required"
        if edited
        else "Ready for human review",
        payload={"snapshot_hash": digest, "review": review.model_dump(mode="json")},
        links=links,
    )
    return update(run, route="review_gate")
