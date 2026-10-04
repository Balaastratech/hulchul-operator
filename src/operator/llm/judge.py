"""LLM-based semantic judge for verify_fill and verify_submission."""

from __future__ import annotations

import logging
from pydantic import BaseModel, Field

from src.operator.llm.protocol import LLMPort

logger = logging.getLogger(__name__)


class SemanticMatchResult(BaseModel):
    """Result of comparing expected field value with actual form value."""
    match: bool = Field(description="True if the actual value semantically matches expected.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    reason: str = Field(description="Brief explanation of why it matched or differed.")


class SubmissionConfirmationResult(BaseModel):
    """Result of inspecting page text to verify whether an application was submitted."""
    confirmed: bool = Field(description="True if the page conclusively confirms application submission.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    application_id: str | None = Field(default=None, description="Extracted confirmation or application ID if present.")
    evidence_reason: str = Field(description="Detailed reason citing specific phrases or signals found.")


class SemanticJudge:
    """Judge utility for comparing fuzzy form field values and verifying submission success."""

    def __init__(self, llm_port: LLMPort) -> None:
        self.llm = llm_port

    def is_semantic_match(
        self,
        expected: str,
        actual: str,
        field_label: str = "",
    ) -> SemanticMatchResult:
        """Compare expected and actual form field values for semantic equivalence."""
        exp_norm = expected.strip().lower()
        act_norm = actual.strip().lower()

        # Reject empty read-back or expected (AUDIT-013)
        if not act_norm or not exp_norm:
            return SemanticMatchResult(
                match=False,
                confidence=1.0 if not act_norm and exp_norm else 0.0,
                reason="Empty read-back or expected value",
            )

        # Fast deterministic path
        if exp_norm == act_norm:
            return SemanticMatchResult(match=True, confidence=1.0, reason="Exact match")
        if exp_norm in act_norm or act_norm in exp_norm:
            return SemanticMatchResult(match=True, confidence=0.95, reason="Substring containment match")

        if not self.llm:
            return SemanticMatchResult(match=False, confidence=0.0, reason="No LLM port available")

        prompt = (
            "You are an automated evaluation judge verifying form field read-back.\n"
            "Compare the expected value intended by the candidate with the actual value read from the form input.\n"
            f"Field label/context: {field_label}\n"
            f"Expected value: {expected}\n"
            f"Actual value: {actual}\n\n"
            "Do these two values represent the same underlying answer? "
            "(For example: 'Ahmedabad, India' and 'Ahmedabad, Gujarat, India' represent the same answer; "
            "'3 years' and '3' represent the same answer for experience; "
            "'+91 98765 43210' and '+919876543210' are identical phone numbers)."
        )

        try:
            result, _ = self.llm.generate_structured(
                prompt=prompt,
                schema=SemanticMatchResult,
                temperature=0.0,
            )
            return result
        except Exception as e:
            logger.error("Semantic match LLM judgment failed: %s", e)
            return SemanticMatchResult(
                match=False,
                confidence=0.0,
                reason=f"LLM judgment failed: {e}",
            )

    def verify_submission(
        self,
        page_text: str,
        current_url: str,
    ) -> SubmissionConfirmationResult:
        """Evaluate whether a page text indicates successful job application submission."""
        # Fast deterministic signals
        low_text = page_text.lower()

        # Negative / error signals take precedence (AUDIT-014)
        error_signals = [
            "error:",
            "error ",
            "an error occurred",
            "submission failed",
            "failed to submit",
            "no application received",
            "application not received",
            "try again",
            "please fix",
        ]
        if any(err in low_text for err in error_signals):
            return SubmissionConfirmationResult(
                confirmed=False,
                confidence=0.99,
                evidence_reason="Negative error signals detected on page.",
            )

        if any(kw in low_text for kw in [
            "application submitted",
            "thank you for applying",
            "your application has been received",
            "application received",
            "we have received your application",
        ]):
            return SubmissionConfirmationResult(
                confirmed=True,
                confidence=0.99,
                evidence_reason="Deterministic match on clear confirmation phrase.",
            )

        if not self.llm:
            return SubmissionConfirmationResult(
                confirmed=False,
                confidence=0.0,
                evidence_reason="No deterministic confirmation and no LLM port available",
            )

        prompt = (
            "You are a verification judge determining whether a job application was successfully submitted.\n"
            f"Current URL: {current_url}\n"
            f"Page text snippet:\n{page_text[:4000]}\n\n"
            "Determine if this page is a confirmation page indicating successful submission, "
            "or if the user is still on the application form / an error page / login wall."
        )

        try:
            result, _ = self.llm.generate_structured(
                prompt=prompt,
                schema=SubmissionConfirmationResult,
                temperature=0.0,
            )
            return result
        except Exception as e:
            logger.error("Submission confirmation LLM judgment failed: %s", e)
            return SubmissionConfirmationResult(
                confirmed=False,
                confidence=0.0,
                evidence_reason=f"LLM judgment failed: {e}",
            )
