"""Tests for StepNavigator and submit button safety guards (S11)."""

import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.operator.browser.navigate import (
    StepNavigator,
    is_safe_next_button,
    is_submit_class_button,
)


def test_submit_class_button_recognition():
    """Verify submit-class buttons are identified accurately."""
    submit_terms = [
        "Submit",
        "SUBMIT APPLICATION",
        "Apply Now",
        "Apply",
        "Complete Application",
        "Send Application",
        "Finish",
    ]
    for term in submit_terms:
        assert is_submit_class_button(term) is True, f"Expected {term} to be submit-class"
        assert is_safe_next_button(term) is False, f"Expected {term} NOT to be safe next"


def test_safe_next_button_recognition():
    """Verify safe multi-step navigation terms are recognized."""
    safe_terms = [
        "Next",
        "Continue",
        "Save & Continue",
        "Save and Continue",
        "Proceed",
        "Next Step",
        "Review Application",
    ]
    for term in safe_terms:
        assert is_safe_next_button(term) is True, f"Expected {term} to be safe next"
        assert is_submit_class_button(term) is False, f"Expected {term} NOT to be submit-class"


def test_multi_step_progression_with_submit_guard(tmp_path: Path):
    """Test multi-step navigation advances step 1 to step 2, but stops at submit."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
      <div id="step1" style="display: block;">
        <h2>Step 1: Personal Info</h2>
        <input type="text" id="name" value="Aarav">
        <button id="btn-next" type="button" onclick="
          document.getElementById('step1').style.display='none';
          document.getElementById('step2').style.display='block';
        ">Next</button>
      </div>
      <div id="step2" style="display: none;">
        <h2>Step 2: Review & Submit</h2>
        <p>Review your information.</p>
        <button id="btn-submit" type="submit">Submit Application</button>
      </div>
    </body>
    </html>
    """
    f = tmp_path / "multistep.html"
    f.write_text(html, encoding="utf-8")

    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        p = b.new_page()
        p.goto(f"file:///{f.as_posix()}")

        nav = StepNavigator(p)

        # On Step 1: should find "Next" and advance to Step 2
        advanced = nav.click_next(wait_time=500)
        assert advanced is True
        assert p.locator("#step2").is_visible()

        # On Step 2: only "Submit Application" exists -> MUST NOT CLICK!
        advanced_again = nav.click_next(wait_time=500)
        assert advanced_again is False

        # Confirm submit button was NEVER clicked
        submit_btn = p.locator("#btn-submit")
        assert submit_btn.is_visible()

        b.close()
