"""Human names and identities for form fields (T-047).

The browser layer keys every field as ``label|type|group|index`` (``extract.build_stable_key``),
for example ``Yes|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0``. That key is an
internal identifier: the reader must only ever see the question or the field label.

* `parse_key(key)` splits a key, or returns None for a plain key such as ``why_us``.
* `question_name(key)` is the sentence-cased human question (trailing asterisks dropped).
  A radio/checkbox GROUP is asked as one question, so its name is the group text; every other
  field is named by its label.
* `question_id(key)` says which fields are the same question: all options of one radio/checkbox
  group are one question, duplicate upload inputs with the same label are one question.
* `field_display(key)` is the row label for the review page (group question plus the option).

Pure text helpers: no I/O, no secrets, never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CHOICE_TYPES = frozenset({"radio", "checkbox"})
_UPLOAD_TYPES = frozenset({"file", "upload"})
_ACRONYMS = {
    # Only tokens that are never ordinary words ("us", "it", "ai" are left alone).
    "url": "URL", "cv": "CV", "id": "ID", "uk": "UK", "eeo": "EEO", "gpa": "GPA",
    "linkedin": "LinkedIn", "github": "GitHub", "ctc": "CTC", "pdf": "PDF",
}
_STAR_TAIL = re.compile(r"[\s*\u2217\u204e]+$")
_LEADING_PREFIX = re.compile(r"^(?:q|field|input)[_-]?\d*[_-]", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedKey:
    """The four parts of ``label|type|group|index``."""

    label: str
    type: str
    group: str
    index: int

    @property
    def is_choice_group(self) -> bool:
        return self.type in _CHOICE_TYPES and bool(self.group.strip())

    @property
    def is_upload(self) -> bool:
        return self.type in _UPLOAD_TYPES


def parse_key(key: object) -> ParsedKey | None:
    """Split ``label|type|group|index``; None when `key` does not have that shape."""
    parts = str(key).split("|")
    if len(parts) < 4 or not parts[-1].strip().isdigit():
        return None
    return ParsedKey(
        label=parts[0].strip(),
        type=parts[1].strip().lower(),
        group="|".join(parts[2:-1]).strip(),
        index=int(parts[-1]),
    )


def _clean(text: str) -> str:
    return " ".join(_STAR_TAIL.sub("", " ".join(str(text).split())).split())


def human_text(text: object) -> str:
    """Drop trailing asterisks; SHOUTING text becomes sentence case; mixed case is kept."""
    value = _clean(str(text or ""))
    if not value:
        return ""
    if any(c.isalpha() for c in value) and value == value.upper():
        value = re.sub(
            r"[A-Za-z]+", lambda m: _ACRONYMS.get(m.group(0).lower(), m.group(0).lower()), value
        )
    return value[:1].upper() + value[1:]


def _from_plain_key(key: str) -> str:
    """'why_us' -> 'Why us'; 'q1_notice-period' -> 'Notice period'."""
    text = _LEADING_PREFIX.sub("", key)
    text = " ".join(re.sub(r"[_\-.]+", " ", text).split())
    return human_text(text)


def question_name(key: object) -> str:
    """The question a reader sees for a field key (or for text that already is a question)."""
    raw = str(key if key is not None else "")
    parsed = parse_key(raw)
    if parsed is not None:
        name = human_text(parsed.group if parsed.is_choice_group else parsed.label)
        return name or human_text(parsed.label) or human_text(parsed.group) or "Unnamed field"
    if raw and " " not in raw.strip() and re.search(r"[_\-.]", raw):
        return _from_plain_key(raw.strip()) or "Unnamed field"
    return human_text(raw) or "Unnamed field"


def question_id(key: object) -> tuple:
    """Fields with the same id are ONE question for counting."""
    raw = str(key)
    parsed = parse_key(raw)
    if parsed is None:
        return ("field", raw)
    if parsed.is_choice_group:
        return ("choice", parsed.type, parsed.group.casefold())
    if parsed.is_upload:
        return ("upload", parsed.label.casefold())
    return ("field", raw)


def field_display(key: object) -> str:
    """Row label for the review page: 'Question - option' for a group member, else the label."""
    parsed = parse_key(key)
    if parsed is not None and parsed.is_choice_group:
        option = human_text(parsed.label)
        question = human_text(parsed.group)
        return f"{question} \u2014 {option}" if option else question
    return question_name(key)
