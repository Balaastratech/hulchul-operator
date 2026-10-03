"""Read the live form and publish a snapshot-bound gate."""

from ..runtime import GraphState, Services
from src.operator.contracts import JobStatus
from src.operator.policy.authority import LEGAL
from ..runtime import active_job, read_run, update


def build_review(state: GraphState, services: Services) -> dict:
    """Missing required values cannot be hidden behind a successful FillReport."""
    run = read_run(state)
    job = active_job(run)
    review = services.call(services.browser.read_review())
    actual = {item.field_key: item.actual for item in review.fields}
    missing = [item.key for item in job.fields if item.required and
               (item.key not in actual or actual[item.key] is None or actual[item.key] == "" or
                (LEGAL.search(item.label) and actual[item.key] is not True))]
    review.unanswered = sorted(set(review.unanswered + missing))
    if review.unanswered:
        job.status = JobStatus.NEEDS_HUMAN
        job.blockers.append("Required controls need human completion")
        return update(run, route="human_handoff")
    digest = review.content_hash()
    if (services.ledger.get_review_hash(run.run_id, job.job_id) != digest or
            services.ledger.get_status(run.run_id, job.job_id) != JobStatus.READY_FOR_REVIEW):
        services.ledger.record_review(run.run_id, job.job_id, digest)
    job.review_snapshot = review
    job.review_snapshot_hash = digest
    job.status = JobStatus.READY_FOR_REVIEW
    services.emit("E07", run, "Ready for human review", payload={"snapshot_hash": digest,
                  "review": review.model_dump(mode="json")})
    return update(run, route="review_gate")
