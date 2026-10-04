"""Deterministic-first page state classifier for ATS application forms."""

from __future__ import annotations

import logging
import re
from typing import Any
from playwright.sync_api import Page
from pydantic import BaseModel, Field

from src.operator.browser.models import PageState
from src.operator.llm.protocol import LLMPort

logger = logging.getLogger(__name__)

DOM_SIGNAL_SCRIPT = """() => {
    const inputs = [...document.querySelectorAll('input, textarea, select')].filter(e => e.type !== 'hidden');
    const hasPassword = inputs.some(e => e.type === 'password');
    
    // Active blocking CAPTCHA challenge signals vs embedded background badge:
    // Embedded background badges (e.g. reCAPTCHA enterprise anchor) exist on almost all ATS forms.
    // A blocking challenge wall is either:
    // 1) An active challenge popup/bframe (e.g. picture puzzle / challenge modal with width > 200 and height > 200)
    // 2) An interstitial challenge page where inputs.length < 3
    const challengeIframes = [...document.querySelectorAll(
        'iframe[src*="bframe"], iframe[src*="challenge"], iframe[title*="challenge" i], iframe[title*="recaptcha challenge" i]'
    )];
    const hasActiveChallenge = challengeIframes.some(el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 200 && rect.height > 200 && style.visibility !== 'hidden' && style.display !== 'none';
    });

    const hasAnyCaptchaElement = !!document.querySelector(
        'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], iframe[src*="captcha"], iframe[src*="datadome"], .g-recaptcha, .h-captcha, [data-sitekey]'
    );
    const isCaptchaWall = hasActiveChallenge || (hasAnyCaptchaElement && inputs.length < 3);

    const bodyText = (document.body ? document.body.innerText : '').slice(0, 4000);
    const title = document.title || '';
    const buttons = [...document.querySelectorAll('button, input[type=submit], input[type=button]')]
        .map(b => (b.innerText || b.value || '').trim())
        .filter(Boolean)
        .slice(0, 15);

    return {
        inputCount: inputs.length,
        hasPassword: hasPassword,
        hasVisibleCaptcha: isCaptchaWall,
        hasActiveChallenge: hasActiveChallenge,
        bodySnippet: bodyText,
        title: title,
        buttons: buttons,
        url: window.location.href
    };
}"""



CLOSED_PATTERNS = [
    r"no longer accepting applications",
    r"this job (?:is|has been) closed",
    r"position (?:has been )?filled",
    r"posting has expired",
    r"this role is no longer available",
    r"job requisition (?:is )?closed",
    r"applications for this position are closed",
]

CONFIRMATION_PATTERNS = [
    r"application (?:has been )?submitted",
    r"thank you for applying",
    r"your application has been received",
    r"we have received your application",
    r"application received",
    r"thanks for applying",
]

LOGIN_PATTERNS = [
    r"\b(?:sign in|log in|login|sign-in)\b",
    r"\bcreate an? account\b",
]


class LLMClassification(BaseModel):
    state: PageState = Field(description="Classified state: FORM, LOGIN, CAPTCHA, CLOSED, UNSUPPORTED, CONFIRMATION, UNKNOWN")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class PageStateClassifier:
    """Classifies page state with deterministic checks first, falling back to LLM."""

    def __init__(self, llm_port: LLMPort | None = None) -> None:
        self.llm = llm_port

    def classify_deterministic(self, signals: dict[str, Any]) -> PageState | None:
        """Evaluate deterministic DOM signals without network or LLM calls."""
        input_count = signals.get("inputCount", 0)
        has_password = signals.get("hasPassword", False)
        has_captcha = signals.get("hasVisibleCaptcha", False)
        body = signals.get("bodySnippet", "").lower()
        title = signals.get("title", "").lower()

        # 1. CAPTCHA detection (highest priority for human handoff safety)
        if has_captcha:
            logger.info("Deterministic signal: visible CAPTCHA challenge detected")
            return PageState.CAPTCHA

        # 2. Login wall check (password field takes precedence over incidental confirmation text)
        if has_password:
            logger.info("Deterministic signal: password field detected (LOGIN)")
            return PageState.LOGIN

        # 3. Closed job check
        for pat in CLOSED_PATTERNS:
            if re.search(pat, body) or re.search(pat, title):
                logger.info("Deterministic signal: closed job pattern '%s' matched", pat)
                return PageState.CLOSED

        # 4. Confirmation check
        for pat in CONFIRMATION_PATTERNS:
            if re.search(pat, body) or re.search(pat, title):
                logger.info("Deterministic signal: confirmation pattern '%s' matched", pat)
                return PageState.CONFIRMATION

        # 5. Text-based login pattern check
        for pat in LOGIN_PATTERNS:
            if (re.search(pat, body) or re.search(pat, title)) and input_count < 3:
                logger.info("Deterministic signal: login text with inputCount=%d (LOGIN)", input_count)
                return PageState.LOGIN

        # 6. Application Form check
        if input_count >= 3:
            logger.info("Deterministic signal: %d inputs found (FORM)", input_count)
            return PageState.FORM

        # Inconclusive
        return None

    def classify_page(self, page: Page) -> PageState:
        """Classify live page using deterministic signals first, then LLM fallback."""
        try:
            signals = page.evaluate(DOM_SIGNAL_SCRIPT)
        except Exception as e:
            logger.warning("Failed to evaluate DOM signals on page: %s", e)
            return PageState.UNKNOWN

        state = self.classify_deterministic(signals)
        if state is not None:
            return state

        # If deterministic signals are inconclusive, try LLM fallback if configured
        if self.llm is not None:
            prompt = (
                "You are an automated browser operator evaluating a web page.\n"
                f"Page URL: {signals.get('url', '')}\n"
                f"Page Title: {signals.get('title', '')}\n"
                f"Input Count: {signals.get('inputCount', 0)}\n"
                f"Buttons Seen: {signals.get('buttons', [])}\n"
                f"Body Text Snippet:\n{signals.get('bodySnippet', '')[:2000]}\n\n"
                "Classify this page into exactly one of: "
                "FORM, LOGIN, CAPTCHA, CLOSED, UNSUPPORTED, CONFIRMATION, UNKNOWN."
            )
            try:
                res, _ = self.llm.generate_structured(prompt=prompt, schema=LLMClassification)
                logger.info("LLM classified page as %s (confidence: %.2f, reason: %s)", res.state, res.confidence, res.reason)
                return res.state
            except Exception as e:
                logger.warning("LLM classification fallback failed: %s", e)

        return PageState.UNKNOWN
