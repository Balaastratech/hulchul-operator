"""Library-first deterministic answer resolution and question counting helpers."""

from __future__ import annotations

import re
from typing import Any

from src.operator.contracts import Answer, AnswerLibrary, FieldSpec, FillAction
from src.operator.policy.authority import CHALLENGE, EEO, LEGAL

# Patterns in form labels that represent select dropdown placeholders or noise
_NOISE_TAIL = re.compile(
    r"\b(?:please choose|select one|choose one|please select|choose)\b.*$",
    re.IGNORECASE,
)
_KEY_SUFFIX = re.compile(r"\|.*$")
_PUNCT_CLEAN = re.compile(r"[\*\(\)\[\]\:\,\?]")


def clean_field_label(text: str) -> str:
    """Clean field label for robust pattern matching.
    
    Strips stable key suffix (`|type|group|index`), asterisks, prompt instructions like
    'Please choose...', punctuation, and collapses whitespace.
    """
    cleaned = _KEY_SUFFIX.sub("", text)
    cleaned = _NOISE_TAIL.sub("", cleaned)
    cleaned = _PUNCT_CLEAN.sub(" ", cleaned)
    return " ".join(cleaned.split()).strip()


def match_select_option(answer: str, options: list[str]) -> str | None:
    """Match library answer to extracted select options deterministically."""
    if not options:
        return None
    ans_clean = str(answer).strip()
    ans_lower = ans_clean.casefold()

    # 1. Exact match (case-insensitive)
    for opt in options:
        if opt.strip().casefold() == ans_lower:
            return opt

    # 2. Boolean mapping (e.g. "No" -> "No", "False", etc.)
    if ans_lower in ("no", "false", "0"):
        for opt in options:
            if opt.strip().casefold() in ("no", "false"):
                return opt
    elif ans_lower in ("yes", "true", "1"):
        for opt in options:
            if opt.strip().casefold() in ("yes", "true"):
                return opt

    # 3. Substring match against valid non-placeholder options
    for opt in options:
        opt_clean = opt.strip()
        if opt_clean.casefold() in ("please choose", "select", "choose", ""):
            continue
        if ans_lower in opt_clean.casefold() or opt_clean.casefold() in ans_lower:
            return opt

    return None


def match_library_answer(
    field: FieldSpec,
    library: AnswerLibrary,
) -> tuple[Answer, str, Any] | None:
    """Find deterministic best-matching Answer entry for a field.
    
    Returns (matched_answer, action_type, resolved_value) or None if no match.
    Enforces that EEO and Legal sensitivity are never auto-filled.
    """
    # 1. Check field text against policy EEO/Legal/Challenge guards
    field_text = f"{field.label} {field.group} {field.type}"
    if any(p.search(field_text) for p in (LEGAL, EEO, CHALLENGE)):
        return None

    # Search candidates across label and group
    search_texts = []
    cleaned_label = clean_field_label(field.label)
    if cleaned_label:
        search_texts.append(cleaned_label)
    if field.group:
        cleaned_group = clean_field_label(field.group)
        if cleaned_group:
            search_texts.append(cleaned_group)
            search_texts.append(f"{cleaned_group} {cleaned_label}".strip())

    if not search_texts:
        return None

    matches: list[tuple[int, int, Answer]] = []

    for ans in library.answers:
        # Never auto-fill sensitive EEO or Legal answers
        if ans.sensitivity in ("eeo", "legal", "sensitive"):
            continue
        # Also check pattern text for EEO/Legal tokens
        if EEO.search(ans.pattern) or LEGAL.search(ans.pattern):
            continue

        # Split pattern into sub-alternatives separated by '|'
        for sub in ans.pattern.split("|"):
            sub_clean = sub.strip()
            if not sub_clean:
                continue
            for target_text in search_texts:
                try:
                    # Match with word boundaries
                    m = re.search(r"\b(?:" + sub_clean + r")\b", target_text, re.IGNORECASE)
                    if m:
                        matched_span_len = len(m.group(0))
                        sub_len = len(sub_clean)
                        matches.append((matched_span_len, sub_len, ans))
                except Exception:
                    # Fallback to literal search if regex syntax is in sub_clean
                    sub_lit = re.escape(sub_clean)
                    m = re.search(r"\b(?:" + sub_lit + r")\b", target_text, re.IGNORECASE)
                    if m:
                        matches.append((len(m.group(0)), len(sub_clean), ans))

        # Check full regex pattern if it contains special regex wildcards like '.*'
        if ".*" in ans.pattern or ".+" in ans.pattern:
            for target_text in search_texts:
                try:
                    m = re.search(r"\b(?:" + ans.pattern + r")\b", target_text, re.IGNORECASE)
                    if m:
                        matches.append((len(m.group(0)), len(ans.pattern), ans))
                except Exception:
                    pass

    if not matches:
        return None

    # Sort candidates by longest match span descending, then longest sub-pattern descending
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_answer = matches[0][2]

    # Resolve action type and value based on field type
    ftype = field.type.lower()
    raw_val = best_answer.answer

    if ftype == "select" or field.options:
        opt = match_select_option(raw_val, field.options)
        if opt is not None:
            return best_answer, "select", opt
        return None

    if ftype == "radio":
        # For a radio option, check if the answer aligns with this radio's label
        # (e.g. radio label "Yes" matches answer "Yes", or boolean True)
        opt_clean = clean_field_label(field.label).casefold()
        ans_clean = str(raw_val).strip().casefold()
        if opt_clean == ans_clean or (ans_clean in ("yes", "true") and opt_clean == "yes"):
            return best_answer, "check", True
        elif ans_clean in ("no", "false") and opt_clean == "no":
            return best_answer, "check", True
        else:
            return best_answer, "skip", None

    if ftype == "checkbox":
        # A single checkbox matched to an answer
        ans_clean = str(raw_val).strip().casefold()
        if ans_clean in ("yes", "true", "1", "checked", "on"):
            return best_answer, "check", True
        elif ans_clean in ("no", "false", "0", "unchecked", "off"):
            return best_answer, "check", False
        return None

    # Text, date, tel, url, number, etc.
    return best_answer, "fill", raw_val


def count_unanswered_questions(
    fields: list[FieldSpec],
    actions: list[FillAction],
) -> int:
    """Count unresolved questions, grouping radio/checkboxes by group."""
    actions_by_key = {a.field_key: a for a in actions}
    # Group fields into questions
    # A radio/checkbox with non-empty group forms 1 question with other group items
    groups: dict[str, list[FieldSpec]] = {}
    for f in fields:
        key = f.key
        if f.type in ("radio", "checkbox") and f.group.strip():
            g_id = f"group:{f.type}:{f.group.strip().casefold()}"
        else:
            g_id = f"field:{key}"
        groups.setdefault(g_id, []).append(f)

    unanswered = 0
    for g_id, g_fields in groups.items():
        # A question is answered if any member is filled/selected/checked
        has_answer = False
        is_asked = False
        for f in g_fields:
            act = actions_by_key.get(f.key)
            if act:
                if act.action in ("fill", "select", "upload_resume") and act.value is not None:
                    has_answer = True
                    break
                if act.action == "check" and act.value is True:
                    has_answer = True
                    break
                if act.action == "ask_user":
                    is_asked = True

        if not has_answer:
            unanswered += 1

    return unanswered
