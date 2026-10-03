"""SSE progress stream, `GET /events/{run}` (CONTROL_PLANE_API.md 4.3), and `python -m control_plane`.

TestClient buffers a response until it ends, so a never-ending stream is driven here through a
small ASGI harness that talks to the app directly (`Conn`): it reads SSE frames as they are
sent and can send `http.disconnect`. Everything runs against a temp SQLite file with keys
generated in the test; there is no network and no real .env.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from control_plane.__main__ import main
from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import Event, ReviewSnapshot
from control_plane.routes.sse import SseSettings
from control_plane.store import Store
from control_plane.tokens import TokenService, token_hash

RUN = "run_sse"
JOB_A = "job_a"
JOB_B = "job_b"
TIMEOUT = 5.0
SLOW = 3600.0  # "never" for timers a test does not exercise
LINK_SECRET = "https://cp.example.test/r/run_sse?t=SECRETLINKTOKEN"


class Clock:
    def __init__(self) -> None:
        self.t = float(int(time.time()))

    def __call__(self) -> float:
        return self.t


# ------------------------------------------------------------------ ASGI harness
def _parse_frame(raw: str) -> dict:
    fields: dict[str, str] = {}
    for line in raw.split("\n"):
        if line.startswith(":"):
            return {"comment": line[1:].strip()}
        key, _, value = line.partition(": ")
        fields[key] = value
    return {"event": fields.get("event"), "id": fields.get("id"),
            "data": json.loads(fields["data"]), "raw": raw}


class Conn:
    """One request/response against the ASGI app, with incremental reads."""

    def __init__(self, app, path: str, *, method: str = "GET", headers: dict | None = None,
                 body: bytes = b"", query: str = "") -> None:
        self.out: asyncio.Queue = asyncio.Queue()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.buf = b""
        self.ended = False
        self.status = 0
        self.headers: dict[str, str] = {}
        self._body_sent = False
        self._body = body
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if body:
            raw_headers.append((b"content-length", str(len(body)).encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
            "headers": raw_headers, "server": ("testserver", 80), "client": ("127.0.0.1", 5000),
        }
        self.task = asyncio.ensure_future(app(scope, self._receive, self._send))

    async def _receive(self):
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        return await self.inbox.get()

    async def _send(self, message) -> None:
        await self.out.put(message)

    async def _next(self):
        getter = asyncio.ensure_future(self.out.get())
        done, _ = await asyncio.wait({getter, self.task}, timeout=TIMEOUT,
                                     return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            return getter.result()
        getter.cancel()
        if self.task in done:
            self.task.result()  # re-raise an application error
            return None
        raise TimeoutError("no ASGI message within the timeout")

    async def start(self) -> "Conn":
        message = await self._next()
        assert message is not None and message["type"] == "http.response.start"
        self.status = message["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        return self

    async def frame(self, *, comments: bool = False) -> dict | None:
        """Next SSE frame; `: connected` and other comments are skipped unless asked for.
        None when the stream has ended."""
        while True:
            if b"\n\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n\n", 1)
                parsed = _parse_frame(raw.decode())
                if "comment" in parsed and not comments:
                    continue
                return parsed
            if self.ended:
                return None
            message = await self._next()
            if message is None:
                self.ended = True
                continue
            assert message["type"] == "http.response.body"
            self.buf += message.get("body", b"")
            self.ended = not message.get("more_body", False)

    async def frames(self, count: int, *, comments: bool = False) -> list[dict]:
        result = []
        for _ in range(count):
            frame = await self.frame(comments=comments)
            assert frame is not None, f"stream ended after {len(result)} of {count} frames"
            result.append(frame)
        return result

    async def body(self) -> bytes:
        while not self.ended:
            message = await self._next()
            if message is None:
                break
            self.buf += message.get("body", b"")
            self.ended = not message.get("more_body", False)
        return self.buf

    async def finished(self) -> None:
        await asyncio.wait_for(self.task, TIMEOUT)

    async def disconnect(self) -> None:
        await self.inbox.put({"type": "http.disconnect"})
        await self.finished()


async def fetch(app, path: str, **kwargs) -> tuple[int, dict, dict | bytes]:
    conn = await Conn(app, path, **kwargs).start()
    raw = await conn.body()
    await conn.finished()
    try:
        return conn.status, conn.headers, json.loads(raw)
    except ValueError:
        return conn.status, conn.headers, raw


# -------------------------------------------------------------------- fixtures
def build(tmp_path: Path, **settings) -> SimpleNamespace:
    clock = Clock()
    config = load_config(environ={
        "CP_SIGNING_KEY": secrets.token_urlsafe(48),
        "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
        "CP_ENV": "dev",
        "CP_BASE_URL": "http://127.0.0.1:8000",
        "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
    })
    store = Store(config.db_path, clock)
    defaults = {"poll_interval": SLOW, "ping_interval": SLOW, "worker_interval": SLOW}
    app = create_app(config, store=store, clock=clock,
                     sse_settings=SseSettings(**{**defaults, **settings}))
    tokens = TokenService(config.signing_key, None, clock)
    return SimpleNamespace(config=config, store=store, app=app, clock=clock, tokens=tokens,
                           hub=app.state.sse_hub)


@pytest.fixture
def cp(tmp_path):
    env = build(tmp_path)
    yield env
    env.store.close()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def view(cp, *, run: str = RUN, job: str | None = None) -> str:
    return cp.tokens.mint("view", run, job=job)


def ev(event_id: str, job: str | None = None, message: str | None = None, **payload) -> Event:
    return Event(event_id=event_id, run_id=RUN, job_id=job,
                 message=message or f"{event_id} message", payload=payload,
                 links={"review": LINK_SECRET},
                 created_at=datetime(2026, 10, 3, 10, 0, tzinfo=UTC))


def snapshot(intended: str) -> tuple[str, dict]:
    body = {"fields": [{"field_key": "email", "intended": intended, "actual": intended,
                        "matched": True}], "screenshots": ["C:\\tmp\\shot.png"]}
    return ReviewSnapshot.model_validate(body).content_hash(), body


def put_snapshot(cp, job: str, intended: str) -> str:
    digest, body = snapshot(intended)
    cp.store.put_snapshot(RUN, job, digest, ReviewSnapshot.model_validate(body))
    return digest


def approve(cp, job: str, digest: str) -> str:
    token = cp.tokens.mint("act", RUN, job=job, action="approve", snapshot_hash=digest)
    return cp.store.consume_act(cp.tokens.verify(token, "act"), token_hash(token))["command_id"]


def seed_events(cp, count: int = 5) -> list[int]:
    cp.store.heartbeat(RUN, "RUNNING")
    return [cp.store.insert_event(ev("E01", message=f"step {i}", goal=f"g{i}"))["seq"]
            for i in range(1, count + 1)]


def fingerprint(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        result = {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
        result["__schema__"] = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        return result
    finally:
        conn.close()


def expire(cp) -> None:
    """Move the frozen clock past every view token and wake the streams."""
    cp.clock.t += 2 * 24 * 3600
    cp.hub.publish()


# ------------------------------------------------------------------------ auth
def test_missing_or_invalid_view_token_is_rejected(cp):
    seed_events(cp, 1)
    act = cp.tokens.mint("act", RUN, job=JOB_A, action="approve", snapshot_hash="a" * 64)
    expired = view(cp)
    tampered = expired[:-1] + ("B" if expired[-1] == "A" else "A")  # last MAC char changed
    cases = {
        "no header": ({}, "", 401, "invalid_token"),
        "empty bearer": ({"Authorization": "Bearer "}, "", 401, "invalid_token"),
        "wrong scheme": ({"Authorization": f"Basic {view(cp)}"}, "", 401, "invalid_token"),
        "garbage": (auth("not-a-token"), "", 401, "invalid_token"),
        "worker-style secret": (auth(cp.config.worker_token), "", 401, "invalid_token"),
        "act token": (auth(act), "", 401, "invalid_token"),
        "tampered": (auth(tampered), "", 401, "invalid_token"),
        "token only in the URL": ({}, f"t={view(cp)}", 401, "invalid_token"),
        "token in the URL and junk header": (auth("x"), f"t={view(cp)}", 401, "invalid_token"),
        "other run": (auth(view(cp, run="run_other")), "", 403, "forbidden"),
    }

    async def scenario():
        for label, (headers, query, status, code) in cases.items():
            got, response_headers, body = await fetch(cp.app, f"/events/{RUN}",
                                                      headers=headers, query=query)
            assert (got, body["error"]) == (status, code), label
            assert "text/event-stream" not in response_headers.get("content-type", ""), label
            if status == 401:
                assert response_headers["www-authenticate"] == "Bearer", label
        got, _, body = await fetch(cp.app, "/events/run_nope", headers=auth(view(cp, run="run_nope")))
        assert (got, body["error"]) == (404, "not_found")  # valid token, unknown run
        got, _, body = await fetch(cp.app, "/events/bad id!", headers=auth(view(cp)))
        assert (got, body["error"]) == (404, "not_found")
        got, _, body = await fetch(cp.app, f"/events/{RUN}", headers=auth(view(cp, job="nojob")))
        assert (got, body["error"]) == (404, "not_found")  # job-level token, unknown job
        cp.clock.t += 2 * 24 * 3600
        got, _, body = await fetch(cp.app, f"/events/{RUN}", headers=auth(expired))
        assert (got, body["error"]) == (410, "token_expired")

    asyncio.run(scenario())
    assert cp.hub.count(RUN) == 0  # no rejected request left a subscriber behind


def test_post_to_the_stream_route_is_not_allowed(cp):
    seed_events(cp, 1)

    async def scenario():
        status, _, body = await fetch(cp.app, f"/events/{RUN}", method="POST",
                                      headers=auth(view(cp)), body=b"{}")
        assert (status, body["error"]) == (405, "method_not_allowed")

    asyncio.run(scenario())


# ---------------------------------------------------------------------- replay
def test_replay_from_last_event_id_in_order_then_bye_on_expiry(cp):
    seqs = seed_events(cp, 5)

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}",
                          headers={**auth(view(cp)), "Last-Event-ID": str(seqs[1])}).start()
        assert conn.status == 200
        assert conn.headers["content-type"].startswith("text/event-stream")
        assert conn.headers["cache-control"] == "no-store"
        assert conn.headers["x-accel-buffering"] == "no"
        frames = await conn.frames(3)
        assert [f["event"] for f in frames] == ["event"] * 3
        assert [f["id"] for f in frames] == [str(s) for s in seqs[2:]]  # resumes after seq 2
        assert [f["data"]["seq"] for f in frames] == seqs[2:]
        assert [f["data"]["message"] for f in frames] == ["step 3", "step 4", "step 5"]
        assert set(frames[0]["data"]) == {"seq", "event_id", "job_id", "message", "created_at",
                                          "summary"}
        assert frames[0]["data"]["summary"] == {"goal": "g3"}
        expire(cp)
        bye = await conn.frame()
        assert bye["event"] == "bye" and bye["data"] == {"reason": "token_expired"}
        assert bye["id"] is None
        assert await conn.frame() is None
        await conn.finished()

    asyncio.run(scenario())
    assert cp.hub.count(RUN) == 0


def test_without_last_event_id_history_is_not_replayed(cp):
    seed_events(cp, 3)

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
        first = await conn.frame(comments=True)
        assert first == {"comment": "connected"}
        cp.store.insert_event(ev("E13", message="paused", state="paused", by="user"))
        cp.hub.publish(RUN)
        frame = await conn.frame()
        assert frame["data"]["event_id"] == "E13"  # the three old events were skipped
        assert frame["data"]["summary"] == {"state": "paused", "by": "user"}
        await conn.disconnect()

    asyncio.run(scenario())


def test_malformed_last_event_id_is_ignored(cp):
    seed_events(cp, 2)

    async def scenario():
        for raw in ("abc", "-1", "1.5", "9" * 40, ""):
            conn = await Conn(cp.app, f"/events/{RUN}",
                              headers={**auth(view(cp)), "Last-Event-ID": raw}).start()
            assert conn.status == 200, raw
            cp.store.insert_event(ev("E13", message=f"m{raw}", state="resumed"))
            cp.hub.publish(RUN)
            frame = await conn.frame()
            assert frame["data"]["event_id"] == "E13", raw  # live tail, no replay
            await conn.disconnect()

    asyncio.run(scenario())


def test_more_than_the_replay_cap_ends_with_resync(tmp_path):
    cp = build(tmp_path, max_replay=3)
    try:
        seed_events(cp, 5)

        async def scenario():
            conn = await Conn(cp.app, f"/events/{RUN}",
                              headers={**auth(view(cp)), "Last-Event-ID": "0"}).start()
            frames = await conn.frames(4)
            assert [f["event"] for f in frames] == ["event", "event", "event", "resync"]
            assert frames[3]["data"] == {"reason": "too_many_events"}
            assert await conn.frame() is None
            await conn.finished()

            exact = await Conn(cp.app, f"/events/{RUN}",
                               headers={**auth(view(cp)), "Last-Event-ID": "2"}).start()
            frames = await exact.frames(3)  # exactly the cap: no resync
            assert [f["event"] for f in frames] == ["event"] * 3
            await exact.disconnect()

            future = await Conn(cp.app, f"/events/{RUN}",
                                headers={**auth(view(cp)), "Last-Event-ID": "999"}).start()
            frame = await future.frame()
            assert frame["event"] == "resync"
            assert frame["data"] == {"reason": "unknown_last_event_id"}
            await future.finished()

        asyncio.run(scenario())
        assert cp.hub.count(RUN) == 0
    finally:
        cp.store.close()


# -------------------------------------------------------------------- ordering
def test_live_order_events_then_snapshots_then_commands(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    old = put_snapshot(cp, JOB_A, "old@example.test")

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
        await conn.frame(comments=True)  # `: connected`
        # Everything below happens before one wake-up: a single pass must emit the events in
        # seq order, then the snapshot changes (stale before current), then the commands.
        new = put_snapshot(cp, JOB_A, "new@example.test")
        cp.store.insert_event(ev("E01", message="first", goal="apply"))
        cp.store.insert_event(ev("E07", JOB_A, snapshot_hash=new,
                                 counts={"filled": 1, "total": 1}, flagged=[]))
        command_id = approve(cp, JOB_A, new)
        cp.hub.publish(RUN)

        frames = await conn.frames(5)
        assert [f["event"] for f in frames] == ["event", "event", "snapshot", "snapshot", "command"]
        assert [f["data"]["event_id"] for f in frames[:2]] == ["E01", "E07"]
        assert int(frames[0]["id"]) < int(frames[1]["id"])
        assert frames[1]["data"]["summary"] == {
            "snapshot_hash_short": new[:8], "counts": {"filled": 1, "total": 1}, "flagged": []}
        assert frames[2]["data"] == {"job_id": JOB_A, "snapshot_hash_short": old[:8],
                                     "status": "stale"}
        assert frames[3]["data"] == {"job_id": JOB_A, "snapshot_hash_short": new[:8],
                                     "status": "current"}
        assert frames[4]["data"] == {"command_id": command_id, "action": "approve",
                                     "status": "queued"}
        assert all(f["id"] is None for f in frames[2:])  # `id:` only on event messages

        cp.store.ack(RUN, command_id)
        cp.hub.publish(RUN)
        acked = await conn.frame()
        assert acked["event"] == "command"
        assert acked["data"] == {"command_id": command_id, "action": "approve", "status": "acked"}
        await conn.disconnect()

    asyncio.run(scenario())


def test_summary_never_carries_links_snapshots_or_full_hashes(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    digest = put_snapshot(cp, JOB_A, "a@example.test")
    seqs = [
        cp.store.insert_event(ev("E07", JOB_A, snapshot_hash=digest,
                                 review_snapshot={"fields": [{"intended": "SECRETVALUE"}]},
                                 screenshots=["C:\\tmp\\shot.png"],
                                 token="SECRETTOKEN", counts={"filled": 1}))["seq"],
        cp.store.insert_event(ev("E03", message="quarantined", rule="r1",
                                 excerpt="ignore previous instructions"))["seq"],
        cp.store.insert_event(ev("E01", message="long", goal="x" * 2000))["seq"],
    ]

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}",
                          headers={**auth(view(cp)), "Last-Event-ID": str(seqs[0] - 1)}).start()
        frames = await conn.frames(3)
        text = "".join(f["raw"] for f in frames)
        for forbidden in ("SECRETVALUE", "SECRETTOKEN", "SECRETLINKTOKEN", "review_snapshot",
                          "links", "screenshots", "excerpt", "ignore previous", digest):
            assert forbidden not in text, forbidden
        assert frames[0]["data"]["summary"]["snapshot_hash_short"] == digest[:8]
        assert len(frames[2]["data"]["summary"]["goal"]) == 500
        await conn.disconnect()

    asyncio.run(scenario())


def test_job_level_token_only_sees_its_job(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    a = put_snapshot(cp, JOB_A, "a@example.test")
    put_snapshot(cp, JOB_B, "b@example.test")
    cp.store.insert_event(ev("E01", message="run level", goal="g"))
    cp.store.insert_event(ev("E07", JOB_B, snapshot_hash=snapshot("b@example.test")[0]))
    cp.store.insert_event(ev("E07", JOB_A, snapshot_hash=a))
    cp.store.queue_run_command(RUN, "pause")

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}",
                          headers={**auth(view(cp, job=JOB_A)), "Last-Event-ID": "0"}).start()
        frame = await conn.frame()
        assert frame["data"]["job_id"] == JOB_A and frame["data"]["event_id"] == "E07"
        cp.store.insert_event(ev("E04", JOB_B, site="s", observed="captcha"))
        cp.store.insert_event(ev("E04", JOB_A, site="s", observed="captcha"))
        cp.store.queue_run_command(RUN, "resume")
        approve(cp, JOB_B, snapshot("b@example.test")[0])
        approve(cp, JOB_A, a)
        cp.hub.publish(RUN)
        frames = await conn.frames(2)
        assert frames[0]["data"]["event_id"] == "E04" and frames[0]["data"]["job_id"] == JOB_A
        assert frames[1]["event"] == "command" and frames[1]["data"]["action"] == "approve"
        expire(cp)
        tail = await conn.frames(1)
        assert tail[0]["event"] == "bye"  # nothing of job B or the run-level commands came first
        await conn.finished()

    asyncio.run(scenario())


# ------------------------------------------------------------------- keepalive
def test_ping_comments_and_worker_status(tmp_path):
    cp = build(tmp_path, ping_interval=0.05, worker_interval=0.05, poll_interval=0.02)
    try:
        cp.store.heartbeat(RUN, "RUNNING")
        cp.clock.t += 7  # the worker was last seen 7 s ago

        async def scenario():
            conn = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
            seen = await conn.frames(6, comments=True)
            assert {"comment": "ping"} in seen
            worker = next(f for f in seen if f.get("event") == "worker")
            assert worker["data"] == {"last_seen_age_s": 7, "status": "RUNNING"}
            assert worker["id"] is None
            await conn.disconnect()

        asyncio.run(scenario())
    finally:
        cp.store.close()


# ------------------------------------------------------- wake-up by a real POST
def test_successful_worker_post_wakes_the_stream(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    body = json.dumps({"event_id": "E01", "run_id": RUN, "job_id": None, "message": "posted",
                       "payload": {"goal": "apply"}, "links": {},
                       "created_at": "2026-10-03T10:00:00+00:00"}).encode()

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
        await conn.frame(comments=True)
        status, _, _ = await fetch(
            cp.app, f"/api/worker/runs/{RUN}/events", method="POST", body=body,
            headers={"Authorization": f"Bearer {cp.config.worker_token}",
                     "Content-Type": "application/json"})
        assert status == 200
        frame = await conn.frame()  # poll interval is an hour: only the POST hook can wake it
        assert frame["data"]["message"] == "posted"
        await conn.disconnect()

    asyncio.run(scenario())


# ------------------------------------------------- no state change on connect
def test_connecting_replaying_and_disconnecting_change_no_row(cp):
    seqs = seed_events(cp, 4)
    put_snapshot(cp, JOB_A, "a@example.test")
    cp.store.queue_run_command(RUN, "pause")
    before = fingerprint(cp.config.db_path)

    async def scenario():
        for headers in ({}, {"Last-Event-ID": str(seqs[0])}, {"Last-Event-ID": "0"},
                        {"Last-Event-ID": "999"}):
            conn = await Conn(cp.app, f"/events/{RUN}",
                              headers={**auth(view(cp)), **headers}).start()
            await conn.frame(comments=True)
            await asyncio.sleep(0.05)
            if conn.task.done():  # a resync ends the stream by itself
                await conn.finished()
            else:
                await conn.disconnect()
        job = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp, job=JOB_A))).start()
        await job.frame(comments=True)
        await job.disconnect()
        for headers in ({}, auth("junk"), auth(view(cp, run="other"))):
            await fetch(cp.app, f"/events/{RUN}", headers=headers)

    asyncio.run(scenario())
    assert fingerprint(cp.config.db_path) == before


# --------------------------------------------------------------------- cleanup
def test_disconnect_releases_the_slot_and_the_limit_is_five_per_run(cp):
    seed_events(cp, 1)

    async def scenario():
        conns = [await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
                 for _ in range(5)]
        assert cp.hub.count(RUN) == 5
        status, headers, body = await fetch(cp.app, f"/events/{RUN}", headers=auth(view(cp)))
        assert (status, body["error"]) == (429, "too_many_streams")
        assert headers["retry-after"] == "5"
        assert cp.hub.count(RUN) == 5  # the refused request took no slot

        await conns[0].disconnect()
        assert cp.hub.count(RUN) == 4
        again = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
        assert again.status == 200 and cp.hub.count(RUN) == 5

        for conn in [*conns[1:], again]:
            await conn.disconnect()
        assert cp.hub.count(RUN) == 0

    asyncio.run(scenario())


def test_shutdown_sends_bye_to_open_streams(cp):
    seed_events(cp, 1)

    async def scenario():
        conn = await Conn(cp.app, f"/events/{RUN}", headers=auth(view(cp))).start()
        await conn.frame(comments=True)
        cp.hub.close()
        bye = await conn.frame()
        assert bye["event"] == "bye" and bye["data"] == {"reason": "shutdown"}
        await conn.finished()

    asyncio.run(scenario())
    assert cp.hub.count(RUN) == 0


# ------------------------------------------------------------ python -m control_plane
@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("CP_", "TELEGRAM_")) or name == "ENV_FILE":
            monkeypatch.delenv(name)
    return tmp_path / "no-such.env"  # never the real .env


@pytest.fixture
def fake_uvicorn(monkeypatch):
    calls: list[dict] = []

    def run(app, **kwargs):
        calls.append(kwargs)
        app.state.store.close()

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def _valid_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CP_SIGNING_KEY", secrets.token_urlsafe(48))
    monkeypatch.setenv("CP_WORKER_TOKEN", secrets.token_urlsafe(48))
    monkeypatch.setenv("CP_ENV", "dev")
    monkeypatch.setenv("CP_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("CP_DB_PATH", str(tmp_path / "state" / "cp.sqlite"))


def test_main_refuses_to_start_without_a_signing_key(clean_env, fake_uvicorn, monkeypatch,
                                                      tmp_path, capsys):
    monkeypatch.setenv("CP_DB_PATH", str(tmp_path / "state" / "cp.sqlite"))
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(clean_env)])
    assert exc.value.code == 2
    assert not fake_uvicorn  # nothing was bound
    assert not (tmp_path / "state").exists()  # and nothing was created on disk
    assert "CP_SIGNING_KEY" in capsys.readouterr().err


def test_main_refuses_a_short_signing_key_without_printing_it(clean_env, fake_uvicorn,
                                                               monkeypatch, tmp_path, capsys):
    _valid_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CP_SIGNING_KEY", "abc123xyz")
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(clean_env)])
    assert exc.value.code == 2
    assert not fake_uvicorn
    err = capsys.readouterr().err
    assert "CP_SIGNING_KEY" in err and "abc123xyz" not in err


def test_main_refuses_to_start_without_a_worker_token(clean_env, fake_uvicorn, monkeypatch,
                                                       tmp_path, capsys):
    _valid_env(monkeypatch, tmp_path)
    monkeypatch.delenv("CP_WORKER_TOKEN")
    with pytest.raises(SystemExit) as exc:  # config validation itself fails closed
        main(["--env-file", str(clean_env)])
    assert exc.value.code == 2
    assert not fake_uvicorn
    assert not (tmp_path / "state").exists()
    assert "CP_WORKER_TOKEN" in capsys.readouterr().err

    # even the dev-only "no worker auth" switch does not let the command line start one
    monkeypatch.setenv("CP_DEV_ALLOW_UNAUTH_WORKER", "1")
    assert main(["--env-file", str(clean_env)]) == 2
    assert not fake_uvicorn


def test_main_defaults_to_port_8790(clean_env, fake_uvicorn, monkeypatch, tmp_path):
    _valid_env(monkeypatch, tmp_path)
    assert main(["--env-file", str(clean_env)]) == 0
    assert fake_uvicorn[0]["port"] == 8790


def test_main_binds_loopback_by_default_and_warns_for_other_hosts(clean_env, fake_uvicorn,
                                                                   monkeypatch, tmp_path, capsys):
    _valid_env(monkeypatch, tmp_path)
    assert main(["--env-file", str(clean_env)]) == 0
    assert fake_uvicorn[0]["host"] == "127.0.0.1"
    assert fake_uvicorn[0]["access_log"] is False  # request lines carry ?t= tokens
    assert "warning" not in capsys.readouterr().err

    assert main(["--env-file", str(clean_env), "--host", "0.0.0.0", "--port", "8790"]) == 0
    assert fake_uvicorn[1]["host"] == "0.0.0.0" and fake_uvicorn[1]["port"] == 8790
    assert "not loopback" in capsys.readouterr().err
