"""Tests for library-first deterministic answer planning and question counting ."""

from __future__ import annotations

import pathlib
import pytest

from src.operator.contracts import (
    Answer,
    AnswerLibrary,
    DataSnapshot,
    FieldSpec,
    FillAction,
    JobPosting,
    JobState,
    PageState,
    Profile,
    Rules,
    RunState,
)
from src.operator.data.local import parse_answers_csv
from src.operator.graph.nodes.answer_library import (
    clean_field_label,
    count_unanswered_questions,
    match_library_answer,
    match_select_option,
)
from src.operator.graph.nodes.plan_answers import plan_answers
from src.operator.graph.runtime import AnswerPlan, Services
from src.operator.graph.tests.fakes import FakeBrowser, FakeChannel, FakeData, FakeLLM
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist
from src.operator.policy.authority import check_fill


@pytest.fixture
def sample_answers() -> AnswerLibrary:
    csv_path = pathlib.Path("sample_data/answers.csv")
    if csv_path.exists():
        return parse_answers_csv(csv_path)
    return AnswerLibrary(
        answers=[
            Answer(
                pattern="how soon can you join|earliest start date",
                answer="Within 30 days of an offer",
                sensitivity="normal",
                source="profile",
            ),
            Answer(
                pattern="require.*sponsorship|visa sponsorship",
                answer="No",
                sensitivity="normal",
                source="profile",
            ),
            Answer(
                pattern="expected salary|salary expectation|expected compensation",
                answer="3000000 INR per year",
                sensitivity="normal",
                source="user",
            ),
            Answer(
                pattern="current salary|current ctc",
                answer="Prefer to discuss after a first conversation",
                sensitivity="normal",
                source="user",
            ),
            Answer(
                pattern="current city|location|city",
                answer="Bengaluru",
                sensitivity="normal",
                source="profile",
            ),
            Answer(
                pattern="gender",
                answer="Decline to self-identify",
                sensitivity="normal",
                source="rules",
            ),
            Answer(
                pattern="veteran",
                answer="Decline to self-identify",
                sensitivity="normal",
                source="rules",
            ),
        ]
    )


def test_clean_field_label():
    assert clean_field_label("CITY AND COUNTRY|text||0") == "CITY AND COUNTRY"
    assert clean_field_label("EARLIEST START DATE *|date||0") == "EARLIEST START DATE"
    assert clean_field_label("SALARY EXPECTATION (ANNUAL, INR)|text||0") == "SALARY EXPECTATION ANNUAL INR"
    assert (
        clean_field_label("DO YOU NEED VISA SPONSORSHIP? Please choose Yes No|select||0")
        == "DO YOU NEED VISA SPONSORSHIP"
    )


def test_match_select_option():
    options = ["Please choose", "Yes", "No"]
    assert match_select_option("No", options) == "No"
    assert match_select_option("no", options) == "No"
    assert match_select_option("false", options) == "No"
    assert match_select_option("Yes", options) == "Yes"

    veteran_options = ["Please choose", "Veteran", "Not a veteran", "Decline to self-identify"]
    assert match_select_option("Decline to self-identify", veteran_options) == "Decline to self-identify"
    assert match_select_option("Unknown", veteran_options) is None


def test_exact_ats_b_fields_resolved_deterministically(sample_answers):
    # 1. City and country
    city_field = FieldSpec(
        id="city",
        key="CITY AND COUNTRY|text||0",
        label="CITY AND COUNTRY",
        type="text",
    )
    res_city = match_library_answer(city_field, sample_answers)
    assert res_city is not None
    ans, act, val = res_city
    assert act == "fill"
    assert val == "Bengaluru"
    assert "city" in ans.pattern

    # 2. Earliest start date
    date_field = FieldSpec(
        id="date",
        key="EARLIEST START DATE|date||0",
        label="EARLIEST START DATE",
        type="date",
    )
    res_date = match_library_answer(date_field, sample_answers)
    assert res_date is not None
    ans, act, val = res_date
    assert act == "fill"
    assert val == "Within 30 days of an offer"

    # 3. Salary expectation
    salary_field = FieldSpec(
        id="salary",
        key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
        label="SALARY EXPECTATION (ANNUAL, INR)",
        type="text",
    )
    res_salary = match_library_answer(salary_field, sample_answers)
    assert res_salary is not None
    ans, act, val = res_salary
    assert act == "fill"
    assert val == "3000000 INR per year"

    # 4. Visa sponsorship select
    visa_field = FieldSpec(
        id="visa",
        key="DO YOU NEED VISA SPONSORSHIP? Please choose Yes No|select||0",
        label="DO YOU NEED VISA SPONSORSHIP? Please choose Yes No",
        type="select",
        options=["Please choose", "Yes", "No"],
    )
    res_visa = match_library_answer(visa_field, sample_answers)
    assert res_visa is not None
    ans, act, val = res_visa
    assert act == "select"
    assert val == "No"


def test_partial_match_disambiguation(sample_answers):
    # 'current salary' vs 'expected salary'
    current_sal_field = FieldSpec(
        id="cs",
        key="CURRENT SALARY|text||0",
        label="Current salary (annual, INR)",
        type="text",
    )
    res_cs = match_library_answer(current_sal_field, sample_answers)
    assert res_cs is not None
    assert res_cs[2] == "Prefer to discuss after a first conversation"

    expected_sal_field = FieldSpec(
        id="es",
        key="EXPECTED SALARY|text||0",
        label="Expected salary (annual, INR)",
        type="text",
    )
    res_es = match_library_answer(expected_sal_field, sample_answers)
    assert res_es is not None
    assert res_es[2] == "3000000 INR per year"


def test_negative_eeo_and_legal_never_autofill(sample_answers):
    # Veteran status is EEO - match_library_answer must return None
    veteran_field = FieldSpec(
        id="vet",
        key="VETERAN STATUS (VOLUNTARY) Please choose Veteran Not a veteran Decline to self-identify|select||0",
        label="VETERAN STATUS (VOLUNTARY) Please choose Veteran Not a veteran Decline to self-identify",
        type="select",
        options=["Please choose", "Veteran", "Not a veteran", "Decline to self-identify"],
    )
    assert match_library_answer(veteran_field, sample_answers) is None

    # Gender is EEO - match_library_answer must return None
    gender_field = FieldSpec(
        id="gen",
        key="GENDER (OPTIONAL)|select||0",
        label="GENDER (OPTIONAL)",
        type="select",
        options=["Female", "Male", "Decline to self-identify"],
    )
    assert match_library_answer(gender_field, sample_answers) is None

    # Terms and conditions consent is LEGAL - match_library_answer must return None
    legal_field = FieldSpec(
        id="terms",
        key="I agree to the terms and the processing of my data. *|checkbox||0",
        label="I agree to the terms and the processing of my data. *",
        type="checkbox",
    )
    assert match_library_answer(legal_field, sample_answers) is None


def test_policy_check_permits_salary_from_answers_library(sample_answers):
    from src.operator.contracts import Goal

    field = FieldSpec(
        id="sal",
        key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
        label="SALARY EXPECTATION (ANNUAL, INR)",
        type="text",
    )
    action = FillAction(
        field_key=field.key,
        action="fill",
        value="3000000 INR per year",
        source="answers.expected salary|salary expectation|expected compensation",
    )
    dec = check_fill(
        action,
        field,
        url="http://127.0.0.1:8780/ats_b",
        allowlist=DomainAllowlist.from_urls(["http://127.0.0.1:8780"]),
        goal=Goal(mode="normal"),
        profile=Profile(name="Aarav", email="aarav@example.com"),
        rules=Rules(salary_expectation=None),  # Not in rules, but in answer library!
        answers=sample_answers,
        resume_path="resume.pdf",
    )
    assert dec.allowed is True
    assert dec.action.action == "fill"
    assert dec.action.value == "3000000 INR per year"


def test_plan_answers_skips_llm_when_all_fields_match_library(tmp_path, sample_answers):
    class PlanningLLM(FakeLLM):
        async def structured(self, prompt, response_model):
            self.calls += 1
            if response_model is AnswerPlan:
                return AnswerPlan(
                    actions=[
                        FillAction(
                            field_key="CITY AND COUNTRY|text||0",
                            action="fill",
                            value="Bengaluru",
                            source="answers.current city|location|city",
                        ),
                        FillAction(
                            field_key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
                            action="fill",
                            value="3000000 INR per year",
                            source="answers.expected salary|salary expectation|expected compensation",
                        ),
                    ]
                )
            return await super().structured(prompt, response_model)

    llm = PlanningLLM()
    services = Services(
        browser=FakeBrowser(),
        llm=llm,
        data=FakeData(),
        channel=FakeChannel(),
        ledger=SQLiteLedger(tmp_path / "ledger.sqlite"),
        submission_urls=lambda: [],
        allowlist=DomainAllowlist.from_urls(["http://localhost:8000"]),
    )

    fields = [
        FieldSpec(
            id="city",
            key="CITY AND COUNTRY|text||0",
            label="CITY AND COUNTRY",
            type="text",
        ),
        FieldSpec(
            id="salary",
            key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
            label="SALARY EXPECTATION (ANNUAL, INR)",
            type="text",
        ),
    ]

    snapshot = DataSnapshot(
        profile=Profile(name="Aarav", email="aarav@example.com"),
        rules=Rules(),
        answer_library=sample_answers,
        resume_path="resume.pdf",
        resume_hash="a" * 64,
        snapshot_hash="b" * 64,
        jobs=[JobPosting(job_id="j1", url="http://localhost:8000", company="C", title="T")],
    )

    state = {
        "run": RunState(
            run_id="r1",
            active_job_id="j1",
            goal="Apply",
            jobs={
                "j1": JobState(
                    job_id="j1",
                    url="http://localhost:8000",
                    fields=fields,
                )
            },
            shortlist=[JobPosting(job_id="j1", url="http://localhost:8000", company="C", title="T")],
        ).model_dump(mode="json"),
        "data": snapshot.model_dump(mode="json"),
        "current_keys": [f.key for f in fields],
    }

    result = plan_answers(state, services)
    # Under D-033, adaptive LLM planning handles semantic matching against facts
    assert llm.calls >= 1
    planned_jobs = result["run"]["jobs"]
    job = planned_jobs["j1"] if isinstance(planned_jobs, dict) else planned_jobs[0]
    assert len(job["actions"]) == 2
    assert job["actions"][0]["value"] == "Bengaluru"
    assert job["actions"][1]["value"] == "3000000 INR per year"


def test_question_counting_on_ats_b_panel_3(sample_answers):
    # Form fields on ats_b panel 3
    fields = [
        # Work eligibility (radio group - 1 question)
        FieldSpec(
            id="we_yes",
            key="Yes|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0",
            label="Yes",
            group="ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *",
            type="radio",
        ),
        FieldSpec(
            id="we_no",
            key="No|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0",
            label="No",
            group="ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *",
            type="radio",
        ),
        # Visa sponsorship (select - 1 question)
        FieldSpec(
            id="visa",
            key="DO YOU NEED VISA SPONSORSHIP? Please choose Yes No|select||0",
            label="DO YOU NEED VISA SPONSORSHIP? Please choose Yes No",
            type="select",
            options=["Please choose", "Yes", "No"],
        ),
        # Earliest start date (date - 1 question)
        FieldSpec(
            id="start",
            key="EARLIEST START DATE|date||0",
            label="EARLIEST START DATE",
            type="date",
        ),
        # Salary expectation (text - 1 question)
        FieldSpec(
            id="salary",
            key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
            label="SALARY EXPECTATION (ANNUAL, INR)",
            type="text",
        ),
        # Tell us why you want to join (textarea - 1 question)
        FieldSpec(
            id="why",
            key="TELL US WHY YOU WANT TO JOIN|textarea||0",
            label="TELL US WHY YOU WANT TO JOIN",
            type="textarea",
        ),
        # Preferred work mode (checkbox group - 1 question)
        FieldSpec(
            id="mode_remote",
            key="Remote|checkbox|PREFERRED WORK MODE|0",
            label="Remote",
            group="PREFERRED WORK MODE",
            type="checkbox",
        ),
        FieldSpec(
            id="mode_hybrid",
            key="Hybrid|checkbox|PREFERRED WORK MODE|0",
            label="Hybrid",
            group="PREFERRED WORK MODE",
            type="checkbox",
        ),
        FieldSpec(
            id="mode_onsite",
            key="On site|checkbox|PREFERRED WORK MODE|0",
            label="On site",
            group="PREFERRED WORK MODE",
            type="checkbox",
        ),
        # Veteran status (select - 1 question)
        FieldSpec(
            id="veteran",
            key="VETERAN STATUS (VOLUNTARY) Please choose Veteran Not a veteran Decline to self-identify|select||0",
            label="VETERAN STATUS (VOLUNTARY) Please choose Veteran Not a veteran Decline to self-identify",
            type="select",
            options=["Please choose", "Veteran", "Not a veteran", "Decline to self-identify"],
        ),
        # Terms consent (checkbox - 1 question)
        FieldSpec(
            id="terms",
            key="I agree to the terms and the processing of my data. *|checkbox||0",
            label="I agree to the terms and the processing of my data. *",
            type="checkbox",
        ),
    ]

    actions = [
        # Work eligibility: checked Yes -> answered
        FillAction(
            field_key="Yes|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0",
            action="check",
            value=True,
        ),
        FillAction(
            field_key="No|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0",
            action="skip",
        ),
        # Visa sponsorship: resolved from library -> answered
        FillAction(
            field_key="DO YOU NEED VISA SPONSORSHIP? Please choose Yes No|select||0",
            action="select",
            value="No",
        ),
        # Start date: resolved from library -> answered
        FillAction(
            field_key="EARLIEST START DATE|date||0",
            action="fill",
            value="Within 30 days of an offer",
        ),
        # Salary expectation: resolved from library -> answered
        FillAction(
            field_key="SALARY EXPECTATION (ANNUAL, INR)|text||0",
            action="fill",
            value="3000000 INR per year",
        ),
        # Why join: essay filled -> answered
        FillAction(
            field_key="TELL US WHY YOU WANT TO JOIN|textarea||0",
            action="fill",
            value="Passionate about systems.",
            generated=True,
        ),
        # Work mode checkboxes: not answered (user preference unknown) -> UNANSWERED (1 question)
        FillAction(field_key="Remote|checkbox|PREFERRED WORK MODE|0", action="skip"),
        FillAction(field_key="Hybrid|checkbox|PREFERRED WORK MODE|0", action="skip"),
        FillAction(field_key="On site|checkbox|PREFERRED WORK MODE|0", action="skip"),
        # Veteran status: EEO decline -> answered
        FillAction(
            field_key="VETERAN STATUS (VOLUNTARY) Please choose Veteran Not a veteran Decline to self-identify|select||0",
            action="select",
            value="Decline to self-identify",
        ),
        # Terms consent: legal gate -> UNANSWERED (1 question)
        FillAction(
            field_key="I agree to the terms and the processing of my data. *|checkbox||0",
            action="ask_user",
            question="Please confirm consent",
        ),
    ]

    # Exactly 2 questions left unanswered on Panel 3:
    # 1. Preferred work mode (group of 3 checkboxes)
    # 2. Terms consent
    unanswered_count = count_unanswered_questions(fields, actions)
    assert unanswered_count == 2
