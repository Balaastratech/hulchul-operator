"""Recover observed transitions; explicitly terminate unobservable ones."""

import pytest

from src.operator.contracts import JobState, JobStatus, RunState
from src.operator.graph.nodes.click_next import click_next
from src.operator.graph.tests.fakes import services_at


@pytest.mark.parametrize("result", [False, True, "claimed", "legacy_success"])
def test_completed_or_uncertain_next_is_never_clicked_again(tmp_path, result):
    services = services_at(tmp_path)
    services.ledger.claim_action("r", "fixture", "next", "0")
    if isinstance(result, bool):
        services.ledger.mark_success("r", "fixture", "next", "0", result=result)
    elif result == "legacy_success":
        services.ledger.mark_success("r", "fixture", "next", "0")

    async def forbidden_click():
        raise AssertionError("recovery must not repeat Next")

    services.browser.click_next = forbidden_click
    run = RunState(
        run_id="r",
        goal="fixture",
        active_job_id="fixture",
        jobs={"fixture": JobState(job_id="fixture", url="http://localhost:8000/apply")},
    )
    try:
        output = click_next({"run": run.model_dump(mode="json"), "step": 0}, services)
        if isinstance(result, bool):
            assert output["route"] == ("classify_page" if result else "build_review")
            assert output["step"] == int(result)
        else:
            assert output["route"] == "end"
            job = output["run"]["jobs"]["fixture"]
            assert job["status"] == JobStatus.NOT_SUPPORTED
            assert "not durably observed" in job["blockers"][0]
        assert services.browser.submissions == 0
    finally:
        services.close()
