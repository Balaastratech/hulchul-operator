"""Tests for deterministic page state classifier (S7)."""

import pytest
from src.operator.browser.classify import PageStateClassifier
from src.operator.browser.models import PageState


def test_classify_form():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 5,
        "hasPassword": False,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Apply for this role. Fill in your details below.",
        "title": "Job Application",
    }
    assert classifier.classify_deterministic(signals) == PageState.FORM


def test_classify_login_with_password():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 2,
        "hasPassword": True,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Please sign in to continue your application.",
        "title": "Sign In",
    }
    assert classifier.classify_deterministic(signals) == PageState.LOGIN


def test_classify_login_with_keyword_and_few_inputs():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 1,
        "hasPassword": False,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Create an account to track your progress.",
        "title": "Create Account",
    }
    assert classifier.classify_deterministic(signals) == PageState.LOGIN


def test_classify_captcha():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 2,
        "hasPassword": False,
        "hasVisibleCaptcha": True,
        "bodySnippet": "Security check. Please verify you are human.",
        "title": "Challenge",
    }
    assert classifier.classify_deterministic(signals) == PageState.CAPTCHA


def test_classify_closed_job():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 0,
        "hasPassword": False,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Thank you for your interest. This position has been filled.",
        "title": "Position Closed",
    }
    assert classifier.classify_deterministic(signals) == PageState.CLOSED


def test_classify_confirmation():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 0,
        "hasPassword": False,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Thank you for applying! Your application has been received.",
        "title": "Application Submitted",
    }
    assert classifier.classify_deterministic(signals) == PageState.CONFIRMATION


def test_classify_inconclusive():
    classifier = PageStateClassifier()
    signals = {
        "inputCount": 1,
        "hasPassword": False,
        "hasVisibleCaptcha": False,
        "bodySnippet": "Search our knowledge base.",
        "title": "Help Center",
    }
    assert classifier.classify_deterministic(signals) is None


@pytest.mark.live
def test_classify_live_ats_forms():
    """Verify live ATS job forms classify as PageState.FORM deterministically."""
    from playwright.sync_api import sync_playwright

    classifier = PageStateClassifier()
    urls = [
        ("https://job-boards.greenhouse.io/vercel/jobs/6136160004", PageState.FORM),
        ("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply", PageState.FORM),
        ("https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application", PageState.FORM),
        ("https://careers.apna.co/_/j/8161DF2AC9/apply", PageState.FORM),
        ("https://social-discovery-ventures.breezy.hr/p/da175075795901-senior-net-developer-ai-product/apply", PageState.FORM),
    ]
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        for u, expected_state in urls:
            p = b.new_page()
            p.goto(u, wait_until="networkidle", timeout=30000)
            p.wait_for_timeout(1500)
            state = classifier.classify_page(p)
            assert state == expected_state, f"Expected {expected_state} for {u}, got {state}"
            p.close()
        b.close()
