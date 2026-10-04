"""Offline containment regressions against real Chrome and production primitives."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from scripts.rehearse_real_forms import (
    GUARD_SCRIPT,
    Decision,
    Plan,
    guarded_action,
    persona,
    rehearse,
    save,
)
from src.operator.browser.models import FieldSpec


@pytest.fixture(scope="module")
def browser():
    """Use system Chrome with no external pages or private profile."""
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def server():
    """Own an ephemeral loopback server, with independently observed writes."""
    state = {"html": "", "writes": 0, "leaks": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/leak"):
                state["leaks"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(state["html"].encode())

        def do_POST(self):
            state["writes"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


class FakePlanner:
    """Only return source choices; never send a real Gemini request in tests."""

    def __init__(self):
        self.calls = 0

    def generate_structured(self, prompt, schema):
        self.calls += 1
        payload = json.loads(prompt.split("\n", 1)[1])
        return Plan(
            decisions=[
                Decision(
                    field_key=f["field_key"],
                    source=f["allowed_source"],
                    reason="Explicit synthetic contact source",
                )
                for f in payload["fields"]
            ]
        ), SimpleNamespace(
            usage=SimpleNamespace(model_dump=lambda **kw: {"total_tokens": 0})
        )


def target(url):
    return {"id": "fixture", "url": url, "source_api": url, "discovered_at": "offline"}


def test_independent_guard_blocks_click_enter_direct_submit_and_writes(browser, server):
    url, state = server
    state["html"] = (
        '<form action="/submit" method="post"><input name="name"><button>Continue</button><input type="submit" value="Apply"></form>'
    )
    context = browser.new_context(service_workers="block")
    context.add_init_script(GUARD_SCRIPT)
    page = context.new_page()
    page.goto(url)
    page.locator("button").click()
    page.locator('[type="submit"]').click()
    page.locator("input[name=name]").press("Enter")
    assert (
        page.evaluate(
            "() => {try {document.querySelector('form').submit();} catch(e) {return e.message;}}"
        )
        == "FILL_ONLY"
    )
    assert (
        page.evaluate(
            "() => {try {document.querySelector('form').requestSubmit();} catch(e) {return e.message;}}"
        )
        == "FILL_ONLY"
    )
    page.evaluate(
        "fetch('/submit', {method:'POST'}).catch(() => {}); navigator.sendBeacon('/submit','x');"
    )
    assert state["writes"] == 0
    assert len(page.evaluate("window.__rehearsalGuard.blocked")) >= 7
    context.close()


@pytest.mark.parametrize(
    "wall, expected",
    [
        ('<input type="password">Sign in to apply', "login"),
        ("<p>Verify you are human</p>", "captcha"),
        ("<p>This job is closed</p>", "closed"),
    ],
)
def test_blockers_never_plan_or_fill(browser, server, tmp_path, wall, expected):
    url, state = server
    state["html"] = (
        wall
        + '<label>First name<input></label><label>Last name<input></label><label>Email<input type="email"></label>'
    )
    llm = FakePlanner()
    result = rehearse(target(url), browser, llm, persona(), tmp_path)
    assert result["blocker"] == expected
    assert result["llm_calls"] == llm.calls == 0
    assert all(
        f["actual"] == "" and f["outcome"] == "skipped" for f in result["fields"]
    )
    assert state["writes"] == state["leaks"] == 0


def test_real_stack_fresh_readback_and_no_autosave(browser, server, tmp_path):
    url, state = server
    state["html"] = (
        """<form><label>First name<input oninput="fetch('/leak?value='+this.value);fetch('/submit',{method:'POST'});this.form.requestSubmit()"></label><label>Last name<input></label><label>Email<input type="email"></label><label>Consent<input type="checkbox" required></label><label>City<input role="combobox"></label><button>Continue</button></form>"""
    )
    llm = FakePlanner()
    result = rehearse(target(url), browser, llm, persona(), tmp_path)
    assert result["status"] == "FILL_ONLY_REVIEW", result
    assert sum(f["outcome"] == "filled_verified" for f in result["fields"]) == 3
    assert len(result["fields"]) == 5
    assert llm.calls == 1
    assert state["writes"] == state["leaks"] == 0
    save([result], [target(url)], tmp_path, tmp_path / "report.md", "offline", "test")
    assert (tmp_path / "report.html").exists()
    assert '"filled_verified": 3' in (tmp_path / "results.json").read_text()


def test_source_injection_and_custom_controls_fail_closed():
    for label, kind, combo in [
        ("Consent", "text", False),
        ("Email", "text", True),
        ("Password", "password", False),
        ("Email", "submit", False),
    ]:
        f = FieldSpec(
            id="1", key="k", label=label, type=kind, is_combobox=combo, required=True
        )
        action, _ = guarded_action(
            f, Decision(field_key="k", source="email", reason="malicious"), persona()
        )
        assert action.action == "ask_user"
    f = FieldSpec(id="1", key="k", label="Email", type="email")
    action, _ = guarded_action(
        f, Decision(field_key="k", source="phone", reason="wrong source"), persona()
    )
    assert action.action == "skip"


def test_fuzzy_positive_never_overrides_actual_email(browser, server, tmp_path):
    url, state = server
    state["html"] = (
        "<label>First name<input></label><label>Last name<input></label><label>Email<input type=\"email\" oninput=\"this.value=this.value.replace('example.test','evil.example.test')\"></label>"
    )
    result = rehearse(target(url), browser, FakePlanner(), persona(), tmp_path)
    email = next(f for f in result["fields"] if f["label"] == "Email")
    assert email["outcome"] == "unverified"
    assert not email["matched"]
    assert email["actual"] != email["intended"]


def test_planner_failure_preserves_all_fields_and_never_executes(
    browser, server, tmp_path
):
    url, state = server
    state["html"] = (
        '<label>First name<input></label><label>Last name<input></label><label>Email<input type="email"></label>'
    )

    class BrokenPlanner:
        def generate_structured(self, *args):
            raise ValueError("Do not include this potentially sensitive message")

    result = rehearse(target(url), browser, BrokenPlanner(), persona(), tmp_path)
    assert result["status"] == "SITE_ERROR"
    assert result["site_errors"] == ["ValueError"]
    assert len(result["fields"]) == 3
    assert all(
        f["outcome"] == "skipped" and f["actual"] == "" for f in result["fields"]
    )
    assert state["writes"] == state["leaks"] == 0
