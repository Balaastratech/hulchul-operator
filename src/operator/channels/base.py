"""Channel base: the ChannelPort protocol, errors and shared rendering helpers.

`ChannelPort` and `Event` are the frozen contracts from ``src.operator.contracts``
(``ports.py``: ``async emit(event) -> None``); this module re-exports `ChannelPort` so
``from src.operator.channels import ChannelPort`` keeps working (CONTROL_PLANE_API.md
section 7).

Nothing in this module logs a secret. Bot tokens and capability tokens never appear in
log lines, error messages or exception arguments.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlsplit

from src.operator.contracts import ChannelPort, Event

log = logging.getLogger("operator.channels")

# Any HTTP client logger would print full URLs (the Telegram bot token is part of its
# URL path), so they are pinned to WARNING for the whole process.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

CHANNEL_UNREACHABLE = "CHANNEL_UNREACHABLE"


ChannelPort = ChannelPort  # re-exported contract; `from .base import ChannelPort` keeps working


class ChannelError(Exception):
    """Delivery failed. `code` is a short machine label; the message never holds secrets."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class NotConfigured(ChannelError, NotImplementedError):
    """A channel that exists only as a stub (WhatsApp, D-007)."""


# ---------------------------------------------------------------- text helpers
def clip(value: object, limit: int = 200) -> str:
    """Single-line, length-limited text (applied BEFORE escaping)."""
    text = " ".join(str(value).split()) if value is not None else ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "\u2026"


# Events whose link goes to the job page; the rest go to the run page; E09/E15 have none.
_JOB_EVENTS = frozenset({"E03", "E04", "E05", "E06", "E07", "E08", "E10", "E11", "E12"})
_RUN_EVENTS = frozenset({"E01", "E02", "E13", "E14"})


def link_scope(event: Event) -> str | None:
    """'job', 'run' or None: which review page (CONTROL_PLANE_API.md 7.1) an event links to."""
    if event.event_id in _JOB_EVENTS:
        return "job" if event.job_id else "run"
    if event.event_id in _RUN_EVENTS:
        return "run"
    return None


def normalise_public_url(public_url: str) -> str:
    """CP_PUBLIC_URL / CP_BASE_URL: https origin (http only on loopback for development)."""
    parts = urlsplit((public_url or "").strip())
    host = parts.hostname
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not parts.netloc or not host or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("public URL must be a bare origin")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError("public URL must not contain credentials")  # AUDIT-023
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise ValueError("public URL must be https")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("public URL has an invalid port") from None
    shown_host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown_host}" + (f":{port}" if port else "")


def build_review_url(public_url: str, run_id: str, job_id: str | None, view_token: str) -> str:
    """`/r/{run}[/{job}]?t=<VIEW token>`; the query carries the view token and nothing else."""
    path = f"/r/{quote(run_id, safe='')}" + (f"/{quote(job_id, safe='')}" if job_id else "")
    return f"{normalise_public_url(public_url)}{path}?t={quote(view_token, safe='')}"


# -------------------------------------------------- "no action token leaves" guard
_TOKEN_SHAPE = re.compile(r"v1\.[0-9a-f]{8}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{20,}")


def _token_type(token: str) -> str | None:
    try:
        payload_b64 = token.split(".")[2]
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        data = json.loads(raw.decode("utf-8"))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return None
    typ = data.get("typ") if isinstance(data, dict) else None
    return typ if isinstance(typ, str) else None


def assert_only_view_tokens(*texts: str) -> None:
    """Raise `ChannelError('action_token_blocked')` if any capability other than a view token
    is present in outgoing text, a URL or button markup. Only `view` tokens may leave the
    control plane through a chat channel (COMMUNICATION_MATRIX section 1, D-008).

    The error carries no part of the offending token.
    """
    for text in texts:
        for match in _TOKEN_SHAPE.finditer(text):
            if _token_type(match.group(0)) != "view":
                raise ChannelError("action_token_blocked", "only view tokens may be sent")


# ------------------------------------------------------- failure composition
class FallbackChannel:
    """Primary channel (Telegram) with the web as fallback (COMMUNICATION_MATRIX section 5).

    * primary ok            -> `on_recovered(run_id)` (clears the unreachable flag)
    * primary fails, web ok -> no error IF someone was seen on the web recently
    * nobody reachable      -> `on_unreachable(run_id)`, log CHANNEL_UNREACHABLE, raise
      `ChannelError(CHANNEL_UNREACHABLE)` so the worker pauses at the next gate. An
      unreachable channel can only delay a gate, never open one.

    `web_subscriber_seen(run_id)` answers from the in-memory subscriber registry (7.2 rule 4);
    it must not write anything. The hooks are set/cleared only from here (the delivery path).
    """

    def __init__(
        self,
        primary: ChannelPort,
        web: ChannelPort,
        *,
        web_subscriber_seen: Callable[[str], bool] = lambda run_id: False,
        on_unreachable: Callable[[str], Any] | None = None,
        on_recovered: Callable[[str], Any] | None = None,
    ) -> None:
        self._primary = primary
        self._web = web
        self._seen = web_subscriber_seen
        self._on_unreachable = on_unreachable
        self._on_recovered = on_recovered

    async def emit(self, event: Event) -> None:
        try:
            await self._primary.emit(event)
        except ChannelError as exc:
            log.warning("primary channel failed event=%s run=%s code=%s",
                        event.event_id, event.run_id, exc.code)
            await self._fall_back(event)
            return
        if self._on_recovered is not None:
            self._on_recovered(event.run_id)

    async def _fall_back(self, event: Event) -> None:
        try:
            await self._web.emit(event)
            web_ok = True
        except ChannelError as exc:
            log.warning("web channel failed event=%s run=%s code=%s",
                        event.event_id, event.run_id, exc.code)
            web_ok = False
        if web_ok and self._seen(event.run_id):
            return
        log.error("%s event=%s run=%s", CHANNEL_UNREACHABLE, event.event_id, event.run_id)
        if self._on_unreachable is not None:
            self._on_unreachable(event.run_id)
        raise ChannelError(CHANNEL_UNREACHABLE, "no channel could reach the user")


AsyncSleep = Callable[[float], Awaitable[None]]
