"""Worker routes W1-W6 (CONTROL_PLANE_API.md section 2).

No network beyond loopback, no real .env: the signing key and the worker bearer are generated
per test. Part 1 drives the REAL worker client (`HttpTransport` from the Codex worktree,
read-only) against a live uvicorn thread on 127.0.0.1; it is skipped if that file is absent.
Part 2 uses plain httpx (starlette TestClient) for status codes and shapes.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import secrets
import socket
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import uvicorn
from starlette.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import TokenService, token_hash

CODEX_ROOT = Path(r"C:\Balaastra\wt-codex")
CODEX_TRANSPORT = CODEX_ROOT / "worker" / "transport.py"

RUN = "run_alpha"
OTHER_RUN = "run_beta"
JOB = "job_1"
JOB2 = "job_2"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------------------------------------------------------- fixtures
def _config(tmp_path: Path, **extra: str):
    environ = {
        "CP_SIGNING_KEY": secrets.token_urlsafe(48),
        "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
        "CP_ENV": "dev",
        "CP_BASE_URL": "http://127.0.0.1:8000",
        "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
        **extra,
    }
    return load_config(environ=environ)  # explicit environ: no .env file is read


@pytest.fixture
def cp(tmp_path):
    config = _config(tmp_path)
    store = Store(config.db_path)
    app = create_app(config, store=store)
    tokens = TokenService(config.signing_key, config.signing_key_previous)
    headers = {"Authorization": f"Bearer {config.worker_token}"}
    with TestClient(app) as client:
        yield SimpleNamespace(
            config=config, store=store, app=app, client=client, tokens=tokens, auth=headers
        )
    store.close()


@pytest.fixture
def live(cp, monkeypatch):
    """The same app on a real loopback socket, for the urllib-based worker client."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(cp.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    thread.join(10)


_TRANSPORT_CACHE: list[ModuleType] = []


def _load_real_transport() -> ModuleType:
    """Import the worker's transport.py by path without leaving codex `src.*` modules behind."""
    if not CODEX_TRANSPORT.is_file():
        pytest.skip(f"real worker transport not found at {CODEX_TRANSPORT}")
    if _TRANSPORT_CACHE:
        return _TRANSPORT_CACHE[0]

    def is_src(name: str) -> bool:
        return name == "src" or name.startswith("src.")

    saved = {name: mod for name, mod in sys.modules.items() if is_src(name)}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(CODEX_ROOT))
    try:
        spec = importlib.util.spec_from_file_location("codex_worker_transport", CODEX_TRANSPORT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ImportError as exc:  # e.g. a dependency missing in this venv
        pytest.skip(f"real worker transport cannot be imported: {exc}")
    finally:
        sys.path.remove(str(CODEX_ROOT))
        for name in [n for n in sys.modules if is_src(n)]:
            del sys.modules[name]
        sys.modules.update(saved)
    _TRANSPORT_CACHE.append(module)
    return module


def _transport(cp, base: str, run: str = RUN):
    module = _load_real_transport()
    prefix = f"{base}/api/worker/runs/{run}"
    return module.HttpTransport(
        f"{prefix}/commands",
        f"{prefix}/ack",
        f"{prefix}/heartbeat",
        authorization=f"Bearer {cp.config.worker_token}",
        timeout=10,
    )


# ----------------------------------------------------------------- helpers
def _snapshot_envelope(value: str = "a@example.com") -> dict:
    snapshot = {"fields": [{"field_key": "email", "intended": value, "actual": value,
                            "matched": True}]}
    return {
        "snapshot_hash": ReviewSnapshot.model_validate(snapshot).content_hash(),
        "snapshot": snapshot,
    }


def _post_snapshot(cp, run: str, job: str, envelope: dict | None = None):
    return cp.client.post(
        f"/api/worker/runs/{run}/jobs/{job}/snapshot",
        json=envelope or _snapshot_envelope(),
        headers=cp.auth,
    )


def _consume(cp, action: str, run: str, job: str, snapshot_hash: str, **extra):
    """What the human POST routes will do: verify a minted act token and consume it."""
    field_key = extra.pop("field_key", None)
    token = cp.tokens.mint(
        "act", run, job=job, action=action, snapshot_hash=snapshot_hash, field_key=field_key
    )
    claims = cp.tokens.verify(token, "act")
    return cp.store.consume_act(claims, token_hash(token), **extra), claims


def _event(run: str = RUN, event_id: str = "E01", job: str | None = None, **payload) -> dict:
    return {
        "event_id": event_id,
        "run_id": run,
        "job_id": job,
        "message": "hello",
        "payload": payload,
        "links": {},
        "created_at": "2026-10-03T10:00:00+00:00",
    }


def _fingerprint(path: Path) -> dict[str, list]:
    """Every row of every table, read through a read-only connection."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    finally:
        conn.close()


# ------------------------------------------- part 1: the REAL worker client
def test_real_transport_round_trip(cp, live):
    transport = _transport(cp, live)

    # First poll precedes any heartbeat: must be a clean empty answer, not a 404 (W1).
    assert transport.poll(RUN) == []
    assert cp.store.get_run(RUN) is None  # the GET wrote nothing

    transport.heartbeat(RUN, "RUNNING")  # W3
    run = cp.store.get_run(RUN)
    assert run is not None and run["last_status"] == "RUNNING"

    envelope = _snapshot_envelope()
    assert _post_snapshot(cp, RUN, JOB, envelope).status_code == 200
    assert _post_snapshot(cp, RUN, JOB2, _snapshot_envelope("b@example.com")).status_code == 200
    approve, claims = _consume(cp, "approve", RUN, JOB, envelope["snapshot_hash"])
    edit, _ = _consume(
        cp, "edit", RUN, JOB2, _snapshot_envelope("b@example.com")["snapshot_hash"],
        field_key="email", value="c@example.com",
    )
    pause = cp.store.queue_run_command(RUN, "pause")

    commands = transport.poll(RUN)  # parses with the worker's own Command model
    assert [c.command_id for c in commands] == [
        approve["command_id"], edit["command_id"], pause["command_id"]
    ]
    assert [c.action for c in commands] == ["approve", "edit", "pause"]
    assert commands[1].field_key == "email" and commands[1].value == "c@example.com"
    assert all(c.run_id == RUN for c in commands)

    # The mandatory approvals sibling was accepted by the real poll and is tz-aware.
    expiry = transport.approval_expiry(commands[0])
    assert expiry is not None and expiry.tzinfo is not None
    assert expiry == datetime.fromtimestamp(claims.exp, UTC)
    assert transport.approval_expiry(commands[1]) is None  # only approve carries expiry

    for command in commands:  # W2: the worker acks last, one by one
        transport.acknowledge(command.command_id)
    assert transport.poll(RUN) == []
    assert {cp.store.get_command(c.command_id)["status"] for c in commands} == {"acked"}
    transport.acknowledge(commands[0].command_id)  # a retried ack is still 200


def test_real_transport_never_sees_another_runs_commands(cp, live):
    assert cp.client.post(
        f"/api/worker/runs/{OTHER_RUN}/heartbeat",
        json={"run_id": OTHER_RUN, "status": "RUNNING"}, headers=cp.auth,
    ).status_code == 200
    cp.store.queue_run_command(OTHER_RUN, "pause")
    transport = _transport(cp, live, RUN)
    assert transport.poll(RUN) == []  # would raise PermissionError if a foreign command leaked
    assert [c.command_id for c in _transport(cp, live, OTHER_RUN).poll(OTHER_RUN)] != []


def test_real_transport_origin_rules_hold_for_our_urls(cp, live):
    module = _load_real_transport()
    prefix = f"{live}/api/worker/runs/{RUN}"
    # Our three URLs are same-origin, query-free, loopback: the client accepts them.
    module.HttpTransport(f"{prefix}/commands", f"{prefix}/ack", f"{prefix}/heartbeat")
    # The client's own rules, for the record: these would be refused.
    with pytest.raises(ValueError):
        module.HttpTransport("https://cp.example.com/a", "https://cp.example.com/b",
                             "https://cp.example.com/c")  # remote origin needs authorization
    with pytest.raises(ValueError):
        module.HttpTransport("http://cp.example.com/a", "http://cp.example.com/b",
                             "http://cp.example.com/c", authorization="Bearer x")  # needs HTTPS
    with pytest.raises(ValueError):
        module.HttpTransport(f"{prefix}/commands?t=1", f"{prefix}/ack", f"{prefix}/heartbeat")


def test_server_never_redirects(cp):
    for path in (f"/api/worker/runs/{RUN}/commands/", "/healthz/", "/api/worker/runs/x/commands//"):
        response = cp.client.get(path, headers=cp.auth, follow_redirects=False)
        assert response.status_code == 404, path
        assert "location" not in response.headers


# ------------------------------------------- part 2: plain httpx behaviour
ROUTES = [
    ("GET", f"/api/worker/runs/{RUN}/commands", None),
    ("POST", f"/api/worker/runs/{RUN}/ack", {"command_id": "cmd_x"}),
    ("POST", f"/api/worker/runs/{RUN}/heartbeat", {"run_id": RUN, "status": "RUNNING"}),
    ("POST", f"/api/worker/runs/{RUN}/events", _event()),
    ("POST", f"/api/worker/runs/{RUN}/jobs/{JOB}/snapshot", None),
    ("POST", f"/api/worker/runs/{RUN}/evidence", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_bearer_required_everywhere(cp, method, path, body):
    before = _fingerprint(cp.config.db_path)
    missing = cp.client.request(method, path, json=body)
    assert missing.status_code == 401 and missing.json()["error"] == "missing_bearer"
    assert missing.headers["www-authenticate"] == "Bearer"
    for header in ("Bearer wrong-token", "Basic abc", f"Bearer {cp.config.worker_token}x", "Bearer"):
        wrong = cp.client.request(method, path, json=body, headers={"Authorization": header})
        assert wrong.status_code == 401, header
        assert wrong.json()["error"] == "invalid_bearer"
    assert _fingerprint(cp.config.db_path) == before  # unauthenticated calls write nothing


def test_signing_key_is_not_a_worker_credential(cp):
    response = cp.client.get(
        f"/api/worker/runs/{RUN}/commands",
        headers={"Authorization": f"Bearer {cp.config.signing_key.decode()}"},
    )
    assert response.status_code == 401


def test_dev_switch_allows_unauthenticated_worker_only_when_configured(tmp_path):
    config = _config(tmp_path, CP_DEV_ALLOW_UNAUTH_WORKER="1", CP_WORKER_TOKEN="")
    with TestClient(create_app(config)) as client:
        assert client.get(f"/api/worker/runs/{RUN}/commands").status_code == 200


def test_unseen_run_get_is_200_empty_and_writes_nothing(cp):
    before = _fingerprint(cp.config.db_path)
    response = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth)
    assert response.status_code == 200
    assert response.json() == {"commands": [], "approvals": {}}
    assert response.headers["cache-control"] == "no-store"
    assert _fingerprint(cp.config.db_path) == before
    assert cp.store.get_run(RUN) is None


def test_get_commands_is_read_only_with_queued_commands(cp):
    envelope = _snapshot_envelope()
    _post_snapshot(cp, RUN, JOB, envelope)
    _consume(cp, "approve", RUN, JOB, envelope["snapshot_hash"])
    cp.store.queue_run_command(RUN, "pause")
    before = _fingerprint(cp.config.db_path)
    first = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()
    second = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()
    assert first == second  # at-least-once: returned again until acked
    assert _fingerprint(cp.config.db_path) == before
    approve = first["commands"][0]
    assert approve["action"] == "approve" and first["commands"][1]["action"] == "pause"
    expires = first["approvals"][approve["command_id"]]["expires_at"]
    assert expires.endswith("+00:00") and datetime.fromisoformat(expires).tzinfo is not None
    assert len(first["approvals"]) == 1  # pause has no entry


def test_get_commands_only_queued_ascending_max_50(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    ids = [cp.store.queue_run_command(RUN, a)["command_id"]
           for a in ("pause", "resume", "pause", "resume")]
    cp.client.post(f"/api/worker/runs/{RUN}/ack", json={"command_id": ids[0]}, headers=cp.auth)
    body = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()
    assert [c["command_id"] for c in body["commands"]] == ids[1:]
    many = "run_many"
    cp.store.heartbeat(many, "RUNNING")
    for n in range(60):  # alternate so coalescing does not merge them
        cp.store.queue_run_command(many, "pause" if n % 2 == 0 else "resume")
    assert len(cp.client.get(f"/api/worker/runs/{many}/commands", headers=cp.auth)
               .json()["commands"]) == 50


def test_bad_ids_are_404(cp):
    assert cp.client.get("/api/worker/runs/bad%20run/commands", headers=cp.auth).status_code == 404
    assert cp.client.get("/api/worker/runs/" + "a" * 65 + "/commands",
                         headers=cp.auth).status_code == 404


def test_ack_unknown_command_is_404_and_foreign_command_is_404(cp):
    cp.store.heartbeat(RUN, "RUNNING")
    cp.store.heartbeat(OTHER_RUN, "RUNNING")
    foreign = cp.store.queue_run_command(OTHER_RUN, "pause")["command_id"]
    unknown = cp.client.post(f"/api/worker/runs/{RUN}/ack", json={"command_id": "cmd_nope"},
                             headers=cp.auth)
    assert unknown.status_code == 404 and unknown.json()["error"] == "command_unknown"
    crossed = cp.client.post(f"/api/worker/runs/{RUN}/ack", json={"command_id": foreign},
                             headers=cp.auth)
    assert crossed.status_code == 404 and crossed.json() == unknown.json()
    assert cp.store.get_command(foreign)["status"] == "queued"  # untouched


def test_ack_validation(cp):
    url = f"/api/worker/runs/{RUN}/ack"
    assert cp.client.post(url, json={}, headers=cp.auth).status_code == 422
    assert cp.client.post(url, json={"command_id": 5}, headers=cp.auth).status_code == 422
    assert cp.client.post(url, content=b"{nope", headers=cp.auth).status_code == 400
    assert cp.client.post(url, json=["x"], headers=cp.auth).status_code == 400
    assert cp.client.post(url, content=b"", headers=cp.auth).status_code == 400


def test_ack_is_idempotent_and_terminal_commands_still_answer_200(cp):
    envelope = _snapshot_envelope()
    _post_snapshot(cp, RUN, JOB, envelope)
    approve, _ = _consume(cp, "approve", RUN, JOB, envelope["snapshot_hash"])
    url = f"/api/worker/runs/{RUN}/ack"
    cid = approve["command_id"]
    first = cp.client.post(url, json={"command_id": cid}, headers=cp.auth)
    again = cp.client.post(url, json={"command_id": cid}, headers=cp.auth)
    assert first.json() == {"ok": True, "already_acked": False, "status": "acked"}
    assert again.status_code == 200 and again.json()["already_acked"] is True

    # A second approve is superseded by a new snapshot; its late ack is still a 200.
    version_one = _snapshot_envelope("z@example.com")
    _post_snapshot(cp, RUN, JOB2, version_one)
    second, _ = _consume(cp, "approve", RUN, JOB2, version_one["snapshot_hash"])
    moved = _post_snapshot(cp, RUN, JOB2, _snapshot_envelope("y@example.com"))
    assert moved.json()["superseded_commands"] == 1
    late = cp.client.post(url, json={"command_id": second["command_id"]}, headers=cp.auth)
    assert late.status_code == 200 and late.json()["status"] == "superseded"


def test_heartbeat_contract(cp):
    url = f"/api/worker/runs/{RUN}/heartbeat"
    ok = cp.client.post(url, json={"run_id": RUN, "status": "RUNNING"}, headers=cp.auth)
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True and body["channel_unreachable"] is False
    assert datetime.fromisoformat(body["server_time"]).tzinfo is not None
    assert cp.store.get_run(RUN)["last_heartbeat_at"] is not None

    mismatch = cp.client.post(url, json={"run_id": OTHER_RUN, "status": "RUNNING"}, headers=cp.auth)
    assert mismatch.status_code == 400 and mismatch.json()["error"] == "run_mismatch"
    assert cp.store.get_run(OTHER_RUN) is None
    for bad in ({"run_id": RUN, "status": "running"}, {"run_id": RUN}, {"status": "RUNNING"},
                {"run_id": RUN, "status": "X" * 33}):
        assert cp.client.post(url, json=bad, headers=cp.auth).status_code == 422, bad


def test_size_caps_return_413(cp):
    big = {"run_id": RUN, "status": "RUNNING", "pad": "x" * 5000}
    assert cp.client.post(f"/api/worker/runs/{RUN}/heartbeat", json=big,
                          headers=cp.auth).status_code == 413
    event = _event(pad="x" * (260 * 1024))
    assert cp.client.post(f"/api/worker/runs/{RUN}/events", json=event,
                          headers=cp.auth).status_code == 413
    snapshot = {"snapshot_hash": "0" * 64, "snapshot": {"generated_texts": {"a": "x" * (1100 * 1024)}}}
    assert cp.client.post(f"/api/worker/runs/{RUN}/jobs/{JOB}/snapshot", json=snapshot,
                          headers=cp.auth).status_code == 413
    evidence = {"job_id": JOB, "name": "a.png", "sha256": "0" * 64, "mime": "image/png",
                "content_b64": "A" * (3 * 1024 * 1024 + 10)}
    assert cp.client.post(f"/api/worker/runs/{RUN}/evidence", json=evidence,
                          headers=cp.auth).status_code == 413
    assert cp.store.get_run(RUN) is None  # rejected before any write


def test_chunked_body_without_content_length_is_still_capped(cp):
    def chunks():
        for _ in range(10):
            yield b"x" * 1024

    response = cp.client.post(f"/api/worker/runs/{RUN}/heartbeat", content=chunks(),
                              headers=cp.auth)
    assert response.status_code == 413


# ------------------------------------------------------------------- W5
def test_snapshot_hash_mismatch_is_422_and_stores_nothing(cp):
    envelope = _snapshot_envelope()
    envelope["snapshot_hash"] = "f" * 64
    response = _post_snapshot(cp, RUN, JOB, envelope)
    assert response.status_code == 422 and response.json()["error"] == "hash_mismatch"
    assert cp.store.current_snapshot_hash(RUN, JOB) is None


def test_snapshot_schema_errors_are_422(cp):
    url = f"/api/worker/runs/{RUN}/jobs/{JOB}/snapshot"
    for body in (
        {"snapshot_hash": "nothex", "snapshot": {}},
        {"snapshot_hash": "0" * 64},
        {"snapshot_hash": "0" * 64, "snapshot": {"surprise": 1}},
        {"snapshot_hash": "0" * 64, "snapshot": {"fields": [{"field_key": ""}]}},
    ):
        response = cp.client.post(url, json=body, headers=cp.auth)
        assert response.status_code == 422 and response.json()["error"] == "schema_invalid", body


def test_snapshot_hash_is_computed_after_normalising_defaults(cp):
    # The worker omitted every defaulted key; the CP must not report a spurious mismatch.
    minimal = {"fields": [{"field_key": "email"}]}
    envelope = {"snapshot_hash": ReviewSnapshot.model_validate(minimal).content_hash(),
                "snapshot": minimal}
    response = _post_snapshot(cp, RUN, JOB, envelope)
    assert response.status_code == 200 and response.json()["accepted"] is True
    assert cp.store.get_run(RUN) is not None  # unknown-run upsert


def test_new_snapshot_invalidates_approval_and_supersedes_queued_approve_not_edit(cp):
    first = _snapshot_envelope("a@example.com")
    assert _post_snapshot(cp, RUN, JOB, first).json()["duplicate"] is False
    assert _post_snapshot(cp, RUN, JOB, first).json()["duplicate"] is True
    approve, _ = _consume(cp, "approve", RUN, JOB, first["snapshot_hash"])
    second = _snapshot_envelope("b@example.com")
    result = _post_snapshot(cp, RUN, JOB, second).json()
    assert result["stale_hash"] == first["snapshot_hash"]
    assert result["invalidated_approvals"] == 1 and result["superseded_commands"] == 1
    assert cp.store.get_command(approve["command_id"])["status"] == "superseded"
    # The superseded approve is never delivered again.
    body = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()
    assert body == {"commands": [], "approvals": {}}

    edit, _ = _consume(cp, "edit", RUN, JOB, second["snapshot_hash"], field_key="email",
                       value="c@example.com")
    third = _snapshot_envelope("c@example.com")
    assert _post_snapshot(cp, RUN, JOB, third).json()["superseded_commands"] == 0
    assert cp.store.get_command(edit["command_id"])["status"] == "queued"


# ------------------------------------------------------------------- W4
def test_events_lifecycle(cp):
    url = f"/api/worker/runs/{RUN}/events"
    first = cp.client.post(url, json=_event(goal="demo"), headers=cp.auth)
    assert first.status_code == 200
    body = first.json()
    assert body["accepted"] is True and body["duplicate"] is False and isinstance(body["seq"], int)
    assert body["delivery"] == {"web": "available", "telegram": "disabled"}
    again = cp.client.post(url, json=_event(goal="demo"), headers=cp.auth).json()
    assert again["duplicate"] is True and again["seq"] == body["seq"]
    assert cp.store.get_run(RUN) is not None  # unknown-run upsert


def test_events_rules(cp):
    url = f"/api/worker/runs/{RUN}/events"
    mismatch = cp.client.post(url, json=_event(run=OTHER_RUN), headers=cp.auth)
    assert mismatch.status_code == 400 and mismatch.json()["error"] == "run_mismatch"
    assert cp.store.get_run(OTHER_RUN) is None

    fake_outage = cp.client.post(url, json=_event(event_id="E15"), headers=cp.auth)
    assert fake_outage.status_code == 422 and fake_outage.json()["error"] == "event_not_allowed"

    bad_schema = _event()
    bad_schema["event_id"] = "E99"
    assert cp.client.post(url, json=bad_schema, headers=cp.auth).json()["error"] == "schema_invalid"
    extra = _event()
    extra["surprise"] = True
    assert cp.client.post(url, json=extra, headers=cp.auth).status_code == 422


def test_review_event_needs_the_snapshot_first(cp):
    url = f"/api/worker/runs/{RUN}/events"
    envelope = _snapshot_envelope()
    event = _event(event_id="E07", job=JOB, snapshot_hash=envelope["snapshot_hash"])
    before = cp.client.post(url, json=event, headers=cp.auth)
    assert before.status_code == 409 and before.json()["error"] == "snapshot_unknown"
    missing_hash = cp.client.post(url, json=_event(event_id="E07", job=JOB), headers=cp.auth)
    assert missing_hash.status_code == 422 and missing_hash.json()["error"] == "payload_invalid"

    assert _post_snapshot(cp, RUN, JOB, envelope).status_code == 200
    after = cp.client.post(url, json=event, headers=cp.auth)
    assert after.status_code == 200 and after.json()["duplicate"] is False
    assert cp.client.post(url, json=event, headers=cp.auth).json()["duplicate"] is True


# ------------------------------------------------------------------- W6
def _evidence_body(content: bytes = PNG, **override) -> dict:
    body = {
        "job_id": JOB,
        "name": "fill_01.png",
        "sha256": hashlib.sha256(content).hexdigest(),
        "mime": "image/png",
        "content_b64": base64.b64encode(content).decode("ascii"),
    }
    body.update(override)
    return body


def test_evidence_upload_is_idempotent_and_stored_on_disk(cp):
    url = f"/api/worker/runs/{RUN}/evidence"
    first = cp.client.post(url, json=_evidence_body(), headers=cp.auth)
    assert first.status_code == 200
    body = first.json()
    assert body["accepted"] is True and body["duplicate"] is False
    assert body["evidence_id"].startswith("ev_")
    assert datetime.fromisoformat(body["expires_at"]).tzinfo is not None
    stored = list((cp.config.db_path.parent / "evidence").glob("ev_*.png"))
    assert len(stored) == 1 and stored[0].read_bytes() == PNG
    again = cp.client.post(url, json=_evidence_body(), headers=cp.auth).json()
    assert again["duplicate"] is True and again["evidence_id"] == body["evidence_id"]
    assert len(list((cp.config.db_path.parent / "evidence").iterdir())) == 1
    assert cp.store.get_run(RUN) is not None  # unknown-run upsert


def test_evidence_rejections(cp):
    url = f"/api/worker/runs/{RUN}/evidence"
    cases = {
        "hash_mismatch": _evidence_body(sha256="0" * 64),
        "unsupported_media_type": _evidence_body(mime="image/svg+xml"),
        "invalid_value": _evidence_body(content_b64="!!!not base64!!!"),
    }
    for code, body in cases.items():
        response = cp.client.post(url, json=body, headers=cp.auth)
        assert response.status_code == 422 and response.json()["error"] == code, code
    not_a_png = b"<svg onload=alert(1)>"
    disguised = _evidence_body(not_a_png)
    assert cp.client.post(url, json=disguised, headers=cp.auth).json()["error"] == "invalid_value"
    for bad_name in ("../x.png", "a b.png", ""):
        assert cp.client.post(url, json=_evidence_body(name=bad_name),
                              headers=cp.auth).status_code == 422
    too_big = PNG + b"\x00" * (2 * 1024 * 1024)
    assert cp.client.post(url, json=_evidence_body(too_big),
                          headers=cp.auth).status_code == 413
    evidence_dir = cp.config.db_path.parent / "evidence"
    assert not evidence_dir.exists() or not list(evidence_dir.iterdir())


def test_responses_carry_no_store_and_json_errors(cp):
    ok = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth)
    assert ok.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in ok.headers
    wrong_method = cp.client.put(f"/api/worker/runs/{RUN}/commands", headers=cp.auth)
    assert wrong_method.status_code == 405 and wrong_method.json()["error"] == "method_not_allowed"
    assert cp.client.get("/openapi.json").status_code == 404
    assert json.loads(cp.client.get("/nope").text)["error"] == "not_found"
