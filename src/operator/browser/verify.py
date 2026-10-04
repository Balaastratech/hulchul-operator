"""Fuzzy verifier and normaliser for comparing intended vs actual form field values."""

from __future__ import annotations

import logging
from pathlib import Path
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
        label_lower = (field_label or "").lower()
        s_intended = str(intended).strip() if intended is not None else ""
        s_actual = str(actual).strip() if actual is not None else ""

        # 1. Resume / File upload verification (AUDIT-003)
        if s_intended.endswith(".pdf") or "resume" in label_lower or "cv" in label_lower:
            if not s_actual or s_actual.lower() in ("?", "none", "false", ""):
                return False, "File upload missing or empty"
            intended_filename = Path(s_intended).name.lower()
            actual_filename = Path(s_actual).name.lower()
            base_intended = intended_filename.rsplit(".", 1)[0]
            if intended_filename == actual_filename or base_intended in actual_filename or actual_filename in base_intended:
                return True, f"File name matched: '{actual_filename}'"
            return False, f"File name mismatch: intended='{intended_filename}' got='{actual_filename}'"

        # 2. Email verification (strict equality, case-insensitive, no substring)
        if "email" in label_lower or "@" in s_intended or "@" in s_actual:
            if s_intended.strip().lower() == s_actual.strip().lower():
                return True, "Email exact match"
            return False, f"Email mismatch: intended='{s_intended}' got='{s_actual}'"

        # 3. Phone verification (must match country code and national number)
        if any(w in label_lower for w in ("phone", "mobile", "tel", "contact")):
            digits_exp = re.sub(r"\D", "", s_intended)
            digits_act = re.sub(r"\D", "", s_actual)
            if digits_exp and digits_act:
                if digits_exp == digits_act:
                    return True, "Phone digits exact match"
                if len(digits_exp) == 12 and len(digits_act) == 10 and digits_exp.endswith(digits_act) and digits_exp.startswith("91"):
                    return True, "Phone domestic match with +91 country code"
                if len(digits_act) == 12 and len(digits_exp) == 10 and digits_act.endswith(digits_exp) and digits_act.startswith("91"):
                    return True, "Phone domestic match with +91 country code"
                if len(digits_exp) == 11 and len(digits_act) == 10 and digits_exp.endswith(digits_act) and digits_exp.startswith("1"):
                    return True, "Phone domestic match with +1 country code"
                if len(digits_act) == 11 and len(digits_exp) == 10 and digits_act.endswith(digits_exp) and digits_act.startswith("1"):
                    return True, "Phone domestic match with +1 country code"
            return False, f"Phone mismatch: intended='{s_intended}' got='{s_actual}'"

        # 4. Salary / Compensation / Currency magnitude verification
        if any(w in label_lower for w in ("salary", "compensation", "ctc", "lpa", "inr", "usd")):
            num_exp_str = re.sub(r"[^\d.]", "", s_intended)
            num_act_str = re.sub(r"[^\d.]", "", s_actual)
            if num_exp_str and num_act_str:
                try:
                    num_exp = float(num_exp_str)
                    num_act = float(num_act_str)
                    if num_exp == num_act:
                        return True, "Salary exact numeric match"
                    return False, f"Salary magnitude mismatch: {num_exp} vs {num_act}"
                except ValueError:
                    pass

        # 5. Polarity / Negation mismatch check (e.g. sponsorship required vs not required)
        norm_exp = normalise_value(intended)
        norm_act = normalise_value(actual)

        neg_words = {"no", "not", "without", "none", "never"}
        tokens_exp = set(re.findall(r"\b\w+\b", norm_exp))
        tokens_act = set(re.findall(r"\b\w+\b", norm_act))
        has_neg_exp = bool(tokens_exp.intersection(neg_words))
        has_neg_act = bool(tokens_act.intersection(neg_words))
        if has_neg_exp != has_neg_act:
            return False, f"Polarity/Negation mismatch: '{norm_exp}' vs '{norm_act}'"

        # Check boolean words
        is_bool_exp = norm_exp in ("true", "false")
        is_bool_act = norm_act in ("true", "false")
        if is_bool_exp and is_bool_act:
            if norm_exp == norm_act:
                return True, "Boolean state match"
            return False, f"Boolean state mismatch: {norm_exp} vs {norm_act}"

        # 6. Exact normalized match
        if norm_exp == norm_act:
            return True, "Exact normalized match"

        # 7. Substring match only where safe (excluding pure numbers)
        if norm_exp and norm_act:
            if norm_exp.isdigit() and norm_act.isdigit():
                if norm_exp == norm_act:
                    return True, "Exact number match"
                return False, f"Number mismatch: {norm_exp} vs {norm_act}"

            if len(norm_exp) >= 4 and len(norm_act) >= 4:
                digits_in_exp = re.findall(r"\b\d+\b", norm_exp)
                digits_in_act = re.findall(r"\b\d+\b", norm_act)
                # If digits are present, allow match if one set of digits is a subset of the other and they share common numbers (e.g. 30 days vs 1 month (30 days))
                if digits_in_exp and digits_in_act:
                    if not set(digits_in_exp).intersection(set(digits_in_act)):
                        return False, f"Embedded number mismatch: {digits_in_exp} vs {digits_in_act}"

                if re.search(r"\b" + re.escape(norm_exp) + r"\b", norm_act) or re.search(r"\b" + re.escape(norm_act) + r"\b", norm_exp):
                    return True, f"Word substring match: '{norm_exp}' in '{norm_act}'"

                # Stem / prefix match for common variants (e.g. immediate vs immediately, postgresql vs postgres)
                if (norm_exp.startswith(norm_act) or norm_act.startswith(norm_exp)) and min(len(norm_exp), len(norm_act)) >= 6:
                    return True, f"Stem match: '{norm_exp}' and '{norm_act}'"

        # 8. Numeric prefix match (e.g. '3 years' vs '3')
        num_exp = re.search(r"\b\d+\b", norm_exp)
        num_act = re.search(r"\b\d+\b", norm_act)
        if num_exp and num_act:
            if num_exp.group() == num_act.group():
                return True, f"Numeric value match: {num_exp.group()}"
            else:
                return False, f"Numeric value mismatch: {num_exp.group()} vs {num_act.group()}"

        # 9. Token overlap match (e.g. 'Ahmedabad, India' vs 'Ahmedabad, Gujarat, India')
        score = token_overlap_score(norm_exp, norm_act)
        if score >= 0.5:
            return True, f"High token overlap match (score: {score:.2f})"

        # 10. LLM judge fallback for non-exact semantic equivalence
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
