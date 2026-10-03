"""GET (and HEAD) must never change state (CONTROL_PLANE_API.md 1.2, 4.1, 5.3; obligation T-2).

The route list is ENUMERATED from `app.routes`, so a GET route added later is covered
automatically. Before and after every request the whole database (every row of every table,
including sqlite_sequence) is compared.

The SSE route `GET /events/{run_id}` is enumerated like every other GET route. A never-ending
stream cannot be driven through TestClient, so none of the request variants below carries a
valid view token in the Authorization header: the route answers 401 at once and is checked
for an unchanged database like the pages. The authorised stream (connect, replay, disconnect
with an unchanged database) is covered in test_sse.py.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import TokenService

RUN = "run_alpha"
JOB = "job_1"
JOB2 = "job_2"
JOB3 = "job_3"
JOB4 = "job_4"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PREFETCH_UA = "TelegramBot (like TwitterBot)"
STREAMING_PATHS = {"/events/{run_id}"}  # never opened with a valid token here; see docstring


class Clock:
    def __init__(self) -> None:
        self.t = float(int(time.time()))

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def cp(tmp_path):
    clock = Clock()
    config = load_config(
        environ={
            "CP_SIGNING_KEY": secrets.token_urlsafe(48),
            "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
            "CP_ENV": "dev",
            "CP_BASE_URL": "http://127.0.0.1:8000",
            "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
        }
    )
    store = Store(config.db_path, clock)
    app = create_app(config, store=store, clock=clock)
    tokens = TokenService(config.signing_key, config.signing_key_previous, clock)
    with TestClient(app) as client:
        yield SimpleNamespace(
            config=config, store=store, app=app, client=client, tokens=tokens,
            auth={"Authorization": f"Bearer {config.worker_token}"},
        )
    store.close()


def fingerprint(path: Path) -> dict[str, list]:
    """Every row of every table (read-only connection), plus the schema itself."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        result = {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
        result["__schema__"] = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        return result
    finally:
        conn.close()


def _post(cp, path: str, body: dict):
    response = cp.client.post(path, json=body, headers=cp.auth)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def populate(cp) -> SimpleNamespace:
    """Rows in every table the GET routes read: runs, jobs, snapshots, events, commands,
    approvals, used_tokens, evidence, gates."""
    base = f"/api/worker/runs/{RUN}"
    _post(cp, f"{base}/heartbeat", {"run_id": RUN, "status": "RUNNING"})
    snapshot = {
        "fields": [{"field_key": "email", "intended": "a@example.com", "actual": "a@example.com",
                    "matched": True}],
        "screenshots": ["C:\\tmp\\fill_01.png"],
    }
    digest = ReviewSnapshot.model_validate(snapshot).content_hash()
    _post(cp, f"{base}/jobs/{JOB}/snapshot", {"snapshot_hash": digest, "snapshot": snapshot})
    evidence = _post(cp, f"{base}/evidence", {
        "job_id": JOB, "name": "fill_01.png", "sha256": hashlib.sha256(PNG).hexdigest(),
        "mime": "image/png", "content_b64": base64.b64encode(PNG).decode()})

    def event(event_id, job=None, **payload):
        _post(cp, f"{base}/events", {
            "event_id": event_id, "run_id": RUN, "job_id": job, "message": f"{event_id} message",
            "payload": payload, "links": {}, "created_at": "2026-10-03T10:00:00+00:00"})

    event("E01", goal="apply")
    event("E07", JOB, snapshot_hash=digest)
    event("E06", JOB2, field_key="notice_period", label="Notice")
    event("E04", JOB3, site="example.test", observed="captcha")
    # One queued approve on its own job (token used, approval row, command row) and one
    # queued pause; JOB itself stays `ready` so its page renders forms.
    other = {"fields": [{"field_key": "email", "intended": "b@example.com",
                         "actual": "b@example.com", "matched": True}]}
    other_digest = ReviewSnapshot.model_validate(other).content_hash()
    _post(cp, f"{base}/jobs/{JOB4}/snapshot", {"snapshot_hash": other_digest, "snapshot": other})
    token = cp.tokens.mint("act", RUN, job=JOB4, action="approve", snapshot_hash=other_digest)
    cp.store.consume_act(cp.tokens.verify(token, "act"), hashlib.sha256(token.encode()).hexdigest())
    cp.store.queue_run_command(RUN, "pause")
    return SimpleNamespace(digest=digest, evidence_id=evidence["evidence_id"])


def iter_routes(routes, prefix: str = ""):
    """Flatten `app.routes` to (path, methods). Recent FastAPI versions keep included routers
    as `_IncludedRouter` wrappers (with `original_router`), so walk those and Mounts too.
    An entry of an unknown kind fails the test instead of being silently skipped."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = getattr(route, "include_context", None)
            yield from iter_routes(inner.routes, prefix + (getattr(context, "prefix", "") or ""))
        elif isinstance(route, Mount):
            yield from iter_routes(route.routes, prefix + route.path)
        elif isinstance(route, Route):
            yield prefix + route.path, set(route.methods or ())
        elif isinstance(route, WebSocketRoute):
            continue
        else:
            pytest.fail(f"unknown route entry {type(route)!r}: teach iter_routes about it")


def get_routes(app) -> list[tuple[str, set[str]]]:
    return [(p, m) for p, m in iter_routes(app.routes) if m & {"GET", "HEAD"}]


def fill(path: str, data: SimpleNamespace) -> str:
    values = {"run_id": RUN, "run": RUN, "job_id": JOB, "job": JOB,
              "evidence_id": data.evidence_id}
    return re.sub(r"\{(\w+)(?::[^}]*)?\}", lambda m: values.get(m.group(1), "x"), path)


def test_fingerprint_detects_a_write(cp):
    """Guard against a vacuous test: the fingerprint must change on a real write."""
    populate(cp)
    before = fingerprint(cp.config.db_path)
    _post(cp, f"/api/worker/runs/{RUN}/heartbeat", {"run_id": RUN, "status": "PAUSED"})
    assert fingerprint(cp.config.db_path) != before


def test_route_enumeration_covers_known_pages(cp):
    paths = {path for path, _ in get_routes(cp.app)}
    assert {"/r/{run_id}", "/r/{run_id}/{job_id}", "/api/worker/runs/{run_id}/commands"} <= paths
    assert STREAMING_PATHS <= paths  # the SSE route is part of the enumeration
    posts = {path for path, methods in iter_routes(cp.app.routes) if "POST" in methods}
    assert {f"/api/{a}" for a in ("approve", "edit", "reject", "skip", "answer", "handoff_done",
                                  "pause", "resume", "cancel")} <= posts


def test_every_get_and_head_route_leaves_the_database_unchanged(cp):
    data = populate(cp)
    view_run = cp.tokens.mint("view", RUN)
    view_job = cp.tokens.mint("view", RUN, job=JOB)
    act_token = cp.tokens.mint("act", RUN, job=JOB, action="reject", snapshot_hash=data.digest)
    evd = cp.tokens.mint("evd", RUN, job=JOB, field_key=data.evidence_id)
    variants = [
        ("authorised run view", {"params": {"t": view_run}, "headers": cp.auth}),
        ("job view", {"params": {"t": view_job}, "headers": {}}),
        ("telegram prefetch", {"params": {"t": view_run}, "headers": {"User-Agent": PREFETCH_UA}}),
        ("telegram prefetch, bearer", {"params": {"t": view_job},
                                       "headers": {"User-Agent": PREFETCH_UA, **cp.auth}}),
        ("act token as t", {"params": {"t": act_token}, "headers": {}}),
        ("evd token as t", {"params": {"t": evd}, "headers": {}}),
        ("expired-looking garbage", {"params": {"t": "v1.aaaaaaaa.e30.AAAA"}, "headers": {}}),
        ("no credentials", {"params": {}, "headers": {}}),
        ("wrong bearer", {"params": {}, "headers": {"Authorization": "Bearer nope"}}),
    ]
    routes = get_routes(cp.app)
    assert routes
    covered = 0
    page_ok = 0
    for route_path, route_methods in routes:
        url = fill(route_path, data)
        for method in sorted({"GET", "HEAD"} & route_methods):
            for label, kwargs in variants:
                before = fingerprint(cp.config.db_path)
                response = cp.client.request(method, url, follow_redirects=False, **kwargs)
                assert response.status_code < 500, (method, route_path, label)
                if route_path in STREAMING_PATHS:  # must have been refused, not opened
                    assert response.status_code in (401, 405), (method, route_path, label)
                assert fingerprint(cp.config.db_path) == before, (method, route_path, label)
                covered += 1
                if route_path.startswith("/r/") and response.status_code == 200:
                    page_ok += 1
    assert covered >= 2 * len(variants)
    assert page_ok >= 6  # the pages really rendered (tokens minted) rather than just erroring


def test_post_only_routes_do_not_mutate_on_get_or_head(cp):
    data = populate(cp)
    checked = 0
    for route_path, methods in iter_routes(cp.app.routes):
        if not methods or methods & {"GET", "HEAD"}:
            continue
        url = fill(route_path, data)
        for method in ("GET", "HEAD"):
            before = fingerprint(cp.config.db_path)
            response = cp.client.request(method, url, headers=cp.auth, follow_redirects=False)
            assert response.status_code in (404, 405), (method, route_path)
            assert fingerprint(cp.config.db_path) == before, (method, route_path)
            checked += 1
    assert checked >= 2 * 9  # at least the nine human POST routes


def test_repeated_page_loads_mint_tokens_without_touching_the_database(cp):
    data = populate(cp)
    t = cp.tokens.mint("view", RUN, job=JOB)
    before = fingerprint(cp.config.db_path)
    bodies = set()
    for _ in range(10):
        response = cp.client.get(f"/r/{RUN}/{JOB}", params={"t": t},
                                 headers={"User-Agent": PREFETCH_UA})
        assert response.status_code == 200
        bodies.add(re.findall(r'name="token" value="([^"]+)"', response.text)[0])
    assert len(bodies) == 10  # a fresh act token every load ...
    assert fingerprint(cp.config.db_path) == before  # ... and still nothing written
    assert data.digest[:8] in response.text


def test_prefetched_action_link_cannot_act(cp):
    """A link previewer following every URL it sees performs only GETs; none of them acts."""
    data = populate(cp)
    before = fingerprint(cp.config.db_path)
    html = cp.client.get(f"/r/{RUN}/{JOB}", params={"t": cp.tokens.mint("view", RUN)},
                         headers={"User-Agent": PREFETCH_UA}).text
    for action in re.findall(r'action="([^"]+)"', html):
        response = cp.client.get(action, headers={"User-Agent": PREFETCH_UA})
        assert response.status_code == 405
    assert fingerprint(cp.config.db_path) == before
    assert data.digest
