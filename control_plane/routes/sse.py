"""Live progress: `GET /events/{run}` as Server-Sent Events (CONTROL_PLANE_API.md section 4.3).

Auth: the view token travels in `Authorization: Bearer <view token>` (the page JS uses fetch()
with a streamed body). No token is accepted in the URL, so native EventSource is not supported
and tokens never reach access logs or Referer headers. A job-level view token only receives
events, snapshots and commands of its own job.

Wire format (one SSE message per blank line):
    id: <events.seq>           only on `event: event` messages, so Last-Event-ID resume works
    event: event|snapshot|command|worker|resync|bye
    data: <single-line JSON>
    : ping                     comment heartbeat so proxies keep the connection open

Design:
  * The `events`, `snapshots`, `commands` and `runs` tables are the single source of truth.
    A stream only READS them (`Store._read`): connecting registers an in-memory subscriber and
    writes nothing, so the GET-never-mutates rule (section 1.2) holds.
  * `SseHub` is an in-process pub/sub. `ChangeNotifier` (ASGI middleware) wakes the subscribers
    after every successful POST; a woken stream re-reads the tables and sends the differences.
    A short poll interval is a safety net for writers that never went through this process.
  * Order inside one wake-up: new events by seq, then snapshot changes, then command changes.
    The `worker` message and the `: ping` comment follow their own 15 s timers.
  * Last-Event-ID: events with seq > n are replayed, at most 500; beyond that (or if n is newer
    than anything stored) the server sends `event: resync` and ends the stream.
  * Cleanup: the subscriber slot is released in `_SseResponse.__call__` (also when the
    generator never started) and again in the generator's own `finally`. Both are idempotent.
  * Max 5 concurrent streams per run (429 beyond), closed with `bye` when the token expires.

Nothing here logs a token or a URL.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio
from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..store import Store
from ..tokens import ID_PATTERN, CpError

router = APIRouter()

MAX_LAST_EVENT_ID_DIGITS = 18
SUMMARY_TEXT_LIMIT = 500
SUMMARY_LIST_LIMIT = 20
SUMMARY_DEPTH_LIMIT = 3
MESSAGE_LIMIT = 1000

# Payload keys per event that may appear in `summary` (section 2.4). Never the raw
# `review_snapshot`, `links`, tokens or screenshots; E03 `excerpt` (raw hostile page text) is
# left out as well. `snapshot_hash` is reduced to `snapshot_hash_short`.
_SUMMARY_KEYS: dict[str, tuple[str, ...]] = {
    "E01": ("goal", "files"),
    "E02": ("chosen", "skipped"),
    "E03": ("rule",),
    "E04": ("site", "observed", "page_kind", "reason"),
    "E05": ("site", "observed", "page_kind", "reason"),
    "E06": ("field_key", "label", "why", "suggestions"),
    "E07": ("snapshot_hash_short", "counts", "flagged", "left_blank"),
    "E08": ("snapshot_hash_short", "counts", "flagged", "left_blank"),
    "E09": ("snapshot_hash_short", "approved_at"),
    "E10": ("evidence", "application_id"),
    "E11": ("evidence", "application_id"),
    "E12": ("blocker", "last_good_step", "retries"),
    "E13": ("state", "by", "step"),
    "E14": ("status", "jobs", "cost_inr", "elapsed_s"),
    "E15": (),
}
# The documented command statuses are queued|acked|superseded; an expired command was voided
# without being executed, which is what "superseded" tells the page.
_COMMAND_STATUS = {"queued": "queued", "acked": "acked", "superseded": "superseded",
                   "expired": "superseded"}


@dataclass(frozen=True)
class SseSettings:
    ping_interval: float = 15.0
    worker_interval: float = 15.0
    poll_interval: float = 2.0
    max_replay: int = 500
    max_streams_per_run: int = 5


# ------------------------------------------------------------------- pub/sub
class Subscription:
    """One stream's registration. `wake()` is thread-safe; `wait()` is for the owning loop."""

    def __init__(self, hub: "SseHub", run_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.hub = hub
        self.run_id = run_id
        self._loop = loop
        self._event = asyncio.Event()
        self._closed = False

    def wake(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:  # loop already closed
            pass

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=max(timeout, 0.0))
        except TimeoutError:
            pass
        self._event.clear()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.hub._remove(self)


class SseHub:
    """In-memory subscriber registry (never persisted; section 4.3 last bullet)."""

    def __init__(self, max_per_run: int = 5) -> None:
        self.max_per_run = max_per_run
        self.closing = False
        self._lock = threading.Lock()
        self._subs: dict[str, set[Subscription]] = {}

    def subscribe(self, run_id: str) -> Subscription:
        loop = asyncio.get_running_loop()
        with self._lock:
            current = self._subs.setdefault(run_id, set())
            if len(current) >= self.max_per_run:
                error = CpError("too_many_streams", 429, "too many open streams for this run")
                error.headers = {"Retry-After": "5"}  # type: ignore[attr-defined]
                raise error
            sub = Subscription(self, run_id, loop)
            current.add(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            current = self._subs.get(sub.run_id)
            if current is not None:
                current.discard(sub)
                if not current:
                    del self._subs[sub.run_id]

    def count(self, run_id: str) -> int:
        with self._lock:
            return len(self._subs.get(run_id, ()))

    def publish(self, run_id: str | None = None) -> None:
        """Wake the streams of one run (or all). Carries no data: streams re-read the tables."""
        with self._lock:
            if run_id is None:
                targets = [s for subs in self._subs.values() for s in subs]
            else:
                targets = list(self._subs.get(run_id, ()))
        for sub in targets:
            sub.wake()

    def close(self) -> None:
        """Shutdown: every stream sends `bye` and ends."""
        self.closing = True
        self.publish()


class ChangeNotifier:
    """Pure ASGI middleware: after a successful POST, wake the SSE subscribers."""

    def __init__(self, app: ASGIApp, hub: SseHub) -> None:
        self.app = app
        self.hub = hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        status = 0

        async def tracking_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        finally:
            if 200 <= status < 400:
                self.hub.publish()


# ------------------------------------------------------------------ store reads
@dataclass
class _Opening:
    global_max_seq: int
    snapshots: dict[tuple[str, str], str]
    commands: dict[str, tuple[str | None, str, str]]


@dataclass
class _Cycle:
    events: list[dict] = field(default_factory=list)
    snapshots: dict[tuple[str, str], str] = field(default_factory=dict)
    commands: dict[str, tuple[str | None, str, str]] = field(default_factory=dict)
    run: dict | None = None


def _read_states(conn: Any, run_id: str, job_id: str | None) -> tuple[dict, dict]:
    snap_sql = "SELECT job_id, snapshot_hash, status FROM snapshots WHERE run_id=?"
    cmd_sql = "SELECT command_id, job_id, action, status FROM commands WHERE run_id=?"
    args: list[Any] = [run_id]
    if job_id is not None:
        snap_sql += " AND job_id=?"
        cmd_sql += " AND job_id=?"
        args.append(job_id)
    snapshots = {
        (r["job_id"], r["snapshot_hash"]): r["status"]
        for r in conn.execute(snap_sql + " ORDER BY rowid", args).fetchall()
    }
    commands = {
        r["command_id"]: (r["job_id"], r["action"], r["status"])
        for r in conn.execute(cmd_sql + " ORDER BY seq", args).fetchall()
    }
    return snapshots, commands


def _open(store: Store, run_id: str, job_id: str | None) -> _Opening | None:
    """Existence checks plus the baseline the stream diffs against. Read-only."""
    with store._read() as conn:
        if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
            return None
        if job_id is not None and conn.execute(
            "SELECT 1 FROM jobs WHERE run_id=? AND job_id=?", (run_id, job_id)
        ).fetchone() is None:
            return None
        global_max = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()["m"]
        snapshots, commands = _read_states(conn, run_id, job_id)
    return _Opening(global_max, snapshots, commands)


def _read_cycle(store: Store, run_id: str, job_id: str | None, after: int, limit: int) -> _Cycle:
    with store._read() as conn:
        sql = "SELECT seq, event_id, job_id, body_json FROM events WHERE run_id=? AND seq>?"
        args: list[Any] = [run_id, after]
        if job_id is not None:
            sql += " AND job_id=?"
            args.append(job_id)
        rows = conn.execute(sql + " ORDER BY seq LIMIT ?", [*args, limit]).fetchall()
        run = conn.execute(
            "SELECT last_heartbeat_at, last_status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        snapshots, commands = _read_states(conn, run_id, job_id)
    return _Cycle([dict(r) for r in rows], snapshots, commands, dict(run) if run else None)


# ---------------------------------------------------------------------- framing
SEP = "\n"  # sse-starlette defaults to CRLF; one plain LF keeps the wire format of section 4.3


def _frame(event: str, data: dict, *, seq: int | None = None) -> ServerSentEvent:
    return ServerSentEvent(
        json.dumps(data, separators=(",", ":")),
        event=event,
        id=str(seq) if seq is not None else None,
        sep=SEP,
    )


def _ping_frame() -> ServerSentEvent:
    return ServerSentEvent(comment="ping", sep=SEP)


def _clip(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:SUMMARY_TEXT_LIMIT]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [_clip(v, depth + 1) for v in value[:SUMMARY_LIST_LIMIT]] if depth < SUMMARY_DEPTH_LIMIT else []
    if isinstance(value, dict):
        if depth >= SUMMARY_DEPTH_LIMIT:
            return {}
        items = list(value.items())[:SUMMARY_LIST_LIMIT]
        return {str(k)[:64]: _clip(v, depth + 1) for k, v in items}
    return None


def _summary(event_id: str, payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    source = dict(payload)
    digest = source.get("snapshot_hash")
    if isinstance(digest, str):
        source["snapshot_hash_short"] = digest[:8]
    return {k: _clip(source[k]) for k in _SUMMARY_KEYS.get(event_id, ()) if k in source}


def _event_frame(row: dict) -> ServerSentEvent:
    try:
        body = json.loads(row["body_json"])
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    message = body.get("message")
    created = body.get("created_at")
    data = {
        "seq": row["seq"],
        "event_id": row["event_id"],
        "job_id": row["job_id"],
        "message": message[:MESSAGE_LIMIT] if isinstance(message, str) else "",
        "created_at": created if isinstance(created, str) else "",
        "summary": _summary(row["event_id"], body.get("payload")),
    }
    return _frame("event", data, seq=row["seq"])


# ------------------------------------------------------------------ the stream
class _Stream:
    def __init__(
        self,
        *,
        store: Store,
        hub: SseHub,
        sub: Subscription,
        settings: SseSettings,
        now: Any,
        run_id: str,
        job_id: str | None,
        exp: int,
        opening: _Opening,
        replay_from: int | None,
    ) -> None:
        self.store, self.hub, self.sub, self.cfg = store, hub, sub, settings
        self.now = now
        self.run_id, self.job_id, self.exp = run_id, job_id, exp
        self.replay_from = replay_from
        self.last_seq = opening.global_max_seq  # live tail unless a replay point is given
        self.snapshots = opening.snapshots
        self.commands = opening.commands
        self.global_max = opening.global_max_seq

    async def _read(self, limit: int) -> _Cycle:
        return await run_in_threadpool(
            _read_cycle, self.store, self.run_id, self.job_id, self.last_seq, limit
        )

    def _emit(self, cycle: _Cycle) -> list[ServerSentEvent]:
        frames: list[ServerSentEvent] = []
        for row in cycle.events:
            frames.append(_event_frame(row))
            self.last_seq = row["seq"]
        changed = [(k, s) for k, s in cycle.snapshots.items() if self.snapshots.get(k) != s]
        for (job, digest), status in sorted(changed, key=lambda item: item[1] != "stale"):
            frames.append(_frame("snapshot", {
                "job_id": job, "snapshot_hash_short": digest[:8], "status": status}))
        self.snapshots = cycle.snapshots
        for command_id, (_, action, status) in cycle.commands.items():
            if self.commands.get(command_id, (None, None, None))[2] != status:
                frames.append(_frame("command", {
                    "command_id": command_id, "action": action,
                    "status": _COMMAND_STATUS.get(status, "superseded")}))
        self.commands = cycle.commands
        return frames

    def _worker_frame(self, run: dict | None) -> ServerSentEvent:
        age = None
        status = None
        if run is not None:
            status = run.get("last_status")
            stamp = run.get("last_heartbeat_at")
            if stamp:
                try:
                    age = max(0, int(self.store.now() - datetime.fromisoformat(stamp).timestamp()))
                except ValueError:
                    age = None
        return _frame("worker", {"last_seen_age_s": age, "status": status})

    def close(self) -> None:
        self.sub.close()

    async def frames(self) -> AsyncIterator[ServerSentEvent]:
        """The `: ping` keepalive is sent by EventSourceResponse itself (its own timer)."""
        cap = self.cfg.max_replay
        try:
            yield ServerSentEvent(comment="connected", sep=SEP)
            if self.replay_from is not None:
                if self.replay_from > self.global_max:
                    yield _frame("resync", {"reason": "unknown_last_event_id"})
                    return
                self.last_seq = self.replay_from
                cycle = await self._read(cap + 1)
                if len(cycle.events) > cap:
                    for row in cycle.events[:cap]:
                        yield _event_frame(row)
                    yield _frame("resync", {"reason": "too_many_events"})
                    return
                for frame in self._emit(cycle):
                    yield frame

            loop = asyncio.get_running_loop()
            next_worker = loop.time() + self.cfg.worker_interval
            while True:
                if self.hub.closing:
                    yield _frame("bye", {"reason": "shutdown"})
                    return
                remaining = self.exp - self.now()
                if remaining <= 0:
                    yield _frame("bye", {"reason": "token_expired"})
                    return
                now_m = loop.time()
                wait = min(next_worker, now_m + self.cfg.poll_interval) - now_m
                await self.sub.wait(min(wait, remaining + 0.05))

                cycle = await self._read(cap)
                for frame in self._emit(cycle):
                    yield frame
                while len(cycle.events) >= cap:  # drain a burst
                    cycle = await self._read(cap)
                    for frame in self._emit(cycle):
                        yield frame
                now_m = loop.time()
                if now_m >= next_worker:
                    yield self._worker_frame(cycle.run)
                    next_worker = now_m + self.cfg.worker_interval
        finally:
            self.close()


class _SseResponse(EventSourceResponse):
    """sse-starlette response (keepalive ping, disconnect watch, `Cache-Control: no-store`,
    `X-Accel-Buffering: no`) that also always releases the subscriber slot, even when the
    client vanishes before the generator ever started."""

    def __init__(self, stream: _Stream, headers: dict[str, str]) -> None:
        super().__init__(
            stream.frames(),
            headers=headers,
            ping=stream.cfg.ping_interval,
            sep=SEP,
            ping_message_factory=_ping_frame,
        )
        self._stream = stream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._stream.close()
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                with anyio.CancelScope(shield=True):
                    await aclose()


# ------------------------------------------------------------------------ route
def _parse_last_event_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    raw = raw.strip()
    if raw.isascii() and raw.isdigit() and len(raw) <= MAX_LAST_EVENT_ID_DIGITS:
        return int(raw)
    return None  # malformed: ignore it and start at the live tail


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        error = CpError("invalid_token", 401)
        error.headers = {"WWW-Authenticate": "Bearer"}  # type: ignore[attr-defined]
        raise error
    return value


@router.get("/events/{run_id}")
async def stream_events(run_id: str, request: Request) -> EventSourceResponse:
    if not ID_PATTERN.match(run_id):
        raise CpError("not_found", 404)
    state = request.app.state
    try:
        claims = state.tokens.verify(_bearer(request), "view")
    except CpError as exc:
        if exc.status == 401:
            exc.headers = {"WWW-Authenticate": "Bearer"}  # type: ignore[attr-defined]
        raise
    if claims.run != run_id:
        raise CpError("forbidden", 403)
    replay_from = _parse_last_event_id(request.headers.get("last-event-id"))

    opening = await run_in_threadpool(_open, state.store, run_id, claims.job)
    if opening is None:
        raise CpError("not_found", 404)
    sub = state.sse_hub.subscribe(run_id)  # 429 beyond the per-run limit
    stream = _Stream(
        store=state.store,
        hub=state.sse_hub,
        sub=sub,
        settings=state.sse_settings,
        now=state.tokens.now,
        run_id=run_id,
        job_id=claims.job,
        exp=claims.exp,
        opening=opening,
        replay_from=replay_from,
    )
    return _SseResponse(
        stream,
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
