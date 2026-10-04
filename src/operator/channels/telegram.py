"""Telegram channel (T-020; CONTROL_PLANE_API.md section 7, COMMUNICATION_MATRIX sections 1-5).

Outbound: `TelegramChannel.emit(event)` renders E01..E15, builds a review link from the
public URL that carries ONLY a VIEW token, and calls the Bot API `sendMessage`.

Link previews (S5, SPIKE_REPORT.md): Telegram fetches a plain link once when the preview is
enabled ("TelegramBot (like TwitterBot)"). So every message is sent with
`disable_web_page_preview=true`, and the link is additionally offered as an inline-keyboard
`url` button. Nothing is ever sent as a bare link with the preview enabled. There are no
`callback_data` buttons: a callback could approve without a snapshot binding (D-008).
Approve/edit/reject forms live on the review page and are POST-only there.

Inbound: `TelegramInbound` long-polls `getUpdates` (outbound only). A free-text message may
do exactly one thing: answer a pending ask_user (E06) question, as a Telegram reply to the
bot's E06 message. It can never approve, edit, reject, hand off or cancel.

The bot token lives only inside `BotApi`. It is never logged, never put in an exception
message and never in `repr`. The HTTP client loggers are pinned to WARNING in `base.py`.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from .base import (
    AsyncSleep,
    ChannelError,
    assert_only_view_tokens,
    build_review_url,
    clip,
    link_scope,
    normalise_public_url,
)
from .context import ContextResolver, Provider
from .messages import Rendered, render_event
from src.operator.contracts import Event

log = logging.getLogger("operator.channels.telegram")

API_ORIGIN = "https://api.telegram.org"
REQUEST_TIMEOUT_S = 15.0
RETRY_BACKOFF_S = (1.0, 4.0, 16.0, 60.0)
MAX_ATTEMPTS = 5
MAX_RETRY_AFTER_S = 120.0
POLL_TIMEOUT_S = 50
MAX_ANSWER_CHARS = 2000
MAX_MESSAGE_CHARS = 3800  # Telegram limit is 4096 after entity parsing; leave headroom

HELP_TEXT = (
    "I send progress and review links. Approvals and edits happen only on the review page.\n"
    "Reply to one of my questions to answer it. /status sends a fresh link to the current run."
)
NOT_A_REPLY_TEXT = (
    "I can only take replies to a question I asked. "
    "Open the review link to approve or edit."
)


class TelegramApiError(ChannelError):
    """The Bot API answered with a non-retryable error (or retries ran out)."""

    def __init__(self, code: str, status: int | None = None, description: str = "") -> None:
        super().__init__(code, description)
        self.status = status
        self.description = description


# ------------------------------------------------------------------ Bot API
class BotApi:
    """Tiny Bot API client: timeout 15 s, retry with backoff 1/4/16/60 s, 429 `retry_after`."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = REQUEST_TIMEOUT_S,
        max_attempts: int = MAX_ATTEMPTS,
        backoff: tuple[float, ...] = RETRY_BACKOFF_S,
        sleep: AsyncSleep = asyncio.sleep,
    ) -> None:
        if not token:
            raise ValueError("bot token is required")
        self._token = token
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff
        self._sleep = sleep

    def __repr__(self) -> str:  # never show the token
        return "BotApi(token=<redacted>)"

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self._client

    def _scrub(self, text: str) -> str:
        return clip(text.replace(self._token, "<token>"), 160)

    async def call(
        self,
        method: str,
        payload: Mapping[str, Any],
        *,
        retry: bool = True,
        timeout: float | None = None,
    ) -> Any:
        """POST one Bot API method; returns `result` or raises ChannelError (never the token)."""
        url = f"{API_ORIGIN}/bot{self._token}/{method}"
        attempts = self._max_attempts if retry else 1
        last_code = "telegram_error"
        for attempt in range(1, attempts + 1):
            retry_after = 0.0
            try:
                response = await self._http().post(
                    url, json=dict(payload), timeout=timeout or self._timeout
                )
            except httpx.HTTPError as exc:
                # Only the exception class is kept: its text may echo the URL (token).
                last_code = "telegram_network"
                log.warning("telegram %s attempt=%d network error=%s", method, attempt,
                            type(exc).__name__)
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                if response.status_code == 200 and body.get("ok") is True:
                    return body.get("result")
                status = response.status_code
                description = self._scrub(str(body.get("description", "")))
                if status == 429:
                    params = body.get("parameters")
                    value = params.get("retry_after") if isinstance(params, dict) else None
                    retry_after = float(value) if isinstance(value, (int, float)) else 0.0
                    last_code = "telegram_rate_limited"
                elif status >= 500:
                    last_code = "telegram_server_error"
                else:  # 4xx: retrying cannot help
                    log.warning("telegram %s rejected status=%d", method, status)
                    raise TelegramApiError(f"telegram_http_{status}", status, description)
                log.warning("telegram %s attempt=%d status=%d", method, attempt, status)
            if attempt == attempts:
                break
            delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
            await self._sleep(max(delay, min(retry_after, MAX_RETRY_AFTER_S)))
        raise TelegramApiError(last_code)


# -------------------------------------------------------- state port (SQLite later)
@dataclass(frozen=True)
class ChatGate:
    """The open question an E06 Telegram message is bound to."""

    run_id: str
    job_id: str
    field_key: str
    kind: str = "ask"


class TelegramState(Protocol):
    """Persistence and command port behind the inbound side.

    The control plane app implements it on top of `control_plane.store` (telegram_links and the
    update offset in SQLite; `submit_reply` creates the answer/skip Command with the same
    one-answer-per-gate rules as the web route). Tests use `InMemoryTelegramState`.
    """

    def remember_link(self, message_id: int, gate: ChatGate, chat_id: str | None = None) -> None:
        """Telegram message ids are only unique inside one chat: key by (chat_id, message_id)."""
        ...

    def resolve_link(self, message_id: int, chat_id: str | None = None) -> ChatGate | None: ...

    def load_offset(self) -> int | None: ...

    def save_offset(self, offset: int) -> None: ...

    def active_run_id(self) -> str | None: ...

    def submit_reply(self, gate: ChatGate, action: str, text: str | None) -> str:
        """`action` is 'answer' or 'skip' only. Returns 'queued', 'duplicate' or 'gate_closed'."""
        ...


class InMemoryTelegramState:
    """Default/test implementation. Records every submit so tests can inspect it."""

    def __init__(self, active_run: str | None = None) -> None:
        # (chat_id or None for a legacy caller, message_id) -> gate  (AUDIT-025)
        self.links: dict[tuple[str | None, int], ChatGate] = {}
        self._ambiguous: set[tuple[str | None, int]] = set()
        self.offset: int | None = None
        self.active_run = active_run
        self.open_gates: set[tuple[str, str, str]] = set()
        self.submitted: list[tuple[ChatGate, str, str | None]] = []

    def remember_link(self, message_id: int, gate: ChatGate, chat_id: str | None = None) -> None:
        key = (None if chat_id is None else str(chat_id), message_id)
        previous = self.links.get(key)
        if previous is not None and previous != gate:
            # Two different gates claim one id and we cannot tell the chats apart: fail
            # closed, so a reply can never be routed to the wrong question.
            self._ambiguous.add(key)
        self.links[key] = gate
        self.open_gates.add((gate.run_id, gate.job_id, gate.field_key))

    def resolve_link(self, message_id: int, chat_id: str | None = None) -> ChatGate | None:
        if chat_id is not None:
            for key in ((str(chat_id), message_id), (None, message_id)):
                if key in self.links:
                    return None if key in self._ambiguous else self.links[key]
            return None
        matches = {k: g for k, g in self.links.items() if k[1] == message_id}
        if len(matches) != 1 or next(iter(matches)) in self._ambiguous:
            return None
        return next(iter(matches.values()))

    def load_offset(self) -> int | None:
        return self.offset

    def save_offset(self, offset: int) -> None:
        self.offset = offset

    def active_run_id(self) -> str | None:
        return self.active_run

    def submit_reply(self, gate: ChatGate, action: str, text: str | None) -> str:
        key = (gate.run_id, gate.job_id, gate.field_key)
        if key not in self.open_gates:
            return "gate_closed"
        self.open_gates.discard(key)  # one answer per gate
        self.submitted.append((gate, action, text))
        return "queued"


# ---------------------------------------------------------------- rendering
# Message texts live in messages.py (T-042); `Rendered` and `render_event` are re-exported.


def is_public_origin(origin: str) -> bool:
    """False for localhost, loopback and private addresses: a phone cannot open those."""
    host = (urlsplit(origin).hostname or "").lower()
    if host in {"localhost", ""} or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private or address.is_link_local
                or address.is_unspecified)


def make_link(tokens: Any, public_url: str, run_id: str, job_id: str | None) -> str:
    """`https://<host>/s/<13-char code>`; falls back to a view-token URL for a token service
    without short codes (older composition)."""
    if hasattr(tokens, "short_code"):
        return f"{public_url}/s/{tokens.short_code(run_id, job_id)}"
    return build_review_url(public_url, run_id, job_id, tokens.mint("view", run_id, job=job_id))


def _delivery_key(event: Event, chat_id: str) -> str:
    """E07/E08 dedup per (run, job, snapshot_hash) (7.1); everything else per full content,
    `created_at` included, so a repeated pause/resume is still a new message while a retry of
    the same emit is not."""
    if event.event_id in ("E07", "E08"):
        raw = f"{event.event_id}|{event.run_id}|{event.job_id}|{event.payload.get('snapshot_hash')}"
    else:
        raw = event.model_dump_json()
    return hashlib.sha256(f"{chat_id}|{raw}".encode()).hexdigest()


# --------------------------------------------------------------- outbound
class TelegramChannel:
    """ChannelPort implementation: sends one Bot API message per allowlisted chat."""

    def __init__(
        self,
        api: BotApi,
        *,
        chat_ids: frozenset[str] | set[str],
        public_url: str,
        tokens: Any,
        state: TelegramState | None = None,
        context: Provider | None = None,
        resolver: ContextResolver | None = None,
    ) -> None:
        if not chat_ids:
            raise ValueError("at least one allowlisted chat id is required")
        self._api = api
        self._chat_ids = tuple(sorted(str(c) for c in chat_ids))
        self._public_url = normalise_public_url(public_url)
        self._public = is_public_origin(self._public_url)
        self._tokens = tokens  # control_plane.tokens.TokenService (`short_code`; `mint` as fallback)
        self._state = state
        self._context = resolver or ContextResolver(provider=context)
        self._sent: set[str] = set()

    def view_link(self, run_id: str, job_id: str | None) -> str:
        """A short opaque link (`/s/<code>`) that opens the review page. It carries no token:
        the control plane mints a fresh VIEW token when the link is opened (never act/run/evd)."""
        return make_link(self._tokens, self._public_url, run_id, job_id)

    async def emit(self, event: Event) -> None:
        scope = link_scope(event)
        self._context.observe(event)
        facts = self._context.facts(event)
        # A localhost/private base URL cannot be opened from a phone: send no link at all
        # rather than a misleading one (CP_BASE_URL decides the origin, never CP_LOCAL_URL).
        url = (
            self.view_link(event.run_id, event.job_id if scope == "job" else None)
            if scope and self._public
            else None
        )
        rendered = render_event(event, url, facts)
        # Fail closed on the raw event data too: clipping could hide a token's tail from the
        # check on the rendered text, but no capability other than a view token may be here.
        assert_only_view_tokens(event.message, json.dumps(event.payload, default=str),
                                rendered.html, url or "")
        delivered = 0
        last_error: ChannelError | None = None
        for chat_id in self._chat_ids:
            key = _delivery_key(event, chat_id)
            if key in self._sent:
                delivered += 1
                continue
            try:
                message_id = await self._send(chat_id, rendered, url)
            except ChannelError as exc:
                last_error = exc
                log.warning("telegram delivery failed event=%s run=%s code=%s",
                            event.event_id, event.run_id, exc.code)
                continue
            self._sent.add(key)
            delivered += 1
            self._remember(event, message_id, chat_id)
        if delivered == 0:
            raise ChannelError("telegram_delivery_failed", last_error.code if last_error else "")

    async def _send(self, chat_id: str, rendered: Rendered, url: str | None) -> int | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": rendered.html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,  # S5: a preview-enabled link gets prefetched
        }
        if url and rendered.button_label:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": rendered.button_label, "url": url}]]
            }
        try:
            result = await self._api.call("sendMessage", payload)
        except TelegramApiError as exc:
            if exc.status == 400 and "BUTTON_URL" in exc.description.upper() and "reply_markup" in payload:
                # Telegram refuses some button URLs: the link moves into the text, still with
                # the preview disabled.
                del payload["reply_markup"]
                payload["text"] += "\n" + html.escape(url or "", quote=True)
                result = await self._api.call("sendMessage", payload)
            else:
                raise
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return message_id if isinstance(message_id, int) else None

    def _remember(self, event: Event, message_id: int | None, chat_id: str) -> None:
        """Bind an E06 message to its gate so a Telegram reply can answer it (7.3)."""
        if event.event_id != "E06" or self._state is None or message_id is None or not event.job_id:
            return
        field_key = event.payload.get("field_key")
        if isinstance(field_key, str) and field_key:
            try:
                self._state.remember_link(
                    message_id, ChatGate(event.run_id, event.job_id, field_key), chat_id)
            except Exception:  # noqa: BLE001 - the message was already sent
                log.exception("could not store telegram link run=%s", event.run_id)


# ---------------------------------------------------------------- inbound
class PollConflict(ChannelError):
    """Another consumer already polls this bot token (HTTP 409); do not start polling."""


class TelegramInbound:
    """Long-poll `getUpdates`; only allowlisted private chats; answers only (7.3)."""

    def __init__(
        self,
        api: BotApi,
        *,
        chat_ids: frozenset[str] | set[str],
        state: TelegramState,
        tokens: Any,
        public_url: str,
        poll_timeout: int = POLL_TIMEOUT_S,
        sleep: AsyncSleep = asyncio.sleep,
    ) -> None:
        self._api = api
        self._chat_ids = frozenset(str(c) for c in chat_ids)
        self._state = state
        self._tokens = tokens
        self._public_url = normalise_public_url(public_url)
        self._poll_timeout = poll_timeout
        self._sleep = sleep

    async def start(self) -> None:
        """Telegram refuses getUpdates while a webhook is set."""
        await self._api.call("deleteWebhook", {"drop_pending_updates": False})

    async def poll_once(self) -> int:
        """One getUpdates round; returns the number of updates handled."""
        params: dict[str, Any] = {"timeout": self._poll_timeout, "allowed_updates": ["message"]}
        offset = await asyncio.to_thread(self._state.load_offset)
        if offset is not None:
            params["offset"] = offset
        try:
            updates = await self._api.call(
                "getUpdates", params, retry=False, timeout=self._poll_timeout + REQUEST_TIMEOUT_S
            )
        except TelegramApiError as exc:
            if exc.status == 409:
                raise PollConflict("telegram_poll_conflict") from None
            raise
        handled = 0
        for update in updates if isinstance(updates, list) else []:
            if not isinstance(update, dict):
                continue
            try:
                await self.handle_update(update)
            except Exception:  # noqa: BLE001 - one bad update must not stop the loop
                log.exception("telegram update handling failed")
            update_id = update.get("update_id")
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                await asyncio.to_thread(self._state.save_offset, update_id + 1)
            handled += 1
        return handled

    async def run_forever(self, stop: asyncio.Event) -> None:
        delay = 1.0
        try:
            await self.start()
        except ChannelError as exc:
            log.warning("telegram deleteWebhook failed code=%s", exc.code)
        while not stop.is_set():
            try:
                await self.poll_once()
                delay = 1.0
            except PollConflict:
                log.error("another consumer polls this bot; telegram inbound disabled")
                return
            except ChannelError as exc:
                log.warning("telegram poll failed code=%s", exc.code)
                await self._sleep(delay)
                delay = min(delay * 2, 60.0)

    async def handle_update(self, update: Mapping[str, Any]) -> None:
        message = update.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        if (
            not isinstance(chat, dict)
            or chat.get("type") != "private"
            or str(chat.get("id")) not in self._chat_ids
        ):
            log.info("ignored non-allowlisted update")  # no ids in the log
            return
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        chat_id, text = str(chat["id"]), text.strip()
        gate = self._reply_gate(message, chat_id)

        if text.startswith("/"):
            command = text.split()[0].split("@")[0].lower()
            if command in ("/start", "/help"):
                await self._reply(chat_id, HELP_TEXT)
            elif command == "/status":
                await self._status(chat_id)
            elif command == "/skip" and gate is not None:
                await self._submit(chat_id, gate, "skip", None)
            else:
                await self._reply(chat_id, NOT_A_REPLY_TEXT)
            return
        if gate is None:
            await self._reply(chat_id, NOT_A_REPLY_TEXT)
        elif len(text) > MAX_ANSWER_CHARS:
            await self._reply(chat_id, f"That answer is too long (limit {MAX_ANSWER_CHARS} characters).")
        else:
            await self._submit(chat_id, gate, "answer", text)

    def _reply_gate(self, message: Mapping[str, Any], chat_id: str) -> ChatGate | None:
        reply = message.get("reply_to_message")
        message_id = reply.get("message_id") if isinstance(reply, dict) else None
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return None
        gate = self._state.resolve_link(message_id, chat_id)
        return gate if gate is not None and gate.kind == "ask" else None

    async def _submit(self, chat_id: str, gate: ChatGate, action: str, text: str | None) -> None:
        assert action in ("answer", "skip")  # the only actions chat can ever produce
        outcome = await asyncio.to_thread(self._state.submit_reply, gate, action, text)
        if outcome == "queued":
            await self._reply(chat_id, "Got it, passing that to the worker.")
        elif outcome == "duplicate":
            await self._reply(chat_id, "That question was already answered.")
        else:
            await self._reply(chat_id, "That question is no longer open. " + NOT_A_REPLY_TEXT)

    async def _status(self, chat_id: str) -> None:
        run_id = await asyncio.to_thread(self._state.active_run_id)
        if not run_id:
            await self._reply(chat_id, "There is no active run.")
            return
        if not is_public_origin(self._public_url):
            await self._reply(chat_id, "No public address is set, so I cannot send a link your phone can open.")
            return
        url = make_link(self._tokens, self._public_url, run_id, None)
        await self._reply(chat_id, "Current run:", url=url)

    async def _reply(self, chat_id: str, text: str, url: str | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": html.escape(text, quote=True) + (f"\n{html.escape(url, quote=True)}" if url else ""),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if url:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Open progress page", "url": url}]]}
        try:
            assert_only_view_tokens(payload["text"])
            await self._api.call("sendMessage", payload, retry=False)
        except ChannelError as exc:
            log.warning("telegram reply failed code=%s", exc.code)


def make_telegram(config: Any, tokens: Any, state: TelegramState, *,
                  client: httpx.AsyncClient | None = None,
                  sleep: AsyncSleep = asyncio.sleep) -> tuple[TelegramChannel, TelegramInbound] | None:
    """Build both halves from a `control_plane.config.Config`; None when Telegram is off.

    Links come from `config.base_url` (CP_BASE_URL, falling back to CP_PUBLIC_URL), because
    the tunnel URL changes on every restart and must never be hard-coded.
    """
    if not config.telegram_enabled:
        return None
    api = BotApi(config.telegram_bot_token, client=client, sleep=sleep)
    channel = TelegramChannel(api, chat_ids=config.telegram_chat_ids, public_url=config.base_url,
                              tokens=tokens, state=state)
    inbound = TelegramInbound(api, chat_ids=config.telegram_chat_ids, state=state, tokens=tokens,
                              public_url=config.base_url, sleep=sleep)
    return channel, inbound
