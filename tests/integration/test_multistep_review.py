"""Real Chrome ATS B regression: fill, signed human hand-off, live review."""

import json
import os
import traceback
from pathlib import Path

import pytest

from scripts.demo_g3 import (
    FixtureData,
    FixturePlanner,
    Forms,
    ObservedBrowser,
    RecordingChannel,
    Scenario,
)
from src.operator.contracts import FillAction, Goal
from src.operator.graph import Services, sqlite_graph
from src.operator.graph.runtime import AnswerPlan, ShortlistPlan
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist
from worker.main import Worker, _current_run
from worker.transport import HttpTransport


class MultistepData(FixtureData):
    """Use only generated candidate data and the local multi-step fixture."""

    async def load(self, run_id):
        data = await super().load(run_id)
        data.jobs[0].url = "http://127.0.0.1:8780/ats_b/"
        return data


class MultistepPlanner(FixturePlanner):
    """Replay deterministic proposals without requiring credentials or Gemini."""

    async def structured(self, prompt, response_model):
        if response_model in {Goal, ShortlistPlan}:
            return await super().structured(prompt, response_model)
        payload = json.loads(
            prompt.split("<untrusted_data>")[1].split("</untrusted_data>")[0]
        )
        for field in payload["fields"]:
            field["label"] = (
                field["label"]
                .casefold()
                .replace("given name", "first name")
                .replace("family name", "last name")
            )
        plan = await super().structured(
            "<untrusted_data>" + json.dumps(payload) + "</untrusted_data>", AnswerPlan
        )
        # The synthetic human owns consent and eligibility. The operator never
        # guesses legal answers; consent's ask_user produces the E04 hand-off.
        fields = {field["key"]: field for field in payload["fields"]}
        plan.actions = [
            FillAction(field_key=action.field_key, action="skip")
            if fields[action.field_key]["type"] == "radio"
            else action
            for action in plan.actions
        ]
        return plan


class DiagnosticBrowser(ObservedBrowser):
    """Retain only synthetic ATS DOM, screenshot and the precise failing call."""

    async def read_review(self):
        try:
            return await super().read_review()
        except Exception:
            failure = traceback.format_exc()

            def capture():
                self.page.bring_to_front()
                evidence = Path(
                    os.getenv("T050_EVIDENCE_DIR", str(self.directory / "diagnostics"))
                )
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / "failure.txt").write_text(failure, encoding="utf-8")
                (evidence / "failure.html").write_text(
                    self.page.content(), encoding="utf-8"
                )
                self.page.screenshot(path=str(evidence / "failure.png"), full_page=True)

            await self._call(capture)
            raise


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_G3") != "1", reason="set RUN_G3=1; owns fixture port 8780"
)
@pytest.mark.parametrize("minimized", [False, True])
def test_ats_b_fill_handoff_review(tmp_path, minimized):
    """Native worker gates reach review without pre-approval fixture submission."""
    with Scenario(tmp_path, headed=minimized) as scenario:
        allowed = DomainAllowlist.from_urls(
            ["http://127.0.0.1:8780/ats_b/"],
            fixture_urls=["http://127.0.0.1:8780"],
            control_plane_url=scenario.public,
        )
        browser = DiagnosticBrowser(allowed, tmp_path)
        services = Services(
            browser=browser,
            llm=MultistepPlanner(tmp_path),
            data=MultistepData(tmp_path),
            channel=RecordingChannel(
                tmp_path, scenario.base, "Bearer " + scenario.config.worker_token
            ),
            ledger=SQLiteLedger(tmp_path / "ledger.sqlite"),
            allowlist=allowed,
            restore_browser=browser.restore,
            target_id=browser.target_id,
            submission_urls=browser.submission_urls,
        )
        prefix = scenario.base + "/api/worker/runs/g3"
        transport = HttpTransport(
            prefix + "/commands",
            prefix + "/ack",
            prefix + "/heartbeat",
            authorization="Bearer " + scenario.config.worker_token,
        )
        try:
            with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
                worker = Worker(
                    graph, services, transport, "g3", tmp_path / "commands.sqlite"
                )
                worker.start_or_resume(
                    "Fill the synthetic multi-step fixture", scenario.cdp
                )
                run = _current_run(graph.get_state(worker.config, subgraphs=True))
                assert run.jobs["fixture"].status == "NEEDS_ANSWER"
                assert any(event["event_id"] == "E04" for event in scenario.events)

                def human():
                    # Explicit fixture-human actor completes only the visible
                    # legal controls; no CAPTCHA/login control is operated.
                    browser.page.locator("[name=work_eligibility][value=yes]").check()
                    browser.page.locator("[name=consent_terms]").check()

                services.call(browser._call(human))
                form = next(
                    form["fields"]
                    for form in Forms(scenario.page_for_gate()).forms
                    if form["action"] == "/api/handoff_done"
                )
                scenario.post("handoff_done", form)
                if minimized:

                    def minimize():
                        session = browser.page.context.new_cdp_session(browser.page)
                        try:
                            window = session.send("Browser.getWindowForTarget")[
                                "windowId"
                            ]
                            session.send(
                                "Browser.setWindowBounds",
                                {
                                    "windowId": window,
                                    "bounds": {"windowState": "minimized"},
                                },
                            )
                            browser.page.set_default_timeout(5000)
                        finally:
                            session.detach()

                    services.call(browser._call(minimize))
                worker.tick()
                run = _current_run(graph.get_state(worker.config, subgraphs=True))
                assert run.jobs["fixture"].status == "READY_FOR_REVIEW", run.jobs[
                    "fixture"
                ].blockers
                review = run.jobs["fixture"].review_snapshot
                assert review and not review.unanswered
                assert review.uploads[0].name == "synthetic.pdf"
                assert any(
                    field.actual == "synthetic@example.test" for field in review.fields
                )
                assert services.call(
                    browser._call(browser.page.locator("#panel-4").is_visible)
                )
                assert scenario.counter()["total"] == 0
                assert "submit" not in (tmp_path / "operations.txt").read_text()
        finally:
            services.call(browser.disconnect())
            services.close()
