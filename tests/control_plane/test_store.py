"""SQLite store tests: atomic consume, snapshot binding, commands, events (sections 3, 5.4, 8).

A temp SQLite file and a test signing key generated here; no real .env, no network.
"""
import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from control_plane.models import Command, Event, ReviewSnapshot
from control_plane.store import Store, canonical_json
from control_plane.tokens import CpError, TokenService, token_hash, utc_iso

T0 = 1_800_000_000
RUN, JOB = "R1", "J1"


class Clock:
    def __init__(self, t: int = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = Store(tmp_path / "cp.sqlite", clock=clock)
    yield s
    s.close()


@pytest.fixture
def svc(clock) -> TokenService:
    return TokenService(secrets.token_bytes(48), clock=clock)


# ------------------------------------------------------------------ helpers
def snap(value: str = "a@example.test", screenshots=()) -> ReviewSnapshot:
    return ReviewSnapshot.model_validate(
        {
            "fields": [{"field_key": "email", "intended": value, "actual": value, "matched": True}],
            "screenshots": list(screenshots),
        }
    )


def post_snapshot(store, value="a@example.test", run=RUN, job=JOB, screenshots=()):
    s = snap(value, screenshots)
    h = s.content_hash()
    return h, store.put_snapshot(run, job, h, s)


def mint_act(svc, action, h, run=RUN, job=JOB, field_key=None, ttl=None):
    if action in ("edit", "answer") and field_key is None:
        field_key = "email"
    return svc.mint("act", run, job=job, action=action, snapshot_hash=h, field_key=field_key,
                    ttl=ttl)


def consume(store, svc, token, value=None):
    claims = svc.verify(token, "act")
    svc.check_bindings(claims, action=claims.action, field_key=claims.field_key)
    return store.consume_act(claims, token_hash(token), value=value)


def err_of(fn, *args, **kwargs) -> CpError:
    with pytest.raises(CpError) as info:
        fn(*args, **kwargs)
    return info.value


def event(kind, run=RUN, job=JOB, message="m", payload=None, minute=0):
    return Event(
        event_id=kind, run_id=run, job_id=job, message=message, payload=payload or {},
        created_at=datetime(2026, 10, 3, 12, minute, tzinfo=UTC),
    )


def rows(store, sql, *args):
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def db_checksum(store) -> str:
    """Full content checksum of every table (rows + sequence counters)."""
    conn = sqlite3.connect(store.path)
    try:
        digest = hashlib.sha256()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            digest.update(table.encode())
            for row in sorted(repr(r) for r in conn.execute(f"SELECT * FROM {table}")):
                digest.update(row.encode())
        return digest.hexdigest()
    finally:
        conn.close()


def ready_job(store, value="a@example.test", run=RUN, job=JOB):
    return post_snapshot(store, value, run, job)[0]


# ------------------------------------------------------------------- schema
def test_schema_pragmas_and_tables(store):
    names = {r["name"] for r in rows(store, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "jobs", "snapshots", "events", "deliveries", "commands", "approvals",
            "used_tokens", "evidence", "telegram_links", "kv"} <= names
    conn = store._conn()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000
    assert store.ping()


# ----------------------------------------------------------- approve / T-1
def test_approve_creates_one_command_approval_and_used_token(store, svc, clock):
    h = ready_job(store)
    token = mint_act(svc, "approve", h)
    out = consume(store, svc, token)
    assert out["status"] == "queued" and out["duplicate"] is False
    assert out["command_id"].startswith("cmd_") and len(out["command_id"]) == 36

    commands, approvals = store.list_queued_commands(RUN)
    assert len(commands) == 1
    cmd = Command.model_validate(commands[0])  # worker-shaped, schema-valid
    assert (cmd.action, cmd.job_id, cmd.snapshot_hash) == ("approve", JOB, h)
    assert cmd.token_hash == token_hash(token)
    # approvals sibling: aware ISO with +00:00, equal to the token's exp
    expires = approvals[out["command_id"]]["expires_at"]
    assert expires.endswith("+00:00") and datetime.fromisoformat(expires).tzinfo is not None
    assert expires == utc_iso(clock.t + 1800)

    assert len(rows(store, "SELECT * FROM used_tokens")) == 1
    (approval,) = rows(store, "SELECT * FROM approvals")
    assert (approval["status"], approval["snapshot_hash"]) == ("active", h)
    assert rows(store, "SELECT review_state FROM jobs")[0]["review_state"] == "approved"


def test_raw_token_is_never_stored(store, svc):
    h = ready_job(store)
    token = mint_act(svc, "approve", h)
    consume(store, svc, token)
    raw = open(store.path, "rb").read()
    wal = store.path.with_name(store.path.name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    assert token.encode() not in raw
    assert token.split(".")[3].encode() not in raw  # nor its MAC


def test_replay_rejected_while_first_command_still_queued(store, svc):
    h = ready_job(store)
    token = mint_act(svc, "approve", h)
    consume(store, svc, token)
    e = err_of(consume, store, svc, token)  # immediately, worker not polled or acked
    assert (e.code, e.status) == ("token_replayed", 409)  # not command_pending/already_approved
    assert len(rows(store, "SELECT * FROM commands WHERE action='approve'")) == 1
    assert len(rows(store, "SELECT * FROM used_tokens")) == 1


def test_replay_beats_stale_snapshot(store, svc):
    h1 = ready_job(store)
    token = mint_act(svc, "approve", h1)
    consume(store, svc, token)
    post_snapshot(store, "b@example.test")  # H1 is now stale
    assert err_of(consume, store, svc, token).code == "token_replayed"


def test_concurrent_consume_same_token_exactly_one_wins(store, svc):
    h = ready_job(store)
    token = mint_act(svc, "approve", h)
    n = 8
    barrier = threading.Barrier(n)
    results: list = []

    def worker():
        barrier.wait()
        try:
            results.append(("ok", consume(store, svc, token)["command_id"]))
        except CpError as exc:
            results.append((exc.code, exc.status))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(r[0] for r in results) == ["ok"] + ["token_replayed"] * (n - 1)
    assert len(rows(store, "SELECT * FROM commands")) == 1
    assert len(rows(store, "SELECT * FROM used_tokens")) == 1


def test_concurrent_different_tokens_same_snapshot_one_approve(store, svc):
    h = ready_job(store)
    tokens = [mint_act(svc, "approve", h) for _ in range(6)]
    barrier = threading.Barrier(len(tokens))
    codes: list = []

    def worker(tok):
        barrier.wait()
        try:
            consume(store, svc, tok)
            codes.append("ok")
        except CpError as exc:
            codes.append(exc.code)

    threads = [threading.Thread(target=worker, args=(t,)) for t in tokens]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert codes.count("ok") == 1 and set(codes) <= {"ok", "already_approved"}
    assert len(rows(store, "SELECT * FROM commands WHERE action='approve'")) == 1
    assert len(rows(store, "SELECT * FROM used_tokens")) == 1  # losers stay unused


# --------------------------------------------------------- stale / T-3
def test_stale_snapshot_rejected_without_consuming(store, svc):
    h1 = ready_job(store)
    old_token = mint_act(svc, "approve", h1)
    h2, result = post_snapshot(store, "b@example.test")
    assert h2 != h1 and result["stale_hash"] == h1
    e = err_of(consume, store, svc, old_token)
    assert (e.code, e.status) == ("stale_snapshot", 409)
    assert rows(store, "SELECT * FROM used_tokens") == []  # NOT consumed
    assert rows(store, "SELECT * FROM commands") == []
    new_token = mint_act(svc, "approve", h2)  # reload mints a token for H2
    assert consume(store, svc, new_token)["status"] == "queued"


def test_new_snapshot_invalidates_approvals_and_supersedes_queued_approve(store, svc):
    h1 = ready_job(store)
    out = consume(store, svc, mint_act(svc, "approve", h1))
    h2, result = post_snapshot(store, "b@example.test")
    assert result["invalidated_approvals"] == 1 and result["superseded_commands"] == 1
    assert rows(store, "SELECT status FROM approvals")[0]["status"] == "invalidated"
    assert store.get_command(out["command_id"])["status"] == "superseded"
    assert store.list_queued_commands(RUN) == ([], {})  # never delivered again
    statuses = {r["snapshot_hash"]: r["status"] for r in rows(store, "SELECT * FROM snapshots")}
    assert statuses == {h1: "stale", h2: "current"}


def test_snapshot_supersedes_queued_reject_but_never_edit(store, svc):
    h1 = ready_job(store)
    edit = consume(store, svc, mint_act(svc, "edit", h1), value="new@example.test")
    post_snapshot(store, "new@example.test")  # H2 = result of the edit
    assert store.get_command(edit["command_id"])["status"] == "queued"  # edit survives
    store.ack(RUN, edit["command_id"])
    h2 = store.current_snapshot_hash(RUN, JOB)
    reject = consume(store, svc, mint_act(svc, "reject", h2))
    post_snapshot(store, "third@example.test")
    assert store.get_command(reject["command_id"])["status"] == "superseded"
    assert store.get_command(edit["command_id"])["status"] == "acked"


def test_snapshot_hash_binding_and_hash_mismatch(store):
    s = snap()
    e = err_of(store.put_snapshot, RUN, JOB, "0" * 64, s)
    assert (e.code, e.status) == ("hash_mismatch", 422)
    assert rows(store, "SELECT * FROM snapshots") == [] and rows(store, "SELECT * FROM runs") == []


def test_snapshot_same_hash_is_duplicate_and_screenshots_do_not_change_hash(store):
    h, first = post_snapshot(store)
    assert first["duplicate"] is False and first["stale_hash"] is None
    h_again, second = post_snapshot(store, screenshots=["shot1.png"])
    assert h_again == h and second["duplicate"] is True
    body = json.loads(rows(store, "SELECT body_json FROM snapshots")[0]["body_json"])
    assert body["screenshots"] == ["shot1.png"]  # refreshed, hash unchanged
    assert post_snapshot(store, "other@example.test")[0] != h  # a changed value changes it


def test_reappearing_stale_hash_becomes_current_but_stays_unapproved(store, svc):
    h1 = ready_job(store)
    consume(store, svc, mint_act(svc, "approve", h1))
    h2, _ = post_snapshot(store, "b@example.test")
    h1_again, result = post_snapshot(store, "a@example.test")  # undone edit
    assert h1_again == h1 and result["stale_hash"] == h2 and result["duplicate"] is False
    assert store.current_snapshot_hash(RUN, JOB) == h1
    assert rows(store, "SELECT status FROM approvals")[0]["status"] == "invalidated"
    # must be re-approved: a new approve for H1 is accepted (old approval is invalidated)
    assert consume(store, svc, mint_act(svc, "approve", h1))["status"] == "queued"


def test_content_hash_matches_codex_reference_vectors():
    """Reference values produced once by the contracts package (read-only run)."""
    assert ReviewSnapshot().content_hash() == (
        "a33e6725f7b45d6caa33f55706edb1525def0bdd348f00c233bc06b7d0a7a515"
    )
    full = ReviewSnapshot.model_validate(
        {
            "fields": [
                {"field_key": "zeta", "intended": "\u00dcn\u00ef", "actual": "\u00dcn\u00ef",
                 "matched": True},
                {"field_key": "alpha", "intended": 3, "actual": "3", "matched": False,
                 "escalated": True, "reason": "mismatch", "generated": True},
            ],
            "uploads": [{"field_key": "resume", "name": "cv.pdf", "sha256": "ab" * 32}],
            "generated_texts": {"cover": "Hello"},
            "unanswered": ["q2", "q1"],
            "screenshots": ["a.png"],
        }
    )
    assert full.content_hash() == (
        "0cab96704b0f0a42a6155ff5bad5d511e46b33b2a223b5150cef5617f1097194"
    )


def test_content_hash_is_order_insensitive_for_fields_and_unanswered():
    a = ReviewSnapshot.model_validate({"fields": [{"field_key": "a"}, {"field_key": "b"}],
                                       "unanswered": ["x", "y"]})
    b = ReviewSnapshot.model_validate({"fields": [{"field_key": "b"}, {"field_key": "a"}],
                                       "unanswered": ["y", "x"]})
    assert a.content_hash() == b.content_hash()
    with pytest.raises(ValueError):
        ReviewSnapshot.model_validate({"fields": [{"field_key": "a"}, {"field_key": "a"}]})


# ---------------------------------------------------- check order / T-14, T-15
def test_edit_flow_blocks_approve_until_new_snapshot(store, svc):
    h1 = ready_job(store)
    edit = consume(store, svc, mint_act(svc, "edit", h1), value="n@example.test")
    assert rows(store, "SELECT review_state FROM jobs")[0]["review_state"] == "edit_pending"
    (cmd,) = store.list_queued_commands(RUN)[0]
    assert (cmd["action"], cmd["field_key"], cmd["value"]) == ("edit", "email", "n@example.test")
    assert cmd["token_hash"] and cmd["snapshot_hash"] == h1

    approve_token = mint_act(svc, "approve", h1)
    e = err_of(consume, store, svc, approve_token)
    assert (e.code, e.status) == ("edit_pending", 409)
    assert rows(store, "SELECT * FROM used_tokens WHERE action='approve'") == []  # unconsumed

    h2, result = post_snapshot(store, "n@example.test")  # worker posts H2 inside graph.invoke
    assert result["superseded_commands"] == 0
    assert store.get_command(edit["command_id"])["status"] == "queued"  # edit not superseded
    assert store.ack(RUN, edit["command_id"]) == {
        "ok": True, "already_acked": False, "status": "acked"}  # ack arrives last
    assert consume(store, svc, mint_act(svc, "approve", h2))["status"] == "queued"


def test_second_approve_token_same_snapshot_is_already_approved_not_command_pending(store, svc):
    h = ready_job(store)
    first, second = mint_act(svc, "approve", h), mint_act(svc, "approve", h)
    consume(store, svc, first)
    e = err_of(consume, store, svc, second)  # first still queued
    assert (e.code, e.status) == ("already_approved", 409)
    assert len(rows(store, "SELECT * FROM used_tokens")) == 1  # second stays unused
    assert len(rows(store, "SELECT * FROM commands WHERE action='approve'")) == 1


def test_already_approved_persists_after_worker_ack(store, svc):
    h = ready_job(store)
    out = consume(store, svc, mint_act(svc, "approve", h))
    store.ack(RUN, out["command_id"])
    assert rows(store, "SELECT status FROM approvals")[0]["status"] == "consumed_by_worker"
    assert err_of(consume, store, svc, mint_act(svc, "approve", h)).code == "already_approved"


def test_precedence_edit_pending_beats_already_approved(store, svc):
    h = ready_job(store)
    out = consume(store, svc, mint_act(svc, "approve", h))
    store.ack(RUN, out["command_id"])  # approval exists, nothing queued
    consume(store, svc, mint_act(svc, "edit", h), value="x@example.test")  # edit_pending
    assert err_of(consume, store, svc, mint_act(svc, "approve", h)).code == "edit_pending"


def test_precedence_already_approved_beats_command_pending(store, svc):
    h = ready_job(store)
    consume(store, svc, mint_act(svc, "approve", h))  # approve queued, approval active
    assert err_of(consume, store, svc, mint_act(svc, "approve", h)).code == "already_approved"
    # reject/edit have no already_approved rule, so they report command_pending
    assert err_of(consume, store, svc, mint_act(svc, "edit", h)).code == "command_pending"
    assert err_of(consume, store, svc, mint_act(svc, "reject", h)).code == "command_pending"


def test_precedence_replay_beats_everything_and_stale_beats_edit_pending(store, svc):
    h1 = ready_job(store)
    first_edit = consume(store, svc, mint_act(svc, "edit", h1), value="x@example.test")
    approve_h1 = mint_act(svc, "approve", h1)
    post_snapshot(store, "x@example.test")  # H1 stale, edit_pending cleared
    store.ack(RUN, first_edit["command_id"])
    assert err_of(consume, store, svc, approve_h1).code == "stale_snapshot"
    # edit_pending with a *current* hash still wins over command_pending
    h2 = store.current_snapshot_hash(RUN, JOB)
    consume(store, svc, mint_act(svc, "edit", h2), value="y@example.test")
    assert err_of(consume, store, svc, mint_act(svc, "approve", h2)).code == "edit_pending"


def test_unknown_run_or_job_is_404_and_consumes_nothing(store, svc):
    h = "cd" * 32
    assert err_of(consume, store, svc, mint_act(svc, "approve", h)).status == 404
    ready_job(store)
    other = mint_act(svc, "approve", h, job="NOJOB")
    assert err_of(consume, store, svc, other).status == 404
    assert rows(store, "SELECT * FROM used_tokens") == []


def test_terminal_run_accepts_no_commands(store, svc):
    h = ready_job(store)
    store.insert_event(event("E14", payload={"status": "COMPLETED"}))
    assert err_of(consume, store, svc, mint_act(svc, "approve", h)).code == "run_terminal"
    assert err_of(store.queue_run_command, RUN, "pause").code == "run_terminal"


def test_consume_requires_act_token(store, svc):
    ready_job(store)
    claims = svc.verify(svc.mint("view", RUN, job=JOB), "view")
    assert err_of(store.consume_act, claims, "0" * 64).status == 401


def test_value_limits_checked_before_consuming(store, svc):
    h = ready_job(store)
    too_big = "x" * 70_000
    assert err_of(consume, store, svc, mint_act(svc, "edit", h), value=too_big).status == 400
    assert err_of(consume, store, svc, mint_act(svc, "edit", h), value=float("nan")).status == 422
    assert rows(store, "SELECT * FROM used_tokens") == []
    store.insert_event(event("E06", payload={"field_key": "q1"}))
    gate = store.get_job(RUN, JOB)["gate_hash"]
    token = svc.mint("act", RUN, job=JOB, action="answer", snapshot_hash=gate, field_key="q1")
    assert err_of(consume, store, svc, token, value="x" * 2001).status == 400
    assert err_of(consume, store, svc, token, value=["not", "text"]).status == 422
    assert consume(store, svc, token, value="x" * 2000)["status"] == "queued"


# ---------------------------------------------------------------- gates
def test_answer_gate_single_answer_and_field_binding(store, svc):
    store.insert_event(event("E06", payload={"field_key": "q1", "label": "Why?"}))
    gate = store.get_job(RUN, JOB)["gate_hash"]
    assert gate and store.get_job(RUN, JOB)["gate_kind"] == "ask"
    wrong_field = svc.mint("act", RUN, job=JOB, action="answer", snapshot_hash=gate, field_key="q2")
    assert err_of(consume, store, svc, wrong_field, value="a").code == "no_open_gate"
    good = svc.mint("act", RUN, job=JOB, action="answer", snapshot_hash=gate, field_key="q1")
    out = consume(store, svc, good, value="because")
    (cmd,) = store.list_queued_commands(RUN)[0]
    assert (cmd["action"], cmd["field_key"], cmd["value"], cmd["snapshot_hash"]) == (
        "answer", "q1", "because", None)
    assert cmd["command_id"] == out["command_id"]
    second = svc.mint("act", RUN, job=JOB, action="answer", snapshot_hash=gate, field_key="q1")
    assert err_of(consume, store, svc, second, value="again").code == "no_open_gate"
    assert len(rows(store, "SELECT * FROM commands")) == 1


def test_handoff_done_needs_an_open_handoff_gate(store, svc):
    ready_job(store)
    bogus = svc.mint("act", RUN, job=JOB, action="handoff_done", snapshot_hash="ee" * 32)
    assert err_of(consume, store, svc, bogus).code == "no_open_gate"
    store.insert_event(event("E04", payload={"site": "s"}))
    gate = store.get_job(RUN, JOB)["gate_hash"]
    assert store.get_job(RUN, JOB)["gate_kind"] == "handoff"
    assert err_of(consume, store, svc, bogus).code == "no_open_gate"  # hash must match
    token = svc.mint("act", RUN, job=JOB, action="handoff_done", snapshot_hash=gate)
    assert consume(store, svc, token)["status"] == "queued"
    assert store.get_job(RUN, JOB)["gate_kind"] == "none"


def test_skip_is_bound_to_the_e02_shortlist_gate(store, svc):
    store.insert_event(event("E02", job=None, payload={
        "chosen": [{"job_id": "J1"}, {"job_id": "J2"}, {"job_id": "bad id!"}],
        "skipped": []}))
    gate = store.get_job(RUN, "J2")["gate_hash"]
    assert store.get_job(RUN, "bad id!") is None
    token = svc.mint("act", RUN, job="J2", action="skip", snapshot_hash=gate)
    consume(store, svc, token)
    assert store.list_queued_commands(RUN)[0][0]["job_id"] == "J2"
    assert store.get_job(RUN, "J1")["gate_kind"] == "shortlist"  # other entry still open


# ------------------------------------------------------ run commands / coalescing
def test_pause_resume_cancel_coalescing_and_order(store):
    store.heartbeat(RUN, "RUNNING")
    a = store.queue_run_command(RUN, "pause")
    dup = store.queue_run_command(RUN, "pause")
    assert dup["duplicate"] is True and dup["command_id"] == a["command_id"]
    b = store.queue_run_command(RUN, "resume")
    assert b["duplicate"] is False
    assert store.queue_run_command(RUN, "resume")["command_id"] == b["command_id"]
    c = store.queue_run_command(RUN, "pause")  # latest is an unacked resume: not a duplicate
    assert c["duplicate"] is False
    x = store.queue_run_command(RUN, "cancel")
    assert store.queue_run_command(RUN, "cancel") == {**x, "duplicate": True}
    commands, approvals = store.list_queued_commands(RUN)
    assert [cmd["action"] for cmd in commands] == ["pause", "resume", "pause", "cancel"]
    assert approvals == {}
    assert all(cmd["job_id"] is None and cmd["token_hash"] is None for cmd in commands)
    assert err_of(store.queue_run_command, "NOPE", "pause").status == 404
    assert err_of(store.queue_run_command, RUN, "approve").status == 403


def test_acked_cancel_makes_run_terminal_and_expires_queued(store):
    store.heartbeat(RUN, "RUNNING")
    pause = store.queue_run_command(RUN, "pause")
    cancel = store.queue_run_command(RUN, "cancel")
    store.ack(RUN, cancel["command_id"])
    assert store.get_run(RUN)["terminal"] == 1
    assert store.get_command(pause["command_id"])["status"] == "expired"
    assert store.ack(RUN, pause["command_id"])["status"] == "expired"  # still 200


def test_wrong_run_commands_never_delivered_and_foreign_ack_is_404(store):
    store.heartbeat("A", "RUNNING")
    store.heartbeat("B", "RUNNING")
    ca = store.queue_run_command("A", "pause")
    cb = store.queue_run_command("B", "pause")
    assert [c["command_id"] for c in store.list_queued_commands("A")[0]] == [ca["command_id"]]
    assert [c["command_id"] for c in store.list_queued_commands("B")[0]] == [cb["command_id"]]
    e = err_of(store.ack, "A", cb["command_id"])
    assert (e.code, e.status) == ("command_unknown", 404)
    assert store.get_command(cb["command_id"])["status"] == "queued"  # untouched


def test_list_queued_is_capped_at_50_and_ascending(store):
    store.heartbeat(RUN, "RUNNING")
    with store._tx() as conn:
        for i in range(60):
            cmd = Command(command_id=f"cmd_{i:03d}", run_id=RUN, action="pause")
            conn.execute(
                "INSERT INTO commands(command_id, run_id, action, body_json, created_at) "
                "VALUES(?,?,?,?,?)",
                (cmd.command_id, RUN, "pause", json.dumps(cmd.model_dump(mode="json")), "t"))
    commands, _ = store.list_queued_commands(RUN, limit=500)
    assert [c["command_id"] for c in commands] == [f"cmd_{i:03d}" for i in range(50)]


# ------------------------------------------------------------------- ack
def test_ack_is_idempotent_and_unknown_is_404(store, svc):
    h = ready_job(store)
    out = consume(store, svc, mint_act(svc, "approve", h))
    cid = out["command_id"]
    assert store.ack(RUN, cid) == {"ok": True, "already_acked": False, "status": "acked"}
    assert store.ack(RUN, cid) == {"ok": True, "already_acked": True, "status": "acked"}
    assert store.list_queued_commands(RUN) == ([], {})
    assert err_of(store.ack, RUN, "cmd_missing").status == 404
    assert store.get_command(cid)["acked_at"] is not None


def test_ack_of_superseded_command_is_200_and_status_unchanged(store, svc):
    h1 = ready_job(store)
    out = consume(store, svc, mint_act(svc, "approve", h1))
    post_snapshot(store, "b@example.test")
    assert store.ack(RUN, out["command_id"]) == {
        "ok": True, "already_acked": False, "status": "superseded"}
    assert store.get_command(out["command_id"])["status"] == "superseded"


# ----------------------------------------------------------------- events
def test_event_insert_is_idempotent_and_run_is_upserted(store):
    e = event("E01", job=None, payload={"goal": "g"})
    first = store.insert_event(e)
    again = store.insert_event(e.model_copy())
    assert first["duplicate"] is False and again == {**first, "duplicate": True}
    assert len(rows(store, "SELECT * FROM events")) == 1
    assert store.get_run(RUN)["last_status"] == "QUEUED"
    other = store.insert_event(event("E01", job=None, payload={"goal": "g"}, minute=1))
    assert other["duplicate"] is False and other["seq"] == first["seq"] + 1


def test_canonical_json_matches_the_spec():
    assert canonical_json({"b": 1, "a": "\u00e9"}) == '{"a":"\u00e9","b":1}'


def test_worker_posted_e15_is_rejected_and_stored_nowhere(store):
    e = err_of(store.insert_event, event("E15", job=None))
    assert (e.code, e.status) == ("event_not_allowed", 422)
    assert rows(store, "SELECT * FROM events") == [] and rows(store, "SELECT * FROM runs") == []
    assert store.insert_event(event("E15", job=None), generated=True)["accepted"] is True


def test_e07_requires_a_current_snapshot_hash(store):
    h = snap().content_hash()
    e = err_of(store.insert_event, event("E07", payload={"snapshot_hash": h}))
    assert (e.code, e.status) == ("snapshot_unknown", 409)
    assert rows(store, "SELECT * FROM events") == []
    assert store.get_run(RUN) is not None  # the run was still upserted
    assert err_of(store.insert_event, event("E07", payload={})).status == 422
    assert err_of(store.insert_event, event("E07", job=None, payload={"snapshot_hash": h})).status == 422
    post_snapshot(store)
    ok = store.insert_event(event("E07", payload={"snapshot_hash": h, "counts": {"filled": 1}}))
    assert ok["duplicate"] is False
    # a second message for the same (run, job, event, snapshot) is a duplicate, whatever its text
    again = store.insert_event(event("E07", message="resend", minute=5, payload={"snapshot_hash": h}))
    assert again["duplicate"] is True and again["seq"] == ok["seq"]
    # a resend stays accepted after the hash went stale (idempotent retry of an old event)
    post_snapshot(store, "z@example.test")
    assert store.insert_event(event("E07", payload={"snapshot_hash": h, "counts": {"filled": 1}}))[
        "duplicate"] is True


def test_review_snapshot_in_payload_is_dropped_before_storage(store):
    s = snap()
    post_snapshot(store)
    store.insert_event(event("E08", payload={
        "snapshot_hash": s.content_hash(), "review_snapshot": s.model_dump(mode="json")}))
    body = json.loads(rows(store, "SELECT body_json FROM events")[0]["body_json"])
    assert "review_snapshot" not in body["payload"] and "snapshot_hash" in body["payload"]


def test_e06_requires_field_key(store):
    assert err_of(store.insert_event, event("E06", payload={})).code == "payload_invalid"
    assert rows(store, "SELECT * FROM events") == []


def test_e13_and_e14_update_run_state(store):
    store.insert_event(event("E13", job=None, payload={"state": "paused"}))
    assert store.get_run(RUN)["paused"] == 1
    store.insert_event(event("E13", job=None, payload={"state": "resumed"}, minute=1))
    assert store.get_run(RUN)["paused"] == 0
    cmd = store.queue_run_command(RUN, "pause")
    store.insert_event(event("E14", job=None, payload={"status": "PARTIAL"}))
    run = store.get_run(RUN)
    assert (run["terminal"], run["last_status"]) == (1, "PARTIAL")
    assert store.get_command(cmd["command_id"])["status"] == "expired"
    assert store.ack(RUN, cmd["command_id"])["status"] == "expired"


def test_heartbeat_upserts_and_reports_channel_flag(store, clock):
    assert store.heartbeat("NEW", "RUNNING") == {"channel_unreachable": False}
    run = store.get_run("NEW")
    assert run["last_status"] == "RUNNING" and run["last_heartbeat_at"] == utc_iso(clock.t)


# ----------------------------------------------- GET-safety / pure minting
def test_minting_many_tokens_leaves_the_database_byte_identical(store, svc):
    h = ready_job(store)
    store.insert_event(event("E01", job=None))
    before = db_checksum(store)
    for i in range(300):
        svc.mint("view", RUN, job=JOB)
        svc.mint("run", RUN)
        svc.mint("evd", RUN, job=JOB, field_key=f"ev_{i}")
        for action in ("approve", "reject", "skip", "handoff_done"):
            svc.mint("act", RUN, job=JOB, action=action, snapshot_hash=h)
        svc.mint("act", RUN, job=JOB, action="edit", snapshot_hash=h, field_key="email")
    assert db_checksum(store) == before
    assert rows(store, "SELECT * FROM used_tokens") == []
    file_before = store.path.stat().st_mtime_ns
    svc.mint("view", RUN)
    assert store.path.stat().st_mtime_ns == file_before


def test_read_methods_never_write(store, svc):
    h = ready_job(store)
    consume(store, svc, mint_act(svc, "approve", h))
    before = db_checksum(store)
    for _ in range(10):  # prefetch simulation
        store.list_queued_commands(RUN)
        store.list_queued_commands("NEWRUN")
        store.get_run(RUN), store.get_run("NEWRUN")
        store.get_job(RUN, JOB), store.get_job(RUN, "nope")
        store.current_snapshot_hash(RUN, JOB), store.get_current_snapshot(RUN, JOB)
        store.get_command("cmd_x"), store.ping()
    assert db_checksum(store) == before
    assert store.list_queued_commands("NEWRUN") == ([], {})
    assert store.get_run("NEWRUN") is None  # an unseen run is not created by a read


def test_failed_consume_leaves_database_unchanged(store, svc):
    h = ready_job(store)
    consume(store, svc, mint_act(svc, "approve", h))
    before = db_checksum(store)
    for code_token in (mint_act(svc, "approve", h), mint_act(svc, "edit", h),
                       mint_act(svc, "reject", h)):
        with pytest.raises(CpError):
            consume(store, svc, code_token)
    assert db_checksum(store) == before


# ---------------------------------------------------------------- retention
def test_purge_keeps_used_tokens_for_at_least_exp_plus_24h(store, svc, clock):
    h = ready_job(store)
    consume(store, svc, mint_act(svc, "approve", h))
    assert store.purge_used_tokens(now=clock.t + 24 * 3600) == 0  # still inside exp + 24 h
    assert store.purge_used_tokens(now=clock.t + 24 * 3600 + 1801) == 1
