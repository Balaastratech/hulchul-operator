"""Small fixture flow with independent safety and liveness assertions."""

import json
import sqlite3
from collections import Counter

from scripts.demo_g3 import Forms, Scenario


class RecoveryFailure(AssertionError):
    """A known failure to progress; safety assertions remain ordinary hard failures."""


def safety(scenario: Scenario, *, edited: bool = False) -> dict[str, object]:
    """Assert safety even for a failed recovery; never hide it under a liveness xfail."""
    total = scenario.counter()["total"]
    assert total <= 1, "duplicate fixture submission"
    with sqlite3.connect(scenario.config.db_path) as db:
        approvals = db.execute(
            "SELECT COUNT(*) FROM commands WHERE action='approve'"
        ).fetchone()[0]
        verified_events = db.execute(
            "SELECT COUNT(*) FROM events WHERE event_id='E10'"
        ).fetchone()[0]
        assert not verified_events or total == 1, "control plane reports false VERIFIED"
    assert approvals <= 1, "approval reused to queue a second command"
    path = scenario.directory / "state.json"
    status = None
    if path.exists():
        status = scenario.state.get("jobs", {}).get("fixture", {}).get("status")
        assert status != "SUBMITTED_VERIFIED" or total == 1, "false VERIFIED"
    path = scenario.directory / "inputs.jsonl"
    counts = (
        Counter(json.loads(line) for line in path.read_text().splitlines())
        if path.exists()
        else Counter()
    )
    assert all(
        count <= (2 if edited and key == "Email|email||0" else 1)
        for key, count in counts.items()
    ), "recovery refilled a successful field"
    return {"status": status, "submissions": total, "inputs": dict(counts)}


def reach_review(scenario: Scenario) -> None:
    """Answer fixture-only gates through the actual signed CP forms."""
    scenario.worker("start")
    for _ in range(10):
        status = scenario.state.get("jobs", {}).get("fixture", {}).get("status")
        if status == "READY_FOR_REVIEW":
            assert scenario.counter()["total"] == 0
            return
        if status not in {"NEEDS_ANSWER", "NEEDS_HUMAN"}:
            raise RecoveryFailure("recovery stopped before review: " + str(status))
        forms = Forms(scenario.page_for_gate()).forms
        answers = [form["fields"] for form in forms if form["action"] == "/api/answer"]
        if answers:
            scenario.post("answer", dict(answers[0], value="+1 202 555 0199"))
            scenario.worker()
        else:
            done = next(
                (
                    form["fields"]
                    for form in forms
                    if form["action"] == "/api/handoff_done"
                ),
                None,
            )
            assert done is not None, "handoff form missing"
            scenario.post("handoff_done", done)
            scenario.worker("human")
    raise RecoveryFailure("fixture did not reach review within ten human gates")


def finish(
    scenario: Scenario, *, pause: bool = False, edit: bool = False
) -> dict[str, object]:
    """Resume, explicitly approve once, restart and reject the old approval again."""
    if pause:
        scenario.post("pause", scenario.form("pause", run=True))
        scenario.worker()
        scenario.worker()
        if scenario.job["status"] == "FAILED":
            raise RecoveryFailure("pause recovery reached FAILED")
        assert scenario.job["paused"] and scenario.counter()["total"] == 0
        scenario.post("resume", scenario.form("resume", run=True))
        scenario.worker()
        assert scenario.job["status"] == "READY_FOR_REVIEW"
    if edit:
        email = next(
            field["key"] for field in scenario.job["fields"] if field["type"] == "email"
        )
        scenario.post(
            "edit",
            dict(
                scenario.form("edit", field=email),
                value="edited-synthetic@example.test",
            ),
        )
        scenario.worker()
    if scenario.job["status"] != "READY_FOR_REVIEW":
        raise RecoveryFailure(
            "recovery stopped before approval: " + scenario.job["status"]
        )
    approval = scenario.form("approve")
    planning = (scenario.directory / "planning.txt").read_text()
    scenario.post("approve", approval)
    scenario.post("approve", approval, 409)
    scenario.worker()
    scenario.worker()
    scenario.post("approve", approval, 409)
    outcome = safety(scenario, edited=edit)
    assert (scenario.directory / "planning.txt").read_text() == planning, (
        "resume replanned fields"
    )
    if outcome["status"] not in {"SUBMITTED_VERIFIED", "SUBMITTED_UNVERIFIED"}:
        raise RecoveryFailure(
            "recovery did not reach truthful terminal submission status: "
            + str(outcome["status"])
        )
    return outcome
