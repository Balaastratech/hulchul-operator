"""Change exactly one field, invalidate approval, then publish another review."""

from src.operator.contracts import Command, FillAction
from src.operator.policy.authority import CHALLENGE, EEO, LEGAL

from ..runtime import GraphState, Services, active_job, read_run, update


def apply_edit(state: GraphState, services: Services) -> dict:
    """Human-authored edit cannot operate legal, demographic or challenge controls."""
    run = read_run(state)
    job = active_job(run)
    command = Command.model_validate(state["command"])
    field = next((item for item in job.fields if item.key == command.field_key), None)
    if (
        field is None
        or any(
            pattern.search(f"{field.label} {field.group} {field.type}")
            for pattern in (LEGAL, EEO, CHALLENGE)
        )
        or field.type == "file"
    ):
        raise PermissionError("edit requires a known reversible non-sensitive field")
    action = FillAction(
        field_key=field.key,
        action="select" if field.options else "fill",
        value=command.value,
        source="human_command",
    )
    if field.options and command.value not in field.options:
        raise PermissionError("edit option absent")
    # Invalidate old approvals before touching the browser, even if the worker crashes.
    services.ledger.record_review(run.run_id, job.job_id, "0" * 64)
    if services.ledger.claim_action(run.run_id, job.job_id, "edit", command.command_id):
        result = services.call(services.browser.execute(action))
        if not result.success:
            raise RuntimeError("edit failed; inspect browser")
        services.ledger.mark_success(run.run_id, job.job_id, "edit", command.command_id)
    elif not services.ledger.is_done(
        run.run_id, job.job_id, "edit", command.command_id
    ):
        raise RuntimeError("edit outcome uncertain; inspect browser")
    report = services.call(services.browser.verify([action]))
    if not report.fields or not report.verified:
        raise RuntimeError("edited field failed verification")
    job.review_snapshot_hash = None
    job.approval = None
    services.emit("E08", run, "Edited one field; new approval required")
    return update(run, route="build_review")
