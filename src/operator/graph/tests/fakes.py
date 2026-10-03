"""Synthetic Ports: no network, credentials, real persona or employer form."""

from src.operator.contracts import (
    ActionResult, AnswerLibrary, DataSnapshot, FieldResult, FieldSpec, FillAction,
    FillReport, Goal, JobPosting, PageState, Profile, ReviewSnapshot, Rules, SubmissionResult,
)
from src.operator.graph.runtime import AnswerPlan, Services, ShortlistPlan
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist


class FakeBrowser:
    def __init__(self):
        self.values = {"name": ""}
        self.executions = []
        self.navigations = 0
        self.submissions = 0
        self.page_state = PageState.FORM

    async def attach(self, endpoint, target_id=None):
        pass

    async def navigate(self, url):
        self.navigations += 1

    async def classify_page(self):
        return self.page_state

    async def extract_fields(self):
        return [FieldSpec(id="name", key="name", label="Full name", type="text", required=True,
                          current_value=self.values["name"])]

    async def execute(self, action):
        self.executions.append(action.field_key)
        self.values[action.field_key] = action.value
        return ActionResult(field_key=action.field_key, success=True, actual=action.value)

    async def verify(self, actions):
        return FillReport(fields=[FieldResult(field_key=action.field_key, intended=action.value,
                                              actual=self.values.get(action.field_key),
                                              matched=action.value == self.values.get(action.field_key))
                                  for action in actions])

    async def click_next(self):
        return False

    async def capture_evidence(self):
        return []

    async def read_review(self):
        return ReviewSnapshot(fields=[FieldResult(field_key=key, actual=value, matched=True)
                                      for key, value in self.values.items()])

    async def submit(self):
        self.submissions += 1
        self.page_state = PageState.CONFIRMATION

    async def verify_submission(self):
        return SubmissionResult(verified=self.page_state == PageState.CONFIRMATION,
                                confirmation="Synthetic fixture confirmation" if self.submissions else None)


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def structured(self, prompt, response_model):
        self.calls += 1
        if response_model is Goal:
            return Goal(mode="normal")
        if response_model is ShortlistPlan:
            return ShortlistPlan(jobs=[dict(job_id="fixture", score=1, reason="Synthetic fixture fit")])
        return AnswerPlan(actions=[FillAction(field_key="name", action="fill", value="Synthetic",
                                              source="profile.name")])


class FakeData:
    async def load(self, run_id):
        return DataSnapshot(profile=Profile(name="Synthetic", email="synthetic@example.test"),
                            rules=Rules(), answer_library=AnswerLibrary(), resume_path="synthetic.pdf",
                            resume_hash="c" * 64, snapshot_hash="d" * 64,
                            jobs=[JobPosting(job_id="fixture", company="Synthetic Company", title="Engineer",
                                             url="http://localhost:8000/apply")])


class FakeChannel:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def services_at(path, browser=None):
    return Services(browser=browser or FakeBrowser(), llm=FakeLLM(), data=FakeData(), channel=FakeChannel(),
                    ledger=SQLiteLedger(path / "ledger.sqlite"),
                    allowlist=DomainAllowlist.from_urls(["http://localhost:8000"],
                                                       fixture_urls=["http://localhost:8000"]))
