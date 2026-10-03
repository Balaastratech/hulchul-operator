"""Fixture-only single attempt; SUBMITTING always precedes the browser click."""

from src.operator.contracts import JobStatus, PageState
from src.operator.policy.authority import authorize_submit

from ..runtime import GraphState, Services, active_job, read_run, update


def submit(state: GraphState, services: Services) -> dict:
    """Re-entry observes outcomes only, including a crash before the first click."""
    run = read_run(state)
    job = active_job(run)
    durable = services.ledger.get_status(run.run_id, job.job_id)
    if durable == JobStatus.SUBMITTED_VERIFIED:
        job.status = durable
        return update(run)
    if durable not in {JobStatus.SUBMITTING, JobStatus.SUBMITTED_UNVERIFIED}:
        if services.submission_urls is None:
            raise PermissionError("live page and submission target guard is required")
        urls = services.call(services.submission_urls())
        if not urls or any(
            not services.allowlist.permits_submission(url) for url in urls
        ):
            raise PermissionError(
                "live submission targets must all be configured fixtures"
            )
        if services.call(services.browser.classify_page()) != PageState.FORM:
            raise PermissionError("submit requires a form without human challenge")
        live = services.call(services.browser.read_review())
        authorize_submit(
            url=urls[0],
            allowlist=services.allowlist,
            goal=run.goal_parsed,
            approved_hash=job.review_snapshot_hash,
            live_hash=live.content_hash(),
        )
        if not services.ledger.begin_submission(
            run.run_id, job.job_id, job.review_snapshot_hash
        ):
            raise PermissionError("approval missing, expired or already submitting")
        # Nothing between durable intent and click can authorize a second attempt.
        job.status = JobStatus.SUBMITTING
        try:
            services.emit("E09", run, "Approved fixture submission is starting")
            services.call(services.browser.submit())
        except Exception:  # noqa: BLE001 - every Port failure leaves irreversible intent consumed
            job.notes.append("Submit click outcome uncertain; verification only")
    try:
        result = services.call(services.browser.verify_submission())
        verified = result.verified and bool(
            result.confirmation or result.application_id or result.evidence
        )
        job.evidence.extend(result.evidence)
    except Exception:  # noqa: BLE001 - failed observation must be truthfully unverified
        verified = False
    services.ledger.finish_submission(run.run_id, job.job_id, verified=verified)
    job.status = services.ledger.get_status(run.run_id, job.job_id)
    if not verified:
        job.blockers.append(
            "Submission outcome is unverified; check the fixture manually"
        )
    services.emit(
        "E10" if verified else "E11",
        run,
        "Submitted and verified"
        if verified
        else "Submit attempt recorded; outcome unverified",
    )
    return update(run)
