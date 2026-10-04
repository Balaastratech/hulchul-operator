"""Unit and integration tests for browser core : CDP, evidence, executor."""

import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.operator.browser.cdp import CDPBrowserManager
from src.operator.browser.evidence import EvidenceManager
from src.operator.browser.execute import ActionExecutor
from src.operator.browser.models import ActionResult, FieldSpec, FillAction


def test_evidence_manager_file_creation(tmp_path: Path):
    """Test EvidenceManager records diffs and writes structured JSON."""
    ev = EvidenceManager(run_id="test_run", base_dir=tmp_path)
    diff_path = ev.record_diff("full_name|text||0", "Aarav Mehta", "Aarav Mehta", matched=True)
    assert Path(diff_path).exists()
    assert "Aarav Mehta" in Path(diff_path).read_text()


def test_action_executor_on_local_html(tmp_path: Path):
    """Test ActionExecutor on an HTML page containing text, select, and checkbox controls."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
      <form id="app-form">
        <label for="fname">First Name</label>
        <input type="text" id="fname" name="first_name" data-opid="1">

        <label for="email">Email</label>
        <input type="email" id="email" name="email" data-opid="2">

        <label for="role">Target Role</label>
        <select id="role" name="role" data-opid="3">
          <option value="">Choose role</option>
          <option value="swe">Software Engineer</option>
          <option value="pm">Product Manager</option>
        </select>

        <label for="auth">
          <input type="checkbox" id="auth" name="authorized" data-opid="4">
          Authorized to work
        </label>
      </form>
    </body>
    </html>
    """
    html_file = tmp_path / "form.html"
    html_file.write_text(html, encoding="utf-8")

    ev = EvidenceManager(run_id="run_exec_test", base_dir=tmp_path)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(f"file:///{html_file.as_posix()}")

        executor = ActionExecutor(page, evidence_manager=ev)

        # 1. Fill text
        f1 = FieldSpec(id="1", key="first_name|text||0", label="First Name", type="text")
        a1 = FillAction(field_key="first_name|text||0", action="fill", value="Aarav")
        r1 = executor.execute_action(a1, f1)
        assert r1.success is True
        assert r1.actual == "Aarav"
        assert len(r1.evidence) >= 1

        # 2. Select option
        f3 = FieldSpec(id="3", key="role|select||0", label="Target Role", type="select", options=["Software Engineer", "Product Manager"])
        a3 = FillAction(field_key="role|select||0", action="select", value="Software Engineer")
        r3 = executor.execute_action(a3, f3)
        assert r3.success is True
        assert len(r3.evidence) >= 1

        # 3. Check checkbox
        f4 = FieldSpec(id="4", key="auth|checkbox||0", label="Authorized to work", type="checkbox")
        a4 = FillAction(field_key="auth|checkbox||0", action="check", value="true")
        r4 = executor.execute_action(a4, f4)
        assert r4.success is True
        assert r4.actual == "true"
        assert len(r4.evidence) >= 1

        # 4. Skip action
        f_skip = FieldSpec(id="99", key="skip|text||0", label="Optional", type="text")
        a_skip = FillAction(field_key="skip|text||0", action="skip")
        r_skip = executor.execute_action(a_skip, f_skip)
        assert r_skip.success is True
        assert r_skip.reason == "Skipped as requested"

        # 5. Ask user escalation
        f_ask = FieldSpec(id="100", key="salary|text||0", label="Salary", type="text")
        a_ask = FillAction(field_key="salary|text||0", action="ask_user", question="What is your salary requirement?")
        r_ask = executor.execute_action(a_ask, f_ask)
        assert r_ask.success is False
        assert "Escalated to human" in r_ask.reason

        browser.close()
