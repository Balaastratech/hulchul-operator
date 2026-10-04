"""Multi-step navigation for ATS forms with safe 'next' vocabulary and strict submit guard."""

from __future__ import annotations

import logging
import re
from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

# Safe vocabulary for advancing multi-step forms
SAFE_NEXT_PATTERNS = [
    r"^next\b",
    r"^continue\b",
    r"^save\s*(?:&|and)\s*continue\b",
    r"^proceed\b",
    r"^go to next step\b",
    r"^next step\b",
    r"^step\s*\d+\b",
    r"^review(?:\s*application)?\b",
]

# FORBIDDEN submit-class vocabulary: NEVER clicked during navigation (D-010, D-014)
FORBIDDEN_SUBMIT_PATTERNS = [
    r"\bsubmit\b",
    r"\bapply\b",
    r"\bfinish\b",
    r"\bcomplete\b",
    r"\bsend\b",
    r"\bconfirm application\b",
    r"\bfile application\b",
]


def is_submit_class_button(text: str, button_type: str = "") -> bool:
    """Check if button belongs to the submit class (never clicked by navigation)."""
    # Explicit or default submit type
    b_type = (button_type or "").strip().lower()
    if b_type == "submit":
        return True

    norm = text.strip().lower()
    for pat in FORBIDDEN_SUBMIT_PATTERNS:
        if re.search(pat, norm):
            return True
    return False


def is_safe_next_button(text: str, button_type: str = "") -> bool:
    """Check if button matches allowed safe progression vocabulary and is not submit-class."""
    b_type = (button_type or "").strip().lower()
    if b_type == "submit":
        return False

    norm = text.strip().lower()
    # If it contains any submit pattern, it is NEVER safe
    if is_submit_class_button(norm, button_type=b_type):
        return False
    for pat in SAFE_NEXT_PATTERNS:
        if re.search(pat, norm):
            return True
    return False


class StepNavigator:
    """Handles multi-step form progression without touching submit buttons."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def find_next_button(self) -> Locator | None:
        """Find a safe next/continue button on the current page."""
        candidates = self.page.locator("button, input[type=button], input[type=submit], a[role=button]")
        count = candidates.count()

        for i in range(count):
            loc = candidates.nth(i)
            if not loc.is_visible():
                continue
            text = (loc.inner_text() or loc.get_attribute("value") or "").strip()
            if not text:
                continue

            tag_name = loc.evaluate("el => el.tagName.toLowerCase()")
            raw_type = loc.get_attribute("type") or ""
            # In HTML, a <button> without type attribute defaults to type="submit" inside forms
            if tag_name == "button" and not raw_type:
                # Check if it is inside a form
                is_in_form = loc.evaluate("el => !!el.closest('form')")
                effective_type = "submit" if is_in_form else "button"
            else:
                effective_type = raw_type.lower()

            # Strict submit guard: if submit-class or effective type=submit, skip immediately
            if is_submit_class_button(text, button_type=effective_type):
                logger.debug("Skipping submit-class button: '%s' (type=%s)", text, effective_type)
                continue

            if is_safe_next_button(text, button_type=effective_type):
                logger.info("Found safe next button: '%s'", text)
                return loc

        return None

    def click_next(self, wait_time: float = 2000) -> bool:
        """Click next step button if present and safe.
        
        Returns:
            True if a safe next button was clicked and page advanced.
            False if no safe next button found (i.e. final review/submit step reached).
        """
        btn = self.find_next_button()
        if btn is None:
            logger.info("No safe next button found. Reached final step or single-page form.")
            return False

        btn_text = (btn.inner_text() or btn.get_attribute("value") or "").strip()
        logger.info("Clicking safe next button: '%s'", btn_text)
        try:
            btn.click(timeout=5000)
            self.page.wait_for_timeout(wait_time)
            return True
        except Exception as e:
            logger.warning("Failed to click next button '%s': %s", btn_text, e)
            return False
