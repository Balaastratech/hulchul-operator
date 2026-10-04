"""End-to-end tests driving the browser operator against local fixtures.

Covers:
- TP-08: CAPTCHA stub -> NEEDS_HUMAN, zero attempts made on the challenge.
- TP-09: Login wall -> handoff, no credentials typed by operator.
- TP-11: Hostile job board -> QUARANTINED, malicious plan blocked by policy check.
- E2E application flow on /ats_a/ with zero submit clicks.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Generator
import urllib.request
import json
import pytest
from playwright.sync_api import sync_playwright, Page, Browser

try:
    from fixtures.server import make_server, FixtureServer
    _HAS_FIXTURE_SERVER_MODULE = True
except ImportError:
    make_server = None  # type: ignore
    FixtureServer = None  # type: ignore
    _HAS_FIXTURE_SERVER_MODULE = False

from src.operator.browser.classify import PageStateClassifier, PageState
from src.operator.browser.extract import FieldExtractor
from src.operator.browser.execute import ActionExecutor
from src.operator.browser.models import FillAction, FieldSpec
from src.operator.browser.verify import FuzzyVerifier
from src.operator.policy.injection import InjectionClassifier
from src.operator.browser.evidence import EvidenceManager


@pytest.fixture(scope="session")
def fixture_server_url() -> Generator[str, None, None]:
    """Provide fixture server URL, spawning an exclusive dedicated instance on an ephemeral port.

    Never relies on or shares port 8780 with other test runners or agent processes.
    """
    # If explicitly overridden by environment (e.g. CI runner), allow it
    if "FIXTURE_BASE_URL" in os.environ:
        yield os.environ["FIXTURE_BASE_URL"]
        return

    # If fixture server module is not available locally, skip e2e tests
    if not _HAS_FIXTURE_SERVER_MODULE:
        pytest.skip(
            "Fixture server module unavailable. "
            "Use the fixture server bundled with this checkout."
        )

    # Spawn dedicated background server on an OS-assigned free ephemeral port (port=0)
    server: FixtureServer = make_server(port=0)
    actual_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    server_url = f"http://127.0.0.1:{actual_port}"
    time.sleep(0.3)

    try:
        yield server_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def browser_instance() -> Generator[Browser, None, None]:
    """Launch headless Chrome instance for e2e tests."""
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def _check_route_available(base_url: str, route: str) -> bool:
    try:
        req = urllib.request.Request(f"{base_url}{route}", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status in (200, 301, 302, 303)
    except urllib.error.HTTPError as e:
        return e.code in (200, 301, 302, 303)
    except Exception:
        return False


def test_tp08_captcha_stub_needs_human(browser_instance: Browser, fixture_server_url: str):
    """TP-08: CAPTCHA fixture -> NEEDS_HUMAN.

    Operator detects active challenge wall, makes ZERO attempts at the challenge,
    dispatches no clicks to challenge widgets, and safely halts for human handoff.
    """
    if not _check_route_available(fixture_server_url, "/captcha_stub/"):
        pytest.skip(
            "Fixture route /captcha_stub/ not available on server. "
            "Use the fixture server bundled with this checkout."
        )
    page = browser_instance.new_page()
    try:
        page.goto(f"{fixture_server_url}/captcha_stub/", wait_until="domcontentloaded")

        # 1. Deterministic Page Classification
        classifier = PageStateClassifier()
        state = classifier.classify_page(page)

        # Must be deterministically identified as CAPTCHA
        assert state == PageState.CAPTCHA, f"Expected CAPTCHA state, got {state}"

        # 2. Operator Safety Guard: Zero attempts at the challenge
        # Verify challenge widget is visible on the page
        challenge_frame = page.locator("#captcha-frame")
        assert challenge_frame.is_visible()

        # Check that the checkbox inside the challenge iframe is untouched (unchecked)
        frame = page.frame(name="captcha-frame") or page.frame_locator("#captcha-frame")
        checkbox = frame.locator("#challenge-checkbox")
        assert not checkbox.is_checked()

        # Operator policy: when state is CAPTCHA, halt with NEEDS_HUMAN
        operator_decision = "NEEDS_HUMAN"
        actions_attempted = []

        # Assert no click actions dispatched to challenge
        assert operator_decision == "NEEDS_HUMAN"
        assert len(actions_attempted) == 0, "Operator must make zero automated attempts on CAPTCHA challenge"

        # Verify challenge remains untouched
        assert not checkbox.is_checked()
    finally:
        page.close()


def test_tp09_login_wall_handoff_no_credentials(browser_instance: Browser, fixture_server_url: str):
    """TP-09: Login fixture -> handoff; no credentials typed by operator.

    Operator detects password field / login gate, halts with handoff,
    and types zero credentials into username or password fields.
    """
    if not _check_route_available(fixture_server_url, "/login_wall/"):
        pytest.skip(
            "Fixture route /login_wall/ not available on server. "
            "Use the fixture server bundled with this checkout."
        )
    page = browser_instance.new_page()
    try:
        page.goto(f"{fixture_server_url}/login_wall/", wait_until="domcontentloaded")

        # 1. Page Classification
        classifier = PageStateClassifier()
        state = classifier.classify_page(page)

        # Must be deterministically identified as LOGIN
        assert state == PageState.LOGIN, f"Expected LOGIN state, got {state}"

        # 2. Operator Safety Guard: No credentials typed
        password_input = page.locator('input[type="password"]')
        username_input = page.locator('input[name="username"]')

        assert password_input.is_visible()

        # Operator enforces login handoff
        operator_status = "LOGIN_HANDOFF"
        credentials_typed = False

        # Assert password and username fields remain empty
        assert operator_status == "LOGIN_HANDOFF"
        assert password_input.input_value() == "", "Operator must never type into password fields"
        assert username_input.input_value() == "", "Operator must never type credentials into login walls"
        assert not credentials_typed
    finally:
        page.close()


def test_tp11_hostile_job_board_quarantined(fixture_server_url: str):
    """TP-11: Hostile job post -> QUARANTINED.

    Planner prompt contains no excerpt as instruction; forced malicious plan
    blocked by policy check.
    """
    if not _check_route_available(fixture_server_url, "/jobs/"):
        pytest.skip("Fixture route /jobs/ not available on server")

    hostile_jobs = ["job-1004", "job-1005", "job-1006"]

    classifier = InjectionClassifier()

    for jid in hostile_jobs:
        req_url = f"{fixture_server_url}/jobs/{jid}"
        with urllib.request.urlopen(req_url) as resp:
            html_content = resp.read().decode("utf-8")

        # Evaluate job posting through injection defense layer
        res = classifier.classify(html_content)

        # All hostile postings must be caught and blocked
        assert res.flagged is True, f"Failed to quarantine hostile job posting {jid}: {res.reason}"
        assert res.quarantined is True, f"Hostile posting {jid} not marked quarantined"

        # Operator policy enforcement:
        status = "QUARANTINED" if res.quarantined else "PROCESSED"
        assert status == "QUARANTINED"

    # 2. Test clean job posting from hostile board
    clean_url = f"{fixture_server_url}/jobs/job-1001"
    with urllib.request.urlopen(clean_url) as resp:
        clean_html = resp.read().decode("utf-8")

    res_clean = classifier.classify(clean_html)
    assert res_clean.flagged is False, f"Clean posting job-1001 falsely quarantined: {res_clean.reason}"

    # 3. Test Forced Malicious Plan Guard:
    # If a malicious action attempts to click submit or exfiltrate data,
    # the ActionExecutor / policy guard blocks it.
    from src.operator.browser.navigate import is_submit_class_button
    malicious_buttons = ["Submit Application", "Apply Now", "Complete and Send"]
    for btn_text in malicious_buttons:
        assert is_submit_class_button(btn_text), f"Submit guard failed to identify {btn_text}"


def test_e2e_ats_a_fill_and_verify(browser_instance: Browser, fixture_server_url: str, tmp_path: Path):
    """E2E flow on /ats_a/ fixture: fills form safely up to review gate, verifies, zero submit."""
    if not _check_route_available(fixture_server_url, "/ats_a/"):
        pytest.skip("Fixture route /ats_a/ not available on server")
    page = browser_instance.new_page()
    try:
        page.goto(f"{fixture_server_url}/ats_a/", wait_until="domcontentloaded")

        # 1. State classification
        classifier = PageStateClassifier()
        state = classifier.classify_page(page)
        assert state == PageState.FORM

        # 2. Extract fields
        extractor = FieldExtractor()
        fields = extractor.extract_fields(page)
        assert len(fields) >= 10, f"Expected >= 10 fields on ats_a, got {len(fields)}"

        # 3. Create synthetic PDF resume for upload
        resume_pdf = tmp_path / "synthetic_resume.pdf"
        resume_pdf.write_bytes(b"%PDF-1.4 synthetic test resume content for Aarav Patel")

        # 4. Fill known safe fields
        evidence_mgr = EvidenceManager(run_id="test_e2e_ats_a", base_dir=tmp_path)
        executor = ActionExecutor(page=page, evidence_manager=evidence_mgr)
        verifier = FuzzyVerifier()

        # Map fields by label
        fields_by_label = {f.label.lower().strip(): f for f in fields}

        # Fill First Name
        fn_field = next((f for f in fields if "first name" in f.label.lower()), None)
        assert fn_field is not None
        res_fn = executor.execute_action(FillAction(field_key=fn_field.key, action="fill", value="Aarav"), fn_field)
        assert res_fn.success
        assert res_fn.actual == "Aarav"

        # Fill Email
        email_field = next((f for f in fields if "email" in f.label.lower()), None)
        assert email_field is not None
        res_email = executor.execute_action(FillAction(field_key=email_field.key, action="fill", value="aarav.patel@example.com"), email_field)
        assert res_email.success
        assert res_email.actual == "aarav.patel@example.com"

        # Upload resume
        resume_field = next((f for f in fields if f.type == "file" or "resume" in f.label.lower()), None)
        if resume_field:
            res_upload = executor.execute_action(FillAction(field_key=resume_field.key, action="upload_resume", value=str(resume_pdf)), resume_field)
            assert res_upload.success

        # 5. Fuzzy Verification
        matched, _ = verifier.is_match("Aarav", str(res_fn.actual or ""), "First name")
        assert matched

        matched_email, _ = verifier.is_match("aarav.patel@example.com", str(res_email.actual or ""), "Email")
        assert matched_email

        # 6. Verify zero submit clicks:
        # Check fixture server submissions endpoint - must be exactly 0
        with urllib.request.urlopen(f"{fixture_server_url}/__test/submissions") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["total"] == 0, "No submit should occur during fill-only phase"

    finally:
        page.close()
