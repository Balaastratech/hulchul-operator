"""Chat message texts (T-042): plain language, company and role names, no raw ids or codes.

`render_event(event, url, facts)` returns Telegram-HTML text and at most ONE URL button label.
Rules (COMMUNICATION_MATRIX section 3, D-008):

* Never show ``E07``, ``job-004`` or a run id. `plain()` rewrites or removes them.
* Every dynamic value is clipped and HTML-escaped; `event.links` from a worker is never used.
* One review page per message, as URL buttons only (no callback buttons, so chat can never
  approve). E07/E08 carry two buttons to that same page: "Review & approve" and "Edit a field"
  (`#edit`). Approving is a POST on the review page; a link only ever opens a page.
* A message without a usable public link says so instead of printing a localhost address.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.operator.contracts import Event

from .base import clip
from .context import Facts, friendly_time
from .questions import parse_key, question_id, question_name

MAX_MESSAGE_CHARS = 3800  # Telegram allows 4096 after entity parsing; keep headroom

REVIEW_LABEL = "Review & approve"
APPROVAL_LINE = "The link works 24 h; your approval is valid 30 min after you press Approve."
NO_LINK_LINE = "The review page is not reachable from your phone yet (no public address is set)."

_EVENT_CODE = re.compile(r"\bE(?:0[1-9]|1[0-5])\b")
_JOB_ID = re.compile(r"\b(?:public-)?job[-_ ]?\d[\w-]*\b", re.IGNORECASE)
_STATUS_WORDS = {
    "SUBMITTED_VERIFIED": "submitted and verified",
    "SUBMITTED_UNVERIFIED": "submitted, not confirmed",
    "SUBMITTED": "submitted",
    "REJECTED_BY_USER": "you rejected it",
    "APPROVAL_EXPIRED": "approval ran out before sending",
    "NEEDS_HUMAN": "waiting for you",
    "READY_FOR_REVIEW": "waiting for your review",
    "QUARANTINED": "blocked: hidden instructions",
    "SKIPPED_DUPLICATE": "skipped: already applied",
    "NOT_SUPPORTED": "skipped: not supported",
    "FAILED": "could not be completed",
    "CANCELLED": "cancelled",
    "QUEUED": "not started",
}
_RUN_WORDS = {
    "COMPLETED": "Finished",
    "PARTIAL": "Partly done",
    "BLOCKED": "Blocked",
    "CANCELLED": "Cancelled",
}
_SOURCE_HINT = re.compile(r"(skip|left blank|by rule|blank)", re.IGNORECASE)


EDIT_LABEL = "Edit a field"
EDIT_ANCHOR = "edit"  # id of the read-back section on the review page (job.html)
MAX_NAMED_QUESTIONS = 5

# Why a hand-off to the browser was needed. Only "login" may tell the reader to log in.
_REASON_PATTERNS = (
    ("human_check", re.compile(r"captcha|recaptcha|hcaptcha|turnstile|human check|robot|not a bot|bot check|verify you", re.I)),
    ("login", re.compile(r"log[\s-]?in|sign[\s-]?in|password|credentials|account wall|login", re.I)),
    ("legal", re.compile(r"legal|consent|terms|privacy|agree|authori[sz]ation|eligib|sponsor|declaration|confirm", re.I)),
)
_REASON_TEXT = {
    "login": ("Reason: you need to log in.", "log in yourself"),
    "legal": ("Reason: a legal confirmation only you can give.", "complete the confirmation yourself"),
    "human_check": ("Reason: a human check (I never solve CAPTCHAs).", "do the check yourself"),
    "other": ("Reason: the page needs a step only you can do.", "finish that step yourself"),
}


@dataclass(frozen=True)
class Rendered:
    """`html` is Telegram-HTML; `button_label` is the main URL button (None = no button).

    `extra_buttons` are (label, fragment) pairs: more URL buttons to the SAME page, each opening
    it at `#fragment` (T-047: "Edit a field" next to "Review & approve"). Still URL buttons only.
    """

    html: str
    button_label: str | None
    extra_buttons: tuple[tuple[str, str], ...] = ()


# ------------------------------------------------------------------ text helpers
def esc(value: object, limit: int = 200) -> str:
    return html.escape(clip(value, limit), quote=True)


def plain(text: object, event: Event, facts: Facts, limit: int = 240) -> str:
    """Remove ids and event codes from free text; the result is NOT yet escaped."""
    value = clip(text, 2000)
    replacements = dict(facts.names)
    if event.job_id:
        replacements.setdefault(event.job_id, facts.role_company() or "this job")
    replacements[event.run_id] = "this run"
    for ident, name in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        if ident:
            value = value.replace(ident, name)
    value = _JOB_ID.sub("this job", value)
    value = _EVENT_CODE.sub("", value)
    return clip(value, limit)


def label(key: object) -> str:
    """'why_us' -> 'Why us'; 'Yes|radio|ARE YOU ELIGIBLE? *|0' -> 'Are you eligible?'.

    Raw field keys never reach the reader (see `questions.question_name`).
    """
    return question_name(key)


def _join(items: list[str], limit: int = 6) -> str:
    """Names (already human text) joined with commas; the rest becomes 'and N more'."""
    shown = [esc(i, 80) for i in items[:limit]]
    return ", ".join(shown) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def handoff_reason(event: Event) -> str:
    """'login', 'legal', 'human_check' or 'other' for an E04/E05 hand-off.

    An explicit payload `reason` wins, then what the page looked like (`observed`, `page_kind`),
    then the event message. E05 is a CAPTCHA by definition. Nothing is assumed to be a login.
    """
    p = event.payload
    explicit = str(p.get("reason") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if explicit in ("login", "legal", "human_check"):
        return explicit
    if explicit in ("captcha", "challenge"):
        return "human_check"
    if explicit in ("legal_confirmation", "consent"):
        return "legal"
    seen = " ".join(str(p.get(k) or "") for k in ("reason", "observed", "page_kind"))
    for reason, pattern in _REASON_PATTERNS:
        if pattern.search(seen):
            return reason
    if event.event_id == "E05":
        return "human_check"
    return "other"


def _text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _role_line(facts: Facts) -> str:
    name = facts.role_company()
    return f"Role: {esc(name, 160)}" if name else "Role: this application"


def _position_line(facts: Facts) -> list[str]:
    if facts.number and facts.total:
        return [f"Job {facts.number} of {facts.total}"]
    return []


# ---------------------------------------------------------------- review stats
@dataclass
class ReviewStats:
    filled: int = 0
    need_answer: int = 0
    left_blank: int = 0
    check: list[str] | None = None  # names of generated-text questions to read
    blank_names: list[str] | None = None  # questions left empty by the user's rules
    need_names: list[str] | None = None  # questions still waiting for the user's answer
    derived_details: list[str] | None = None  # names of fields with derived values and sources


def _member_state(item: Mapping[str, Any], choice: bool) -> str:
    """One input's state: filled | need | blank | neutral (an unselected option of a group)."""
    actual = item.get("actual")
    blank = actual in (None, "") or (choice and actual is False)
    if item.get("escalated"):
        return "need"
    if blank and not item.get("matched") and _SOURCE_HINT.search(str(item.get("reason") or "")):
        return "blank"
    if item.get("matched"):
        return "filled" if (not choice or not blank) else "neutral"
    return "need"


_RANK = {"filled": 3, "need": 2, "blank": 1, "neutral": 0}


def review_stats(payload: Mapping[str, Any]) -> ReviewStats | None:
    """Counts QUESTIONS from the review snapshot (preferred) or from explicit `counts`.

    A radio/checkbox group is one question and duplicate upload inputs count once
    (`questions.question_id`). A question is filled if any of its inputs is; otherwise it
    needs an answer if any input does; otherwise it was left blank by rule.
    """
    snap = payload.get("review_snapshot")
    snap = snap if isinstance(snap, dict) else payload.get("review")
    if isinstance(snap, dict) and isinstance(snap.get("fields"), list):
        stats = ReviewStats(check=[], blank_names=[], need_names=[], derived_details=[])
        asked: dict[tuple, list] = {}  # question id -> [name, state]
        for item in snap["fields"]:
            if not isinstance(item, dict) or not isinstance(item.get("field_key"), str):
                continue
            key = item["field_key"]
            parsed = parse_key(key)
            state = _member_state(item, bool(parsed and parsed.type in ("radio", "checkbox")))
            entry = asked.setdefault(question_id(key), [question_name(key), "neutral"])
            if _RANK[state] > _RANK[entry[1]]:
                entry[1] = state
            if item.get("generated") and entry[0] not in stats.check:
                stats.check.append(entry[0])
            if item.get("derived"):
                src = item.get("source") or "library"
                detail = f"{entry[0]} (derived from: {src})"
                if detail not in stats.derived_details:
                    stats.derived_details.append(detail)
        for key in snap.get("unanswered") or []:
            if not isinstance(key, str):
                continue
            entry = asked.setdefault(question_id(key), [question_name(key), "need"])
            if entry[1] != "filled":  # an answered group ignores its unselected sibling options
                entry[1] = "need"
        for name, state in asked.values():
            if state == "filled":
                stats.filled += 1
            elif state == "need":
                stats.need_answer += 1
                stats.need_names.append(name)
            else:
                stats.left_blank += 1
                if state == "blank":
                    stats.blank_names.append(name)
        generated = snap.get("generated_texts")
        for key in generated if isinstance(generated, dict) else []:
            name = question_name(key)
            if name not in stats.check:
                stats.check.append(name)
        return stats
    counts = payload.get("counts")
    if isinstance(counts, dict):
        def num(name: str) -> int:
            v = counts.get(name)
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        flagged = [k for k in payload.get("flagged") or [] if isinstance(k, str)]
        blank = [k for k in payload.get("left_blank") or [] if isinstance(k, str)]
        return ReviewStats(num("filled"), num("need_user"), num("skipped"),
                           [question_name(k) for k in flagged], [question_name(k) for k in blank], [])
    return None


def _review_lines(event: Event, facts: Facts, url: str | None) -> list[str]:
    stats = review_stats(event.payload)
    lines: list[str] = [_role_line(facts), *_position_line(facts)]
    if stats is not None:
        lines.append(
            f"\u2705 {_count(stats.filled, 'field')} filled \u00b7 "
            f"\u2753 {stats.need_answer} need your answer \u00b7 "
            f"\u23ed {stats.left_blank} left blank by your rules"
        )
        if stats.need_names:
            lines.append(f"Need your answer: {_join(stats.need_names, MAX_NAMED_QUESTIONS)}")
        if stats.derived_details:
            lines.append(f"Derived: {_join(stats.derived_details, MAX_NAMED_QUESTIONS)}")
        if stats.check:
            lines.append(f"\u26a0\ufe0f Please check: {_join(stats.check)}")
        if stats.blank_names:
            lines.append(f"Not filled by rule: {_join(stats.blank_names)}")
    lines.append(APPROVAL_LINE if url else NO_LINK_LINE)
    return lines


# ------------------------------------------------------------------- the events
def _title(icon: str, text: str) -> str:
    return f"{icon} <b>{html.escape(text)}</b>"


def _status_word(raw: object) -> str:
    key = str(raw or "").strip()
    return _STATUS_WORDS.get(key.upper()) or key.replace("_", " ").lower() or "unknown"


def render_event(event: Event, url: str | None, facts: Facts | None = None) -> Rendered:
    """Telegram HTML for E01..E15. `facts` supplies names; without it the text stays generic."""
    facts = facts or Facts()
    p = event.payload
    kind = event.event_id
    button = {
        "E01": "Open progress page", "E02": "View shortlist", "E03": "See details",
        "E04": "Done", "E05": "Done", "E06": "Answer", "E07": REVIEW_LABEL, "E08": REVIEW_LABEL,
        "E10": "View evidence", "E11": "Open details", "E12": "See details",
        "E13": "Resume" if p.get("state") == "paused" else "Open progress page",
        "E14": "Open summary",
    }.get(kind)
    lines: list[str]

    if kind == "E01":
        lines = [_title("\u25b6\ufe0f", "Run started")]
        lines.append(f"Goal: {esc(facts.goal, 300)}" if facts.goal else "Goal: not stated")
        if facts.data_source:
            lines.append(f"Data from: {esc(facts.data_source, 80)}")
        updated = []
        if facts.profile_updated:
            updated.append(f"profile {esc(facts.profile_updated, 40)}")
        if facts.rules_updated:
            updated.append(f"rules {esc(facts.rules_updated, 40)}")
        if updated:
            lines.append("Last updated: " + " \u00b7 ".join(updated))
    elif kind == "E02":
        items = facts.shortlist
        count = len(items) or len([c for c in p.get("selected") or [] if isinstance(c, str)])
        lines = [_title("\U0001f4cb", f"Shortlist: {_count(count, 'role')}")]
        for index, item in enumerate(items[:5], 1):
            name = " \u2014 ".join(x for x in (item.get("role"), item.get("company")) if x) or "A role"
            lines.append(f"{index}. {esc(name, 120)}")
            lines.append(f"   Why: {esc(item.get('reason') or 'fits your rules and profile', 140)}")
        if count > 5:
            lines.append(f"\u2026and {count - 5} more")
        if not items and count == 0:
            lines.append("No role matched your rules this time.")
    elif kind == "E03":
        n = facts.blocked_count or 1
        lines = [_title("\U0001f6e1\ufe0f", f"{_count(n, 'posting')} blocked")]
        lines.append("Hidden instructions, not used.")
        if facts.role_company() and n == 1:
            lines.append(_role_line(facts).replace("Role:", "Posting:", 1))
        lines.append("I carry on with the other roles.")
    elif kind in ("E04", "E05"):
        site = _text(p, "site") or facts.company
        # Neutral title: a hand-off is not always a login (legal confirmation, human check...).
        reason_line, action = _REASON_TEXT[handoff_reason(event)]
        lines = [_title("\U0001f590\ufe0f", "Action needed in the browser")]
        if site:
            lines.append(f"Site: {esc(site, 100)}")
        if facts.role_company():
            lines.append(_role_line(facts))
        lines.append(reason_line)
        lines.append(f"Open the Chrome window on your computer, {action}, then press Done.")
    elif kind == "E06":
        lines = [_title("\u2753", "I need your answer")]
        if facts.role_company():
            lines.append(_role_line(facts))
        # The asked question may arrive as a label, a question text or the raw field key
        # (`label|type|group|index`): only the human question is shown.
        question = question_name(
            _text(p, "label") or _text(p, "question") or p.get("field_key") or ""
        )
        lines.append(f"Question: {esc(plain(question, event, facts, 160), 160)}")
        why = _text(p, "why")
        if why:
            lines.append(f"Why I am asking: {esc(plain(why, event, facts), 200)}")
        suggestions = [esc(s, 60) for s in (p.get("suggestions") or [])[:4] if isinstance(s, str)]
        if suggestions:
            lines.append("Suggestions: " + "; ".join(suggestions))
        lines.append("Reply to this message with your answer, or tap Answer.")
    elif kind in ("E07", "E08"):
        if kind == "E07":
            lines = [_title("\U0001f4dd", "Ready for your review")]
        else:
            lines = [_title("\u270f\ufe0f", "Change saved, please review again")]
        lines += _review_lines(event, facts, url)
    elif kind == "E09":
        lines = [_title("\u23f3", "Submitting now")]
        lines.append(_role_line(facts))
        when = friendly_time(p.get("approved_at")) or _text(p, "approved_at")
        lines.append(f"You approved this{' at ' + esc(when, 40) if when else ''}.")
    elif kind in ("E10", "E11"):
        if kind == "E10":
            lines = [_title("\U0001f389", "Submitted and verified")]
        else:
            lines = [_title("\u26a0\ufe0f", "Submitted, but I could not confirm it")]
        lines.append(_role_line(facts))
        seen = _text(p, "evidence") or _text(p, "confirmation")
        if seen:
            prefix = "Confirmation" if kind == "E10" else "What I saw"
            lines.append(f"{prefix}: \u201c{esc(plain(seen, event, facts), 240)}\u201d")
        ref = _text(p, "application_id")
        if ref:
            lines.append(f"Reference number: {esc(ref, 80)}")
        if kind == "E11":
            lines.append("Please check the application yourself.")
    elif kind == "E12":
        lines = [_title("\u26d4", "Could not finish this application")]
        lines.append(_role_line(facts))
        reason = _text(p, "blocker") or event.message
        lines.append(f"Why: {esc(plain(reason, event, facts), 240)}")
        step = _text(p, "last_good_step")
        if step:
            lines.append(f"Last step that worked: {esc(plain(step, event, facts), 120)}")
        retries = p.get("retries")
        if isinstance(retries, int) and not isinstance(retries, bool) and retries > 0:
            lines.append(f"I tried again {_count(retries, 'time')}.")
    elif kind == "E13":
        paused = p.get("state") == "paused"
        lines = [_title("\u23f8\ufe0f" if paused else "\u25b6\ufe0f", "Paused" if paused else "Resumed")]
        step = _text(p, "step")
        if step:
            lines.append(f"Stopped after: {esc(plain(step, event, facts), 100)}")
        if paused:
            lines.append("Nothing is sent while paused. Press Resume when you are ready.")
    elif kind == "E14":
        status = str(p.get("status") or "")
        lines = [_title("\U0001f3c1", f"Run {_RUN_WORDS.get(status.upper(), 'finished').lower()}")]
        jobs = [j for j in p.get("jobs") or [] if isinstance(j, dict)]
        verified = sum(1 for j in jobs if str(j.get("status")).upper() == "SUBMITTED_VERIFIED")
        lines.append(f"\u2705 Submitted and verified: {verified}")
        for job in jobs[:10]:
            name = facts.names.get(str(job.get("job_id")), "")
            name = name or "A role"
            lines.append(f"\u2022 {esc(name, 110)}: {esc(_status_word(job.get('status')), 60)}")
        extras = []
        cost = p.get("cost_inr")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            extras.append(f"cost \u20b9{cost:g}")
        elapsed = p.get("elapsed_s")
        if isinstance(elapsed, int) and not isinstance(elapsed, bool):
            extras.append(f"time {max(1, round(elapsed / 60))} min")
        if extras:
            lines.append(" \u00b7 ".join(extras))
    else:  # E15
        age = p.get("last_heartbeat_age_s")
        heard = f"last heard from {age // 60} min ago" if isinstance(age, int) and not isinstance(age, bool) else "not heard from recently"
        lines = [_title("\U0001f50c", "Your computer is offline"), f"The worker {heard}.",
                 "Start the worker on your PC to continue."]
        button = None

    extra: tuple[tuple[str, str], ...] = ((EDIT_LABEL, EDIT_ANCHOR),) if kind in ("E07", "E08") else ()
    if url is None:
        button = None
        extra = ()
        if kind not in ("E07", "E08") and button_needed(kind):
            lines.append(NO_LINK_LINE)
    return Rendered(_fit(lines), button, extra)


def button_needed(kind: str) -> bool:
    """Events whose main purpose is a page the reader must open."""
    return kind in {"E04", "E05", "E06"}


def _fit(lines: list[str]) -> str:
    """Join lines, dropping whole lines from the end (never cutting an escape or a tag)."""
    text = "\n".join(lines)
    while len(text) > MAX_MESSAGE_CHARS and len(lines) > 2:
        lines = lines[:-2] + lines[-1:]
        text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1].rsplit("&", 1)[0] + "\u2026"
