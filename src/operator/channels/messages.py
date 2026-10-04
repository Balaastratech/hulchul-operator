"""Chat message texts (T-042): plain language, company and role names, no raw ids or codes.

`render_event(event, url, facts)` returns Telegram-HTML text and at most ONE URL button label.
Rules (COMMUNICATION_MATRIX section 3, D-008):

* Never show ``E07``, ``job-004`` or a run id. `plain()` rewrites or removes them.
* Every dynamic value is clipped and HTML-escaped; `event.links` from a worker is never used.
* One link per message, as a URL button (no callback buttons, so chat can never approve).
  Approving is a POST on the review page; a link only ever opens a page.
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


@dataclass(frozen=True)
class Rendered:
    """`html` is Telegram-HTML; `button_label` is the single URL button (None = no button)."""

    html: str
    button_label: str | None


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
    """'why_us' -> 'Why us'; raw question keys never reach the reader."""
    text = re.sub(r"^(?:q|field|input)[_-]?\d*[_-]", "", str(key), flags=re.IGNORECASE)
    text = " ".join(re.sub(r"[_\-.]+", " ", text).split())
    return (text[:1].upper() + text[1:]) if text else "Unnamed field"


def _join(items: list[str], limit: int = 6) -> str:
    shown = [esc(label(i), 60) for i in items[:limit]]
    return ", ".join(shown) + (f" and {len(items) - limit} more" if len(items) > limit else "")


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
    check: list[str] | None = None  # generated-text fields to read
    blank_names: list[str] | None = None  # fields left empty by the user's rules


def review_stats(payload: Mapping[str, Any]) -> ReviewStats | None:
    """Counts from the review snapshot (preferred) or from explicit `counts` (older emitters)."""
    snap = payload.get("review_snapshot")
    snap = snap if isinstance(snap, dict) else payload.get("review")
    if isinstance(snap, dict) and isinstance(snap.get("fields"), list):
        stats = ReviewStats(check=[], blank_names=[])
        counted: set[str] = set()
        for item in snap["fields"]:
            if not isinstance(item, dict) or not isinstance(item.get("field_key"), str):
                continue
            key = item["field_key"]
            blank = item.get("actual") in (None, "")
            if item.get("generated"):
                stats.check.append(key)
            if item.get("escalated"):
                stats.need_answer += 1
            elif blank and not item.get("matched") and _SOURCE_HINT.search(str(item.get("reason") or "")):
                stats.left_blank += 1
                stats.blank_names.append(key)
            elif item.get("matched"):
                stats.filled += 1
            else:
                stats.need_answer += 1
            counted.add(key)
        for key in snap.get("unanswered") or []:
            if isinstance(key, str) and key not in counted:
                stats.need_answer += 1
        generated = snap.get("generated_texts")
        for key in generated if isinstance(generated, dict) else []:
            if key not in stats.check:
                stats.check.append(key)
        return stats
    counts = payload.get("counts")
    if isinstance(counts, dict):
        def num(name: str) -> int:
            v = counts.get(name)
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        flagged = [k for k in payload.get("flagged") or [] if isinstance(k, str)]
        blank = [k for k in payload.get("left_blank") or [] if isinstance(k, str)]
        return ReviewStats(num("filled"), num("need_user"), num("skipped"), flagged, blank)
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
        if kind == "E04":
            lines = [_title("\U0001f510", "Login needed")]
            action = "log in yourself"
        else:
            lines = [_title("\U0001f9e9", "Human check needed")]
            action = "do the check yourself (I never solve CAPTCHAs)"
        if site:
            lines.append(f"Site: {esc(site, 100)}")
        if facts.role_company():
            lines.append(_role_line(facts))
        lines.append(f"Open the Chrome window on your computer, {action}, then press Done.")
    elif kind == "E06":
        lines = [_title("\u2753", "I need your answer")]
        if facts.role_company():
            lines.append(_role_line(facts))
        question = _text(p, "label") or label(p.get("field_key") or "")
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

    if url is None:
        button = None
        if kind not in ("E07", "E08") and button_needed(kind):
            lines.append(NO_LINK_LINE)
    return Rendered(_fit(lines), button)


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
