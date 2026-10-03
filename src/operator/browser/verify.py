"""Fuzzy verifier and normaliser for comparing intended vs actual form field values."""

from __future__ import annotations

import logging
import re
from typing import Any
from src.operator.browser.models import FieldResult, FieldSpec, FillAction, FillReport
from src.operator.llm.judge import SemanticJudge

logger = logging.getLogger(__name__)


# Common country and location abbreviations
ABBREVIATIONS: dict[str, str] = {
    r"\buk\b": "united kingdom",
    r"\bu\.k\.\b": "united kingdom",
    r"\busa\b": "united states",
    r"\bu\.s\.a\.\b": "united states",
    r"\bus\b": "united states",
    r"\bu\.s\.\b": "united states",
    r"\bny\b": "new york",
    r"\bca\b": "california",
    r"\bb\.?tech\b": "bachelor of technology",
    r"\bb\.?s\.?\b": "bachelor of science",
    r"\bb\.?e\.?\b": "bachelor of engineering",
    r"\bbachelor'?s?\b": "bachelor",
}


def normalise_value(v: Any) -> str:
    """Normalize strings, numbers, and booleans for resilient comparison."""
    if v is None:
        return ""
    s = str(v).strip().lower()

    # Boolean normalization only for explicit words (not numeric '0' or '1' to avoid corrupting years/salary)
    if s in ("true", "yes", "checked", "on"):
        return "true"
    if s in ("false", "no", "unchecked", "off"):
        return "false"

    # Normalize phone: keep only digits if formatted like phone
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 10 and not any(word in s for word in ("salary", "lpa", "inr", "usd", "year")):
        return digits[-10:]  # last 10 digits

    # Standardize abbreviations
    for pattern, replacement in ABBREVIATIONS.items():
        s = re.sub(pattern, replacement, s)

    # Standardize whitespace and remove commas/periods
    s = re.sub(r"[\.,;:!\?]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s



def token_overlap_score(s1: str, s2: str) -> float:
    """Calculate token overlap (Jaccard similarity) between two strings."""
    tokens1 = set(s1.split())
    tokens2 = set(s2.split())
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)


class FuzzyVerifier:
    """Compares intended values with read-back form values using normalisation + LLM judge."""

    def __init__(self, judge: SemanticJudge | None = None) -> None:
        self.judge = judge

    def is_match(
        self,
        intended: Any,
        actual: Any,
        field_label: str = "",
    ) -> tuple[bool, str]:
        """Determine if actual value matches intended value.
        
        Returns:
            (matched: bool, reason: str)
        """
        # Upload resume verification
        if str(intended).endswith(".pdf") or "resume" in field_label.lower():
            if actual and (str(actual).lower() not in ("", "?", "none", "false")):
                return True, "File uploaded successfully"

        norm_exp = normalise_value(intended)
        norm_act = normalise_value(actual)

        # 1. Exact normalized match
        if norm_exp == norm_act:
            return True, "Exact normalized match"

        # 2. Substring match (both must be non-empty)
        if norm_exp and norm_act and (norm_exp in norm_act or norm_act in norm_exp):
            return True, f"Substring match: '{norm_exp}' in '{norm_act}'"


        # 3. Numeric prefix match (e.g. '3 years' vs '3')
        num_exp = re.search(r"\b\d+\b", norm_exp)
        num_act = re.search(r"\b\d+\b", norm_act)
        if num_exp and num_act and num_exp.group() == num_act.group():
            return True, f"Numeric value match: {num_exp.group()}"

        # 4. Token overlap match (e.g. 'Ahmedabad, India' vs 'Ahmedabad, Gujarat, India')
        score = token_overlap_score(norm_exp, norm_act)
        if score >= 0.5:
            return True, f"High token overlap match (score: {score:.2f})"

        # 5. LLM judge fallback for non-exact semantic equivalence
        if self.judge:
            res = self.judge.is_semantic_match(
                expected=str(intended),
                actual=str(actual),
                field_label=field_label,
            )
            if res.match:
                return True, f"LLM semantic match: {res.reason}"

        return False, f"Value mismatch: intended='{intended}' got='{actual}'"

    def verify_actions(
        self,
        actions: list[FillAction],
        fields_by_key: dict[str, FieldSpec],
    ) -> FillReport:
        """Verify all planned actions against extracted fields."""
        results: list[FieldResult] = []

        for act in actions:
            if act.action in ("skip", "ask_user"):
                results.append(
                    FieldResult(
                        field_key=act.field_key,
                        intended=act.value,
                        actual=None,
                        matched=False,
                        escalated=(act.action == "ask_user"),
                        reason=f"Action was {act.action}",
                        generated=act.generated,
                    )
                )
                continue

            field = fields_by_key.get(act.field_key)
            if not field:
                results.append(
                    FieldResult(
                        field_key=act.field_key,
                        intended=act.value,
                        actual=None,
                        matched=False,
                        escalated=False,
                        reason="Field not found in post-execution DOM",
                        generated=act.generated,
                    )
                )
                continue

            matched, reason = self.is_match(
                intended=act.value,
                actual=field.current_value,
                field_label=field.label,
            )

            results.append(
                FieldResult(
                    field_key=act.field_key,
                    intended=act.value,
                    actual=field.current_value,
                    matched=matched,
                    escalated=False,
                    reason=reason,
                    generated=act.generated,
                )
            )

        return FillReport(fields=results)
