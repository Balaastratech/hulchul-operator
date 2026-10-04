"""Plain-language facts for chat/web messages: role, company, "job n of N", goal, data source.

The message texts (``messages.py``) never show ids such as ``job-004`` or ``E07``. They need
names, so this module answers "which role is this event about?" from, in priority order:

1. ``payload["context"]`` put on the event by whoever emits it (the preferred, explicit route);
2. a provider callable handed to the channel (tests, or a composition that knows more);
3. direct payload keys (``title``/``role``, ``company``, ``chosen`` lists);
4. what the channel itself has seen in this run (E02 order -> "job 2 of 3", E03 count);
5. the job queue CSVs of the data folder (``LOCAL_DATA_DIR``, default ``sample_data``), read only.

Everything is best effort and never raises: a missing fact simply drops its line.
Facts are text only; no capability, token or secret ever passes through here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from src.operator.contracts import Event

log = logging.getLogger("operator.channels.context")

Provider = Callable[[str, "str | None"], "Mapping[str, Any] | None"]

_SOURCE_NAMES = {
    "drive_public": "Google Drive folder",
    "drive": "Google Drive folder",
    "local_folder": "local sample folder",
    "local": "local sample folder",
}
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "sample_data"


@dataclass
class Facts:
    """What a message may say about the event's job and run. Any field may be missing."""

    role: str | None = None
    company: str | None = None
    number: int | None = None
    total: int | None = None
    goal: str | None = None
    data_source: str | None = None
    profile_updated: str | None = None
    rules_updated: str | None = None
    blocked_count: int | None = None
    shortlist: list[dict[str, str]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # job id -> "Role at Company"

    def role_company(self) -> str | None:
        if self.role and self.company:
            return f"{self.role} \u2014 {self.company}"
        return self.role or self.company


def _str(value: Any, limit: int = 160) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = " ".join(str(value).split())
        return text[:limit] if text else None
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def friendly_time(value: Any) -> str | None:
    """'4 Oct 2026, 09:12' from an ISO string or an epoch; None if unreadable."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            moment = datetime.fromtimestamp(value).astimezone()
        elif isinstance(value, str) and value.strip():
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if moment.tzinfo is not None:
                moment = moment.astimezone()
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    return f"{moment.day} {moment:%b %Y, %H:%M}"


class PostingDirectory:
    """job id -> (title, company) from the data folder's job queue CSVs (read only, lazy)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._dir = data_dir
        self._loaded: dict[str, tuple[str, str]] | None = None

    def _folder(self) -> Path:
        if self._dir is not None:
            return self._dir
        return Path(os.environ.get("LOCAL_DATA_DIR") or _DEFAULT_DATA_DIR)

    def lookup(self, job_id: str | None) -> tuple[str, str] | None:
        if not job_id:
            return None
        if self._loaded is None:
            self._loaded = {}
            try:
                from src.operator.data.local import parse_job_queue_csv

                for name, prefix in (("job_queue.csv", ""), ("job_queue_real.csv", "public-")):
                    for posting in parse_job_queue_csv(self._folder() / name):
                        self._loaded.setdefault(prefix + posting.job_id, (posting.title, posting.company))
            except Exception:  # noqa: BLE001 - names are optional
                log.info("job directory unavailable")
        return self._loaded.get(job_id)

    def file_time(self, name: str) -> str | None:
        try:
            return friendly_time((self._folder() / name).stat().st_mtime)
        except OSError:
            return None


class ContextResolver:
    """Builds `Facts` for one event. One instance per channel; remembers what it has seen."""

    def __init__(self, provider: Provider | None = None, directory: PostingDirectory | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        self._provider = provider
        self._dir = directory or PostingDirectory()
        self._env = os.environ if env is None else env
        self._selected: dict[str, list[str]] = {}
        self._blocked: dict[str, int] = {}
        self._goal: dict[str, str] = {}

    # ------------------------------------------------------------- observing
    def observe(self, event: Event) -> None:
        """Remember run-level facts from the event stream (order of the shortlist, blocks)."""
        payload = event.payload
        if event.event_id == "E01":
            ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            goal = _str(payload.get("goal"), 300) or _str(ctx.get("goal"), 300)
            if goal:
                self._goal[event.run_id] = goal
        elif event.event_id == "E02":
            ids = self._shortlist_ids(payload)
            if ids:
                self._selected[event.run_id] = ids
        elif event.event_id == "E03":
            self._blocked[event.run_id] = self._blocked.get(event.run_id, 0) + 1

    @staticmethod
    def _shortlist_ids(payload: Mapping[str, Any]) -> list[str]:
        ids: list[str] = []
        for key in ("selected", "chosen"):
            value = payload.get(key)
            for item in value if isinstance(value, list) else []:
                job_id = item.get("job_id") if isinstance(item, dict) else item
                if isinstance(job_id, str) and job_id not in ids:
                    ids.append(job_id)
        return ids

    # -------------------------------------------------------------- resolving
    def facts(self, event: Event) -> Facts:
        facts = Facts()
        payload = event.payload
        ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        extra: Mapping[str, Any] = {}
        if self._provider is not None:
            try:
                extra = self._provider(event.run_id, event.job_id) or {}
            except Exception:  # noqa: BLE001
                log.info("context provider failed")
        for source in (ctx, extra):  # explicit context wins over the provider
            self._apply(facts, source)
        self._apply_payload(facts, payload)
        self._fill_from_memory(facts, event)
        self._fill_names(facts, event)
        return facts

    @staticmethod
    def _apply(facts: Facts, source: Mapping[str, Any]) -> None:
        for name, key in (("role", "role"), ("company", "company"), ("goal", "goal"),
                          ("data_source", "data_source"), ("profile_updated", "profile_updated"),
                          ("rules_updated", "rules_updated")):
            value = _str(source.get(key), 300 if key == "goal" else 160)
            if value and getattr(facts, name) is None:
                setattr(facts, name, value)
        for name, key in (("number", "job_number"), ("total", "job_total"), ("blocked_count", "blocked_count")):
            value = _int(source.get(key))
            if value is not None and getattr(facts, name) is None:
                setattr(facts, name, value)
        shortlist = source.get("shortlist")
        if isinstance(shortlist, list) and not facts.shortlist:
            for item in shortlist[:5]:
                if isinstance(item, dict):
                    facts.shortlist.append({k: _str(item.get(k)) or "" for k in ("role", "company", "reason")})
        names = source.get("names")
        if isinstance(names, dict):
            facts.names.update({str(k): str(v) for k, v in names.items() if isinstance(v, str)})

    @staticmethod
    def _apply_payload(facts: Facts, payload: Mapping[str, Any]) -> None:
        facts.role = facts.role or _str(payload.get("role") or payload.get("title"))
        facts.company = facts.company or _str(payload.get("company"))
        facts.goal = facts.goal or _str(payload.get("goal"), 300)

    def _fill_from_memory(self, facts: Facts, event: Event) -> None:
        facts.goal = facts.goal or self._goal.get(event.run_id)
        order = self._selected.get(event.run_id)
        if order and event.job_id in order:
            facts.number = facts.number or order.index(event.job_id) + 1
            facts.total = facts.total or len(order)
        if facts.blocked_count is None and event.run_id in self._blocked:
            facts.blocked_count = self._blocked[event.run_id]
        if facts.data_source is None:
            raw = str(self._env.get("DATA_SOURCE") or "").strip()
            facts.data_source = _SOURCE_NAMES.get(raw)
        if facts.data_source and "local" in facts.data_source:
            facts.profile_updated = facts.profile_updated or self._dir.file_time("profile.md")
            facts.rules_updated = facts.rules_updated or self._dir.file_time("rules.md")

    def _fill_names(self, facts: Facts, event: Event) -> None:
        found = self._dir.lookup(event.job_id)
        if found:
            facts.role = facts.role or found[0]
            facts.company = facts.company or found[1]
        if event.job_id and facts.role_company():
            facts.names.setdefault(event.job_id, facts.role_company() or "")
        # Shortlist: the explicit list, else the E02 payload / remembered order with names.
        if event.event_id == "E02" and not facts.shortlist:
            chosen = event.payload.get("chosen")
            ids = self._shortlist_ids(event.payload)
            reasons: dict[str, str] = {}
            titles: dict[str, tuple[str | None, str | None]] = {}
            for item in chosen if isinstance(chosen, list) else []:
                if isinstance(item, dict) and isinstance(item.get("job_id"), str):
                    reason = item.get("reasons")
                    reasons[item["job_id"]] = _str(reason[0] if isinstance(reason, list) and reason else item.get("reason")) or ""
                    titles[item["job_id"]] = (_str(item.get("title")), _str(item.get("company")))
            for job_id in ids[:5]:
                role, company = titles.get(job_id, (None, None))
                if not (role and company):
                    found = self._dir.lookup(job_id)
                    if found:
                        role, company = role or found[0], company or found[1]
                facts.shortlist.append({"role": role or "", "company": company or "", "reason": reasons.get(job_id, "")})
        for job_id in self._known_ids(event):
            found = self._dir.lookup(job_id)
            if found:
                facts.names.setdefault(job_id, f"{found[0]} \u2014 {found[1]}")

    def _known_ids(self, event: Event) -> list[str]:
        ids = list(self._selected.get(event.run_id, []))
        jobs = event.payload.get("jobs")
        for item in jobs if isinstance(jobs, list) else []:
            if isinstance(item, dict) and isinstance(item.get("job_id"), str):
                ids.append(item["job_id"])
        return ids
