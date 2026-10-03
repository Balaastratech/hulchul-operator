"""Opt-in shared ATS A/B integration; synthetic sources and human fixture actor."""

import hashlib
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

import pytest
from langgraph.types import Command as Resume

from src.operator.contracts import Answer, AnswerLibrary, FillAction, Goal
from src.operator.graph import Services, sqlite_graph
from src.operator.graph.adapters import BrowserBridge
from src.operator.graph.runtime import AnswerPlan, ShortlistPlan
from src.operator.graph.tests.fakes import FakeChannel, FakeData
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist
from worker.main import _current_run


@pytest.mark.skipif(
    not os.getenv("HULCHUL_FIXTURE_SOURCE"),
    reason="requires explicit shared fixture/browser sources",
)
@pytest.mark.parametrize("layout", ["ats_a", "ats_b"])
def test_shared_layout_reaches_review_restores_and_submits_once(tmp_path, layout):
    browser_source = Path(os.environ["HULCHUL_BROWSER_SOURCE"]).resolve()
    fixture_source = Path(os.environ["HULCHUL_FIXTURE_SOURCE"]).resolve()
    importlib.import_module("src.operator").__path__.append(
        str(browser_source / "src/operator")
    )
    from src.operator.browser.cdp import CDPBrowserManager

    spec = importlib.util.spec_from_file_location(
        "shared_fixture_server", fixture_source / "fixtures/server.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    server = fixture.make_server(0, tmp_path / "server")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/{layout}/"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    chrome = subprocess.Popen(
        [
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    allowed = DomainAllowlist.from_urls([url], fixture_urls=[url])
    browser = BrowserBridge(CDPBrowserManager(), allowed, tmp_path / "evidence")
    from src.operator.browser.models import FieldSpec as PeerField

    browser.fields["prior-job"] = PeerField(
        id="9999", key="prior-job", label="Prior synthetic job field", type="text"
    )
    started = time.monotonic()
    original_navigate = browser.navigate

    async def track_navigation(target):
        await original_navigate(target)
        await browser._call(
            browser.page.evaluate,
            """() => {
            sessionStorage.setItem('operator_input_events', '0');
            document.addEventListener('input', () => sessionStorage.setItem('operator_input_events',
                Number(sessionStorage.getItem('operator_input_events') || 0) + 1));
        }""",
        )

    browser.navigate = track_navigation

    def input_count():
        return services.call(
            browser._call(
                browser.page.evaluate,
                "Number(sessionStorage.getItem('operator_input_events') || 0)",
            )
        )

    resume = tmp_path / "synthetic.pdf"
    resume.write_bytes(b"%PDF-1.4\nSynthetic fixture resume\n%%EOF")

    class Data(FakeData):
        async def load(self, run_id):
            data = await super().load(run_id)
            data.profile.phone = "+919000000000"
            data.jobs[0].url = url
            data.resume_path = str(resume)
            data.resume_hash = hashlib.sha256(resume.read_bytes()).hexdigest()
            data.answer_library = AnswerLibrary(
                answers=[
                    Answer(
                        pattern="first",
                        answer="Synthetic",
                        source="synthetic fixture profile",
                    ),
                    Answer(
                        pattern="last",
                        answer="Example",
                        source="synthetic fixture profile",
                    ),
                    Answer(
                        pattern="country",
                        answer="India",
                        source="synthetic fixture profile",
                    ),
                ]
            )
            return data

    class Planner:
        def __init__(self):
            self.calls = 0

        async def structured(self, prompt, response_model):
            self.calls += 1
            if response_model is Goal:
                return Goal(mode="normal")
            if response_model is ShortlistPlan:
                return ShortlistPlan(
                    jobs=[
                        {
                            "job_id": "fixture",
                            "score": 1,
                            "reason": "Synthetic integration",
                        }
                    ]
                )
            payload = json.loads(
                prompt.split("<untrusted_data>")[1].split("</untrusted_data>")[0]
            )
            actions = []
            for field in payload["fields"]:
                label = field["label"].casefold()
                kind, value, source = "skip", None, None
                if "first name" in label or "given name" in label:
                    kind, value, source = "fill", "Synthetic", "answers.first"
                elif "last name" in label or "family name" in label:
                    kind, value, source = "fill", "Example", "answers.last"
                elif field["type"] == "email":
                    kind, value, source = (
                        "fill",
                        payload["profile"]["email"],
                        "profile.email",
                    )
                elif field["type"] == "tel":
                    kind, value, source = (
                        "fill",
                        payload["profile"]["phone"],
                        "profile.phone",
                    )
                elif field["type"] == "select" and "country" in label:
                    kind, value, source = "select", "India", "answers.country"
                elif field["type"] == "file" and "resume" in label:
                    kind, value, source = (
                        "upload_resume",
                        payload["resume_path"],
                        "resume",
                    )
                elif field["required"]:
                    kind = "ask_user"
                actions.append(
                    FillAction(
                        field_key=field["key"],
                        action=kind,
                        value=value,
                        source=source,
                        question="Complete fixture control manually"
                        if kind == "ask_user"
                        else None,
                    )
                )
            return AnswerPlan(actions=actions)

    services = Services(
        browser=browser,
        llm=Planner(),
        data=Data(),
        channel=FakeChannel(),
        ledger=SQLiteLedger(tmp_path / "ledger.sqlite"),
        allowlist=allowed,
        submission_urls=browser.submission_urls,
        restore_browser=browser.restore,
        target_id=browser.target_id,
    )
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 300}
    try:
        for attempt in range(100):
            try:
                with urlopen(endpoint + "/json/version", timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Chrome CDP failed to start")
        from src.operator.contracts import RunState

        with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
            result = graph.invoke(
                {
                    "run": RunState(
                        run_id="r", goal="Fill synthetic fixture", cdp_endpoint=endpoint
                    ).model_dump(mode="json")
                },
                config,
            )
            for turn in range(8):
                gate = result.get("__interrupt__", [None])[0]
                if not gate or gate.value["kind"] == "review":
                    break
                if gate.value["kind"] == "answer":
                    result = graph.invoke(
                        Resume(
                            resume={
                                "command_id": f"answer-{turn}",
                                "run_id": "r",
                                "job_id": "fixture",
                                "action": "answer",
                                "field_key": gate.value["field_key"],
                                "value": True,
                            }
                        ),
                        config,
                    )
                    continue
                assert gate.value["kind"] == "handoff", gate.value

                # Test-only human actor: synthetic fixture consent and work answers;
                # this code is outside operator nodes and never targets a CAPTCHA.
                def human():
                    for selector in [
                        "[name=privacy_consent]",
                        "[name=consent_terms]",
                        "[name=work_authorization][value=authorized]",
                        "[name=work_eligibility][value=yes]",
                    ]:
                        control = browser.page.locator(selector)
                        if control.count() and (
                            control.is_visible()
                            or control.locator("xpath=ancestor::label[1]").is_visible()
                        ):
                            control.check(force=True)
                    control = browser.page.locator("[name=requires_sponsorship]")
                    if control.count():
                        browser.page.locator(".yn button[data-answer=no]").first.click()

                services.call(browser._call(human))
                result = graph.invoke(
                    Resume(
                        resume={
                            "command_id": f"human-{turn}",
                            "run_id": "r",
                            "action": "handoff_done",
                        }
                    ),
                    config,
                )
            run = _current_run(graph.get_state(config, subgraphs=True))
            assert run.jobs["fixture"].status.value == "READY_FOR_REVIEW", (
                run.model_dump()
            )
            assert server.RequestHandlerClass.store.snapshot()["total"] == 0
            old_hash = run.jobs["fixture"].review_snapshot_hash
            services.ledger.record_approval(
                "r",
                "fixture",
                "a" * 64,
                old_hash,
                datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            edited_field = next(
                item for item in run.jobs["fixture"].fields if item.type == "email"
            )
            inputs_before_edit = input_count()
            result = graph.invoke(
                Resume(
                    resume={
                        "command_id": "edit-email",
                        "run_id": "r",
                        "job_id": "fixture",
                        "action": "edit",
                        "field_key": edited_field.key,
                        "value": "edited-synthetic@example.test",
                        "snapshot_hash": old_hash,
                    }
                ),
                config,
            )
            assert result.get("__interrupt__"), result
            assert result["__interrupt__"][0].value["kind"] == "review"
            run = _current_run(graph.get_state(config, subgraphs=True))
            assert run.jobs["fixture"].review_snapshot_hash != old_hash
            assert input_count() == inputs_before_edit + 1
            assert not services.ledger.consume_approval(
                "r", "fixture", "a" * 64, old_hash
            )
            before = services.llm.calls
            before_inputs = input_count()
        services.call(browser.disconnect())
        browser = BrowserBridge(CDPBrowserManager(), allowed, tmp_path / "evidence")
        services.browser = browser
        services.submission_urls = browser.submission_urls
        services.restore_browser = browser.restore
        services.target_id = browser.target_id
        services.call(browser.attach(endpoint, run.jobs["fixture"].browser_target_id))
        services.call(browser.restore(run.jobs["fixture"]))
        with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
            job = run.jobs["fixture"]
            services.ledger.record_approval(
                "r",
                "fixture",
                "b" * 64,
                job.review_snapshot_hash,
                datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            result = graph.invoke(
                Resume(
                    resume={
                        "command_id": "approve",
                        "run_id": "r",
                        "job_id": "fixture",
                        "action": "approve",
                        "token_hash": "b" * 64,
                        "snapshot_hash": job.review_snapshot_hash,
                    }
                ),
                config,
            )
            assert result["run"]["jobs"]["fixture"]["status"] == "SUBMITTED_VERIFIED", (
                result
            )
            assert services.llm.calls == before
            assert input_count() == before_inputs
            assert server.RequestHandlerClass.store.snapshot()["total"] == 1
            graph.invoke(None, config)
            assert server.RequestHandlerClass.store.snapshot()["total"] == 1
            (tmp_path / "result.json").write_text(
                json.dumps(
                    {
                        "layout": layout,
                        "status": result["run"]["jobs"]["fixture"]["status"],
                        "submissions_before_approval": 0,
                        "submissions_after_reentry": 1,
                        "new_planning_calls_on_resume": services.llm.calls - before,
                        "new_input_events_on_resume": input_count() - before_inputs,
                        "edited_field_input_events": 1,
                        "old_approval_rejected": True,
                        "fresh_browser_bridge_reattached": True,
                        "elapsed_s": round(time.monotonic() - started, 3),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        services.call(browser.disconnect())
        services.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
