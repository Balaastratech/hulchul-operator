"""Unit tests for adaptive fill, grounding verification, and bounded repair (T-051, D-033)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from src.operator.browser.execute import ActionExecutor
from src.operator.browser.models import FieldSpec as BrowserFieldSpec, FillAction as BrowserFillAction
from src.operator.contracts import (
    Answer,
    AnswerLibrary,
    DataSnapshot,
    FieldSpec,
    FillAction,
    JobPosting,
    JobState,
    Profile,
    Rules,
    RunState,
)
from src.operator.graph.nodes.plan_answers import plan_answers, verify_grounding
from src.operator.graph.runtime import AnswerPlan, Services
from src.operator.graph.tests.fakes import FakeBrowser, FakeChannel, FakeData, FakeLLM
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist


def test_grounding_verification_accepts_valid_sources():
    snapshot = DataSnapshot(
        profile=Profile(name="Aarav Mehta", email="aarav@example.com"),
        rules=Rules(salary_expectation="3000000"),
        answer_library=AnswerLibrary(
            answers=[
                Answer(
                    pattern="earliest start date",
                    answer="Within 30 days of an offer",
                    sensitivity="normal",
                    source="profile",
                )
            ]
        ),
        resume_path="resume.pdf",
        resume_hash="a" * 64,
        snapshot_hash="b" * 64,
        jobs=[JobPosting(job_id="j1", url="http://localhost:8000", company="C", title="T")],
    )

    field = FieldSpec(
        id="date_input",
        key="EARLIEST START DATE|date||0",
        label="EARLIEST START DATE",
        type="date",
        required=True,
    )

    # 1. Valid derived value with source
    action_derived = FillAction(
        field_key=field.key,
        action="fill",
        value="2026-11-03",
        derived=True,
        source="answers.earliest start date",
    )
    res = verify_grounding(action_derived, field, snapshot)
    assert res.action == "fill"
    assert res.value == "2026-11-03"
    assert res.derived is True
    assert res.source == "answers.earliest start date"

    # 2. Direct fact from profile
    name_field = FieldSpec(
        id="name",
        key="Full Name|text||0",
        label="Full Name",
        type="text",
        required=True,
    )
    action_name = FillAction(
        field_key=name_field.key,
        action="fill",
        value="Aarav Mehta",
        source="profile.name",
    )
    res_name = verify_grounding(action_name, name_field, snapshot)
    assert res_name.action == "fill"
    assert res_name.value == "Aarav Mehta"


def test_grounding_verification_rejects_invented_facts():
    snapshot = DataSnapshot(
        profile=Profile(name="Aarav Mehta", email="aarav@example.com"),
        rules=Rules(),
        answer_library=AnswerLibrary(answers=[]),
        resume_path="resume.pdf",
        resume_hash="a" * 64,
        snapshot_hash="b" * 64,
        jobs=[],
    )

    field = FieldSpec(
        id="fav_color",
        key="Favorite Color|text||0",
        label="Favorite Color",
        type="text",
        required=True,
    )

    # Invented value with no source
    action_no_source = FillAction(
        field_key=field.key,
        action="fill",
        value="Blue",
    )
    res = verify_grounding(action_no_source, field, snapshot)
    assert res.action == "ask_user"
    assert "Favorite Color" in res.question

    # Fake source
    action_fake_source = FillAction(
        field_key=field.key,
        action="fill",
        value="Blue",
        source="profile.favorite_color",
    )
    res2 = verify_grounding(action_fake_source, field, snapshot)
    assert res2.action == "ask_user"


def test_error_driven_repair_recovers_malformed_date(tmp_path: Path):
    """Playwright rejects raw date text on <input type=date>; LLM repairs to YYYY-MM-DD."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
      <form id="app-form">
        <label for="start">EARLIEST START DATE</label>
        <input type="date" id="start" name="start_date" data-opid="1">
      </form>
    </body>
    </html>
    """
    html_file = tmp_path / "date_form.html"
    html_file.write_text(html, encoding="utf-8")

    class RepairLLM:
        def __init__(self):
            self.calls = 0

        def structured(self, prompt, response_model):
            self.calls += 1
            return response_model(
                corrected_value="2026-11-03",
                explanation="Formatted 'Within 30 days of an offer' as YYYY-MM-DD",
            )

    repair_llm = RepairLLM()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(f"file:///{html_file.as_posix()}")

        executor = ActionExecutor(page, llm=repair_llm)
        field = BrowserFieldSpec(
            id="1",
            key="EARLIEST START DATE|date||0",
            label="EARLIEST START DATE",
            type="date",
        )
        action = BrowserFillAction(
            field_key=field.key,
            action="fill",
            value="Within 30 days of an offer",
            source="answers.earliest start date",
        )

        result = executor.execute_action(action, field)
        assert result.success is True
        assert result.actual == "2026-11-03"
        assert action.value == "2026-11-03"
        assert action.derived is True
        assert repair_llm.calls == 1

        browser.close()


def test_error_driven_repair_failure_escalates_to_ask_user(tmp_path: Path):
    """If repair fails twice, the action is marked as ask_user with suggested answer."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
      <form id="app-form">
        <label for="start">EARLIEST START DATE</label>
        <input type="date" id="start" name="start_date" data-opid="1">
      </form>
    </body>
    </html>
    """
    html_file = tmp_path / "date_form.html"
    html_file.write_text(html, encoding="utf-8")

    class BrokenRepairLLM:
        def __init__(self):
            self.calls = 0

        def structured(self, prompt, response_model):
            self.calls += 1
            # Returns an invalid date value again
            return response_model(
                corrected_value="Still Not A Date",
                explanation="Failed conversion",
            )

    repair_llm = BrokenRepairLLM()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(f"file:///{html_file.as_posix()}")

        executor = ActionExecutor(page, llm=repair_llm)
        field = BrowserFieldSpec(
            id="1",
            key="EARLIEST START DATE|date||0",
            label="EARLIEST START DATE",
            type="date",
        )
        action = BrowserFillAction(
            field_key=field.key,
            action="fill",
            value="Within 30 days of an offer",
            source="answers.earliest start date",
        )

        result = executor.execute_action(action, field)
        assert result.success is False
        assert action.action == "ask_user"
        assert "Needs your answer: EARLIEST START DATE" in action.question
        assert "suggested: Within 30 days of an offer" in action.question
        assert repair_llm.calls == 1

        browser.close()


def test_real_fixture_field_types_semantic_planning(tmp_path: Path):
    """Test planning across date, number, select, textarea, radio group with real field descriptions."""
    fields = [
        FieldSpec(
            id="start_date",
            key="Earliest start date|date||0",
            label="Earliest start date",
            type="date",
        ),
        FieldSpec(
            id="salary",
            key="Salary expectation (annual, INR)|text||0",
            label="Salary expectation (annual, INR)",
            type="text",
        ),
        FieldSpec(
            id="exp_band",
            key="Experience level|select||0",
            label="Experience level",
            type="select",
            options=["Please choose", "0-2", "3-5", "6-9", "10+"],
        ),
        FieldSpec(
            id="motivation",
            key="Tell us why you want to join|textarea||0",
            label="Tell us why you want to join",
            type="textarea",
        ),
        FieldSpec(
            id="work_eligibility",
            key="Are you eligible to work in the country of this role?|radio||0",
            label="Are you eligible to work in the country of this role?",
            type="radio",
            options=["Yes", "No"],
            required=True,
        ),
    ]

    snapshot = DataSnapshot(
        profile=Profile(name="Aarav Mehta", email="aarav@example.com"),
        rules=Rules(salary_expectation="3000000"),
        answer_library=AnswerLibrary(
            answers=[
                Answer(
                    pattern="earliest start date",
                    answer="Within 30 days of an offer",
                    sensitivity="normal",
                    source="profile",
                ),
                Answer(
                    pattern="total experience",
                    answer="6",
                    sensitivity="normal",
                    source="profile",
                ),
                Answer(
                    pattern="why.*join",
                    answer="Passionate about systems engineering.",
                    sensitivity="normal",
                    source="user",
                ),
                Answer(
                    pattern="work authorization",
                    answer="Yes",
                    sensitivity="normal",
                    source="profile",
                ),
            ]
        ),
        resume_path="resume.pdf",
        resume_hash="a" * 64,
        snapshot_hash="b" * 64,
        jobs=[JobPosting(job_id="j1", url="http://localhost:8000/ats_b/", company="Platform Labs", title="Platform Engineer")],
    )

    class TypesPlanningLLM(FakeLLM):
        async def structured(self, prompt, response_model):
            self.calls += 1
            if response_model is AnswerPlan:
                return AnswerPlan(
                    actions=[
                        FillAction(
                            field_key="Earliest start date|date||0",
                            action="fill",
                            value="2026-11-03",
                            derived=True,
                            source="answers.earliest start date",
                        ),
                        FillAction(
                            field_key="Salary expectation (annual, INR)|text||0",
                            action="fill",
                            value="3000000",
                            derived=True,
                            source="rules.salary_expectation",
                        ),
                        FillAction(
                            field_key="Experience level|select||0",
                            action="select",
                            value="6-9",
                            derived=True,
                            source="answers.total experience",
                        ),
                        FillAction(
                            field_key="Tell us why you want to join|textarea||0",
                            action="fill",
                            value="Passionate about systems engineering.",
                            source="answers.why.*join",
                        ),
                        FillAction(
                            field_key="Are you eligible to work in the country of this role?|radio||0",
                            action="select",
                            value="Yes",
                            source="answers.work authorization",
                        ),
                    ]
                )
            return await super().structured(prompt, response_model)

    llm = TypesPlanningLLM()
    services = Services(
        browser=FakeBrowser(),
        llm=llm,
        data=FakeData(),
        channel=FakeChannel(),
        ledger=SQLiteLedger(tmp_path / "ledger.sqlite"),
        submission_urls=lambda: [],
        allowlist=DomainAllowlist.from_urls(["http://localhost:8000"]),
    )

    state = {
        "run": RunState(
            run_id="r1",
            active_job_id="j1",
            goal="Apply",
            jobs={
                "j1": JobState(
                    job_id="j1",
                    url="http://localhost:8000/ats_b/",
                    fields=fields,
                )
            },
            shortlist=[JobPosting(job_id="j1", url="http://localhost:8000/ats_b/", company="C", title="T")],
        ).model_dump(mode="json"),
        "data": snapshot.model_dump(mode="json"),
        "current_keys": [f.key for f in fields],
    }

    result = plan_answers(state, services)
    planned = result["run"]["jobs"]["j1"]["actions"]
    assert len(planned) == 5

    # Verify date
    assert planned[0]["value"] == "2026-11-03"
    assert planned[0]["derived"] is True
    assert planned[0]["source"] == "answers.earliest start date"

    # Verify number/salary
    assert planned[1]["value"] == "3000000"
    assert planned[1]["derived"] is True

    # Verify select option
    assert planned[2]["value"] == "6-9"
    assert planned[2]["action"] == "select"

    # Verify textarea
    assert planned[3]["value"] == "Passionate about systems engineering."

    # Verify radio
    assert planned[4]["value"] == "Yes"
    assert planned[4]["action"] == "select"

