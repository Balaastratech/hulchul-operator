"""Explicit queued public-form proof: reversible fields and read-back only (D-014)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.operator.contracts import Event, Goal, JobState, JobStatus, PageState, RunState
from src.operator.graph import Services
from src.operator.graph.nodes.plan_answers import plan_answers
from src.operator.graph.nodes.policy_check import policy_check


def public_proof(
    services: Services, run_id: str, job_id: str, cdp: str, directory: Path
) -> dict:
    """Fill one explicitly selected queue URL without any submit/gate-release code path."""
    data = services.call(services.data.load(run_id))
    matches = [j for j in data.jobs if j.job_id == job_id]
    if len(matches) != 1:
        raise ValueError(
            "public proof must select one existing public job_queue_real ID"
        )
    posting = matches[0]
    if services.allowlist.permits_submission(posting.apply_url or posting.url):
        raise ValueError("public proof requires a non-fixture origin")
    run = RunState(
        run_id=run_id,
        goal="Fill this explicit queued public form for review only; never submit",
        goal_parsed=Goal(mode="dry_run", permits_filling=True),
        active_job_id=job_id,
    )
    job = JobState(job_id=job_id, url=posting.apply_url or posting.url)
    run.jobs[job_id] = job
    services.ledger.register_application(run_id, job_id, posting.company, posting.url)
    services.call(services.browser.attach(cdp))
    services.call(services.browser.navigate(job.url))
    state = services.call(services.browser.classify_page())
    if state != PageState.FORM:
        result = {
            "url": job.url,
            "state": state.value,
            "filled": 0,
            "submitted": False,
            "limitation": "public posting is closed or requires human login/CAPTCHA",
        }
        (directory / "public-proof.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print("PUBLIC PROOF: " + json.dumps(result), flush=True)
        return result
    job.fields = services.call(services.browser.extract_fields())
    state = {
        "run": run.model_dump(mode="json"),
        "data": data.model_dump(mode="json"),
        "current_keys": [f.key for f in job.fields],
    }
    state.update(plan_answers(state, services))
    state.update(policy_check(state, services))
    run = RunState.model_validate(state["run"])
    job = run.jobs[job_id]
    executed = []
    for action in job.actions:
        if action.action in {"skip", "ask_user"}:
            continue
        result = services.call(services.browser.execute(action))
        if result.success:
            executed.append(action)
    report = services.call(services.browser.verify(executed))
    review = services.call(services.browser.read_review())
    review.unanswered = [a.field_key for a in job.actions if a.action == "ask_user"]
    digest = review.content_hash()
    services.ledger.record_review(run_id, job_id, digest)
    job.status = JobStatus.READY_FOR_REVIEW
    job.review_snapshot = review
    job.review_snapshot_hash = digest
    link = services.review_url(run_id, job_id, digest)
    services.call(
        services.channel.emit(
            Event(
                event_id="E07",
                run_id=run_id,
                job_id=job_id,
                message="Public fill-only read-back; unanswered questions remain for human review",
                payload={
                    "review": review.model_dump(mode="json"),
                    "snapshot_hash": digest,
                },
                links={"review": link},
                created_at=datetime.now(UTC),
            )
        )
    )
    # Even a valid capability cannot grant submission authority on the guarded CP.
    tokens = services.channel.tokens
    token = tokens.mint(
        "act", run_id, job=job_id, action="approve", snapshot_hash=digest
    )
    with httpx.Client(base_url=services.channel.local_url) as client:
        page = client.get(
            link.replace(services.channel.public_url, services.channel.local_url, 1)
        )
        assert "submission disabled for this site (D-014)" in page.text
        assert 'action="/api/approve"' not in page.text
        response = client.post(
            "/api/approve", json={"token": token, "run_id": run_id, "job_id": job_id}
        )
        assert (
            response.status_code == 403
            and response.json()["error"] == "submission_disabled"
        )
    result = {
        "url": job.url,
        "state": "FILL_ONLY_REVIEW",
        "filled": len(executed),
        "matched": sum(f.matched for f in report.fields),
        "unanswered": review.unanswered,
        "submitted": False,
        "approve_status": response.status_code,
        "note": "Explicit queue form proof, separate from fit ranking; no live employer submission.",
    }
    (directory / "public-proof.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print("PUBLIC PROOF: " + json.dumps(result), flush=True)
    return result
