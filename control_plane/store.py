"""SQLite store for the control plane (CONTROL_PLANE_API.md sections 3, 5.4, 8).

Rules enforced here:
  * WAL, foreign_keys=ON, busy timeout; every mutation runs in BEGIN IMMEDIATE so two
    concurrent writers are serialised and the second sees the first one's rows.
  * One connection per thread (FastAPI runs sync handlers in a thread pool); the
    database file is the only shared state.
  * Reads (`list_queued_commands`, getters) never write. GET handlers only use those.
  * Raw tokens, the signing key, the bearer and the Telegram token are never stored
    (`used_tokens` and `commands` hold SHA-256 digests only).
  * A token is consumed if and only if its Command row exists (atomic consume).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import Command, Event, ReviewSnapshot
from .tokens import ID_PATTERN, Claims, CpError, utc_iso

SCHEMA_VERSION = "1"
BUSY_TIMEOUT_MS = 5000
MAX_QUEUED_COMMANDS_PER_POLL = 50
MAX_EDIT_VALUE_BYTES = 64 * 1024
MAX_ANSWER_CHARS = 2000
USED_TOKEN_RETENTION_S = 24 * 3600 + 30 * 60  # exp + 24 h, measured from used_at (act <= 30 min)
TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "BLOCKED", "CANCELLED"})

# action -> jobs.gate_kind that must be open for gate-bound act tokens
_GATE_FOR_ACTION = {"answer": "ask", "handoff_done": "handoff", "skip": "shortlist"}
_REVIEW_ACTIONS = ("approve", "edit", "reject")
_HEX64 = frozenset("0123456789abcdef")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_heartbeat_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'QUEUED',
    terminal INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    channel_unreachable INTEGER NOT NULL DEFAULT 0,
    offline_notified_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    job_id TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'none'
        CHECK (review_state IN ('none','ready','edit_pending','approved','rejected')),
    gate_kind TEXT NOT NULL DEFAULT 'none'
        CHECK (gate_kind IN ('none','handoff','ask','shortlist')),
    gate_hash TEXT,
    gate_field_key TEXT,
    PRIMARY KEY (run_id, job_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('current','stale')),
    received_at TEXT NOT NULL,
    PRIMARY KEY (run_id, job_id, snapshot_hash),
    FOREIGN KEY (run_id, job_id) REFERENCES jobs(run_id, job_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS snapshots_one_current
    ON snapshots(run_id, job_id) WHERE status = 'current';
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    job_id TEXT,
    event_id TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    snapshot_key TEXT UNIQUE,
    body_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS deliveries (
    event_seq INTEGER NOT NULL REFERENCES events(seq),
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    PRIMARY KEY (event_seq, channel)
);
CREATE TABLE IF NOT EXISTS commands (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    job_id TEXT,
    action TEXT NOT NULL,
    body_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','acked','superseded','expired')),
    token_hash TEXT UNIQUE,
    created_at TEXT NOT NULL,
    acked_at TEXT,
    approval_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS commands_run_status ON commands(run_id, status, seq);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    job_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    command_id TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','consumed_by_worker','invalidated'))
);
CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_live
    ON approvals(run_id, job_id, snapshot_hash)
    WHERE status IN ('active','consumed_by_worker');
CREATE TABLE IF NOT EXISTS used_tokens (
    token_hash TEXT PRIMARY KEY,
    typ TEXT NOT NULL,
    run_id TEXT NOT NULL,
    job_id TEXT,
    action TEXT,
    used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    job_id TEXT,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime TEXT NOT NULL,
    path TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS evidence_dedup ON evidence(run_id, job_id, sha256);
CREATE TABLE IF NOT EXISTS telegram_links (
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    job_id TEXT,
    gate_hash TEXT,
    field_key TEXT,
    kind TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
"""


def canonical_json(value: Any) -> str:
    """Sorted keys, `,`/`:` separators, ensure_ascii=False (event dedup, section 2.3 W4)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


class Store:
    """Thread-safe (per-thread connections) SQLite persistence."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------ connections
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,  # autocommit; transactions are explicit
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            with self._all_lock:
                self._all.append(conn)
        return conn

    def close(self) -> None:
        with self._all_lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # AUDIT-025: telegram_links was keyed by message_id alone (ids repeat across chats).
            # Nothing ever wrote to the old table, so it is safe to recreate with the new key.
            old = conn.execute("PRAGMA table_info(telegram_links)").fetchall()
            if old and "chat_id" not in {row["name"] for row in old}:
                conn.execute("DROP TABLE telegram_links")
            # executescript() would COMMIT implicitly, so run the statements one by one.
            for statement in filter(None, (s.strip() for s in SCHEMA.split(";\n"))):
                conn.execute(statement)
            conn.execute(
                "INSERT OR IGNORE INTO kv(k, v) VALUES('schema_version', ?)", (SCHEMA_VERSION,)
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE: the write lock is taken before any read."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        try:
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """Deferred read transaction (consistent view; takes no write lock)."""
        conn = self._conn()
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            conn.execute("COMMIT")

    def now(self) -> int:
        return int(self._clock())

    def ping(self) -> bool:
        try:
            self._conn().execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _ensure_run(conn: sqlite3.Connection, run_id: str, now: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO runs(run_id, created_at) VALUES(?, ?)", (run_id, utc_iso(now))
        )

    @staticmethod
    def _ensure_job(conn: sqlite3.Connection, run_id: str, job_id: str) -> None:
        conn.execute("INSERT OR IGNORE INTO jobs(run_id, job_id) VALUES(?, ?)", (run_id, job_id))

    @staticmethod
    def _current_hash(conn: sqlite3.Connection, run_id: str, job_id: str) -> str | None:
        row = conn.execute(
            "SELECT snapshot_hash FROM snapshots WHERE run_id=? AND job_id=? AND status='current'",
            (run_id, job_id),
        ).fetchone()
        return row["snapshot_hash"] if row else None

    @staticmethod
    def _expire_queued(conn: sqlite3.Connection, run_id: str, except_id: str | None = None) -> int:
        cur = conn.execute(
            "UPDATE commands SET status='expired' WHERE run_id=? AND status='queued' "
            "AND command_id IS NOT ?",
            (run_id, except_id),
        )
        return cur.rowcount

    @staticmethod
    def _new_command_id() -> str:
        return "cmd_" + uuid.uuid4().hex  # 128-bit random

    @staticmethod
    def _expired_unexecuted_approvals(
        conn: sqlite3.Connection, run_id: str, job_id: str, now: int
    ) -> list[sqlite3.Row]:
        """Live approvals whose 30-minute window (D-030) has passed and that never led to a
        submit. A submit leaves an E09/E10/E11 event at or after the approval time; such an
        approval stays live forever, so the CP can never mint a second approve for content that
        may already have been sent (single-use, D-015). Read-only."""
        rows = conn.execute(
            "SELECT id, snapshot_hash, command_id, approved_at FROM approvals "
            "WHERE run_id=? AND job_id=? AND status IN ('active','consumed_by_worker') "
            "AND expires_at <= ?",
            (run_id, job_id, utc_iso(now)),
        ).fetchall()
        return [
            row
            for row in rows
            if conn.execute(
                "SELECT 1 FROM events WHERE run_id=? AND job_id=? "
                "AND event_id IN ('E09','E10','E11') AND received_at >= ? LIMIT 1",
                (run_id, job_id, row["approved_at"]),
            ).fetchone()
            is None
        ]

    def _reap_expired_approvals(
        self, conn: sqlite3.Connection, run_id: str, job_id: str, now: int
    ) -> int:
        """AUDIT-020: retire expired, unexecuted approvals so a fresh approval can be minted.

        Runs only inside a POST transaction (GET never writes). The used token stays in
        `used_tokens`, so the old token can never be replayed; only the per-snapshot
        'one live approval' lock is released and the job returns to `ready`."""
        stale = self._expired_unexecuted_approvals(conn, run_id, job_id, now)
        for row in stale:
            conn.execute("UPDATE approvals SET status='invalidated' WHERE id=?", (row["id"],))
            conn.execute(
                "UPDATE commands SET status='expired' WHERE command_id=? AND status='queued'",
                (row["command_id"],),
            )
        if stale and not conn.execute(
            "SELECT 1 FROM approvals WHERE run_id=? AND job_id=? "
            "AND status IN ('active','consumed_by_worker')",
            (run_id, job_id),
        ).fetchone():
            conn.execute(
                "UPDATE jobs SET review_state='ready' WHERE run_id=? AND job_id=? "
                "AND review_state='approved'",
                (run_id, job_id),
            )
        return len(stale)

    def approval_expired(self, run_id: str, job_id: str) -> bool:
        """Read-only: is the job's only blocker an expired, never-executed approval?"""
        with self._read() as conn:
            return bool(self._expired_unexecuted_approvals(conn, run_id, job_id, self.now()))

    # ------------------------------------------------------------------ reads
    def get_run(self, run_id: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_job(self, run_id: str, job_id: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id=? AND job_id=?", (run_id, job_id)
            ).fetchone()
        return dict(row) if row else None

    def current_snapshot_hash(self, run_id: str, job_id: str) -> str | None:
        with self._read() as conn:
            return self._current_hash(conn, run_id, job_id)

    def get_current_snapshot(self, run_id: str, job_id: str) -> tuple[str, dict] | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT snapshot_hash, body_json FROM snapshots "
                "WHERE run_id=? AND job_id=? AND status='current'",
                (run_id, job_id),
            ).fetchone()
        return (row["snapshot_hash"], json.loads(row["body_json"])) if row else None

    def get_command(self, command_id: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
        return dict(row) if row else None

    def list_queued_commands(
        self, run_id: str, limit: int = MAX_QUEUED_COMMANDS_PER_POLL
    ) -> tuple[list[dict], dict[str, dict]]:
        """Worker GET commands: read-only. Returns (commands, approvals sibling).

        Only `queued` commands of `run_id`, ascending by seq. Every approve command gets an
        `approvals[command_id] = {"expires_at": <aware ISO, +00:00>}` entry (mandatory for
        the worker's HttpTransport.poll). An unseen run yields ([], {}).
        """
        with self._read() as conn:
            rows = conn.execute(
                "SELECT command_id, run_id, action, body_json, approval_expires_at FROM commands "
                "WHERE run_id=? AND status='queued' ORDER BY seq ASC LIMIT ?",
                (run_id, min(limit, MAX_QUEUED_COMMANDS_PER_POLL)),
            ).fetchall()
        commands: list[dict] = []
        approvals: dict[str, dict] = {}
        for row in rows:
            body = json.loads(row["body_json"])
            if body.get("run_id") != run_id:  # defence in depth (T-6)
                raise RuntimeError("command run mismatch")
            commands.append(body)
            if row["action"] == "approve":
                approvals[row["command_id"]] = {"expires_at": row["approval_expires_at"]}
        return commands, approvals

    # ------------------------------------------------------------ worker writes
    def heartbeat(self, run_id: str, status: str) -> dict:
        now = self.now()
        with self._tx() as conn:
            self._ensure_run(conn, run_id, now)
            conn.execute(
                "UPDATE runs SET last_heartbeat_at=?, last_status=? WHERE run_id=?",
                (utc_iso(now), status, run_id),
            )
            row = conn.execute(
                "SELECT channel_unreachable FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return {"channel_unreachable": bool(row["channel_unreachable"])}

    def ack(self, run_id: str, command_id: str) -> dict:
        """W2: idempotent; terminal (superseded/expired) commands still answer 200."""
        now = self.now()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT command_id, action, status FROM commands WHERE command_id=? AND run_id=?",
                (command_id, run_id),
            ).fetchone()
            if row is None:  # unknown id or another run's id: same answer
                raise CpError("command_unknown", 404)
            status = row["status"]
            if status == "queued":
                conn.execute(
                    "UPDATE commands SET status='acked', acked_at=? WHERE command_id=?",
                    (utc_iso(now), command_id),
                )
                if row["action"] == "approve":
                    conn.execute(
                        "UPDATE approvals SET status='consumed_by_worker' "
                        "WHERE command_id=? AND status='active'",
                        (command_id,),
                    )
                if row["action"] == "cancel":  # acked cancel ends the run (9.2)
                    conn.execute("UPDATE runs SET terminal=1 WHERE run_id=?", (run_id,))
                    self._expire_queued(conn, run_id, except_id=command_id)
                return {"ok": True, "already_acked": False, "status": "acked"}
            return {"ok": True, "already_acked": status == "acked", "status": status}

    def put_snapshot(
        self, run_id: str, job_id: str, claimed_hash: str, snapshot: ReviewSnapshot
    ) -> dict:
        """W5: verify the claimed hash, make it current, supersede what it outdates."""
        if snapshot.content_hash() != claimed_hash:
            raise CpError("hash_mismatch", 422)
        new_hash = claimed_hash
        body_json = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
        now = self.now()
        with self._tx() as conn:
            self._ensure_run(conn, run_id, now)
            self._ensure_job(conn, run_id, job_id)
            existing = conn.execute(
                "SELECT status FROM snapshots WHERE run_id=? AND job_id=? AND snapshot_hash=?",
                (run_id, job_id, new_hash),
            ).fetchone()
            if existing is not None and existing["status"] == "current":
                # Same hash: refresh the body (screenshots are not part of the hash); nothing else.
                conn.execute(
                    "UPDATE snapshots SET body_json=? WHERE run_id=? AND job_id=? AND snapshot_hash=?",
                    (body_json, run_id, job_id, new_hash),
                )
                # AUDIT-021: a no-op edit (same value, same hash) is finished once the worker
                # has acked every edit command and posts a fresh read-back of this snapshot.
                if not conn.execute(
                    "SELECT 1 FROM commands WHERE run_id=? AND job_id=? AND action='edit' "
                    "AND status='queued'",
                    (run_id, job_id),
                ).fetchone():
                    conn.execute(
                        "UPDATE jobs SET review_state='ready' WHERE run_id=? AND job_id=? "
                        "AND review_state='edit_pending'",
                        (run_id, job_id),
                    )
                return {
                    "accepted": True,
                    "duplicate": True,
                    "stale_hash": None,
                    "invalidated_approvals": 0,
                    "superseded_commands": 0,
                }
            old_hash = self._current_hash(conn, run_id, job_id)
            if old_hash is not None:
                conn.execute(
                    "UPDATE snapshots SET status='stale' "
                    "WHERE run_id=? AND job_id=? AND status='current'",
                    (run_id, job_id),
                )
            if existing is None:
                conn.execute(
                    "INSERT INTO snapshots(run_id, job_id, snapshot_hash, body_json, status, "
                    "received_at) VALUES(?,?,?,?, 'current', ?)",
                    (run_id, job_id, new_hash, body_json, utc_iso(now)),
                )
            else:  # an older state reappeared (undone edit): current again
                conn.execute(
                    "UPDATE snapshots SET status='current', body_json=?, received_at=? "
                    "WHERE run_id=? AND job_id=? AND snapshot_hash=?",
                    (body_json, utc_iso(now), run_id, job_id, new_hash),
                )
            # Approvals not yet taken by the worker and bound to another hash are void.
            # (A `consumed_by_worker` approval stays: the worker already holds it, and keeping
            # it means the CP can never mint a second approve for that hash, D-015.)
            invalidated = conn.execute(
                "UPDATE approvals SET status='invalidated' "
                "WHERE run_id=? AND job_id=? AND snapshot_hash<>? AND status='active'",
                (run_id, job_id, new_hash),
            ).rowcount
            # Queued approve/reject are decisions about content that no longer exists.
            # `edit` (and every other action) is deliberately never superseded.
            superseded = self._supersede(conn, run_id, job_id, new_hash)
            conn.execute(
                "UPDATE jobs SET review_state='ready' WHERE run_id=? AND job_id=?", (run_id, job_id)
            )
        return {
            "accepted": True,
            "duplicate": False,
            "stale_hash": old_hash,
            "invalidated_approvals": invalidated,
            "superseded_commands": superseded,
        }

    @staticmethod
    def _supersede(conn: sqlite3.Connection, run_id: str, job_id: str, new_hash: str) -> int:
        rows = conn.execute(
            "SELECT command_id, body_json FROM commands "
            "WHERE run_id=? AND job_id=? AND status='queued' AND action IN ('approve','reject')",
            (run_id, job_id),
        ).fetchall()
        count = 0
        for row in rows:
            if json.loads(row["body_json"]).get("snapshot_hash") != new_hash:
                conn.execute(
                    "UPDATE commands SET status='superseded' WHERE command_id=?",
                    (row["command_id"],),
                )
                count += 1
        return count

    def insert_event(self, event: Event, *, generated: bool = False) -> dict:
        """W4: idempotent by dedup key; stores before any delivery; applies gate effects.

        `generated=True` is for CP-made events (E15). A worker-posted E15 is 422.
        """
        if event.event_id == "E15" and not generated:
            raise CpError("event_not_allowed", 422)
        body = event.model_dump(mode="json")
        body["payload"].pop("review_snapshot", None)  # snapshot body is stored once, in W5
        payload = body["payload"]
        kind = event.event_id
        snapshot_hash = payload.get("snapshot_hash")
        if kind in ("E07", "E08"):
            if not _is_hex64(snapshot_hash) or not event.job_id:
                raise CpError("payload_invalid", 422, "snapshot_hash and job_id are required")
        if kind == "E06" and not (isinstance(payload.get("field_key"), str) and payload["field_key"]):
            raise CpError("payload_invalid", 422, "field_key is required")

        dedup_key = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        snapshot_key = None
        if kind in ("E07", "E08"):
            snapshot_key = hashlib.sha256(
                f"{event.run_id}|{event.job_id}|{kind}|{snapshot_hash}".encode()
            ).hexdigest()

        now = self.now()
        error: CpError | None = None
        result: dict = {}
        with self._tx() as conn:
            self._ensure_run(conn, event.run_id, now)  # kept even if a precondition fails
            dup = conn.execute(
                "SELECT seq FROM events WHERE dedup_key=? OR (? IS NOT NULL AND snapshot_key=?)",
                (dedup_key, snapshot_key, snapshot_key),
            ).fetchone()
            if dup is not None:
                result = {"accepted": True, "duplicate": True, "seq": dup["seq"]}
            elif snapshot_key is not None and (
                self._current_hash(conn, event.run_id, event.job_id or "") != snapshot_hash
            ):
                error = CpError("snapshot_unknown", 409)
            else:
                cur = conn.execute(
                    "INSERT INTO events(run_id, job_id, event_id, dedup_key, snapshot_key, "
                    "body_json, received_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        event.run_id,
                        event.job_id,
                        kind,
                        dedup_key,
                        snapshot_key,
                        json.dumps(body, sort_keys=True, ensure_ascii=False),
                        utc_iso(now),
                    ),
                )
                self._apply_event_effects(conn, event, payload, dedup_key)
                result = {"accepted": True, "duplicate": False, "seq": cur.lastrowid}
        if error is not None:
            raise error
        return result

    def _apply_event_effects(
        self, conn: sqlite3.Connection, event: Event, payload: dict, dedup_key: str
    ) -> None:
        run_id, kind = event.run_id, event.event_id
        if kind in ("E04", "E05", "E06") and event.job_id and ID_PATTERN.match(event.job_id):
            self._ensure_job(conn, run_id, event.job_id)
            gate = "ask" if kind == "E06" else "handoff"
            conn.execute(
                "UPDATE jobs SET gate_kind=?, gate_hash=?, gate_field_key=? "
                "WHERE run_id=? AND job_id=?",
                (gate, dedup_key, payload.get("field_key") if kind == "E06" else None,
                 run_id, event.job_id),
            )
        elif kind == "E02":
            chosen = payload.get("chosen")
            for item in (chosen if isinstance(chosen, list) else [])[:200]:
                job_id = item.get("job_id") if isinstance(item, dict) else None
                if isinstance(job_id, str) and ID_PATTERN.match(job_id):
                    self._ensure_job(conn, run_id, job_id)
                    conn.execute(
                        "UPDATE jobs SET gate_kind='shortlist', gate_hash=?, gate_field_key=NULL "
                        "WHERE run_id=? AND job_id=?",
                        (dedup_key, run_id, job_id),
                    )
        elif kind == "E13":
            state = payload.get("state")
            if state in ("paused", "resumed"):
                conn.execute(
                    "UPDATE runs SET paused=? WHERE run_id=?", (1 if state == "paused" else 0, run_id)
                )
        elif kind == "E14":
            status = payload.get("status")
            if isinstance(status, str) and status in TERMINAL_STATUSES:
                conn.execute("UPDATE runs SET last_status=? WHERE run_id=?", (status, run_id))
            conn.execute("UPDATE runs SET terminal=1 WHERE run_id=?", (run_id,))
            self._expire_queued(conn, run_id)

    # ----------------------------------------------------------- human actions
    @staticmethod
    def _validate_value(action: str, value: Any) -> None:
        if action == "edit":
            try:
                size = len(json.dumps(value, allow_nan=False).encode("utf-8"))
            except (TypeError, ValueError):
                raise CpError("invalid_value", 422) from None
            if size > MAX_EDIT_VALUE_BYTES:
                raise CpError("bad_request", 400, "value too large")
        elif action == "answer":
            if not isinstance(value, str):
                raise CpError("invalid_value", 422)
            if len(value) > MAX_ANSWER_CHARS:
                raise CpError("bad_request", 400, "value too large")

    def consume_act(self, claims: Claims, token_hash: str, *, value: Any = None) -> dict:
        """Atomic single-use consume + Command insert (section 5.4 steps 6-10).

        `claims` must already be verified (MAC, type, expiry) and binding-checked.
        The first failing check decides the response; nothing is consumed on any failure.
        """
        action = claims.action or ""
        if claims.typ != "act" or not claims.job:
            raise CpError("invalid_token", 401)
        self._validate_value(action, value)  # before the transaction: nothing is consumed
        run_id, job_id, bound_hash = claims.run, claims.job, claims.snapshot_hash
        now = self.now()
        with self._tx() as conn:
            # Step 6: replay check first (fast path; step 9's PRIMARY KEY is the atomic guard).
            if conn.execute(
                "SELECT 1 FROM used_tokens WHERE token_hash=?", (token_hash,)
            ).fetchone():
                raise CpError("token_replayed", 409)
            run = conn.execute("SELECT terminal FROM runs WHERE run_id=?", (run_id,)).fetchone()
            job = conn.execute(
                "SELECT * FROM jobs WHERE run_id=? AND job_id=?", (run_id, job_id)
            ).fetchone()
            if run is None or job is None:
                raise CpError("not_found", 404)
            if run["terminal"]:
                raise CpError("run_terminal", 409)
            if action in _REVIEW_ACTIONS and self._reap_expired_approvals(
                conn, run_id, job_id, now
            ):
                job = conn.execute(
                    "SELECT * FROM jobs WHERE run_id=? AND job_id=?", (run_id, job_id)
                ).fetchone()

            # Step 7: snapshot (approve/edit/reject) or gate (answer/handoff_done/skip).
            if action in _REVIEW_ACTIONS:
                if self._current_hash(conn, run_id, job_id) != bound_hash:
                    raise CpError("stale_snapshot", 409)
            else:
                open_gate = (
                    job["gate_kind"] == _GATE_FOR_ACTION.get(action)
                    and job["gate_hash"] == bound_hash
                    and (action != "answer" or job["gate_field_key"] == claims.field_key)
                )
                if not open_gate:
                    raise CpError("no_open_gate", 409)

            # Step 8: job state, in the normative order (a) edit_pending (b) already_approved
            # (c) command_pending. (a) and (b) are approve-only; (c) covers approve/edit/reject.
            if action == "approve":
                if job["review_state"] == "edit_pending":
                    raise CpError("edit_pending", 409)
                if conn.execute(
                    "SELECT 1 FROM approvals WHERE run_id=? AND job_id=? AND snapshot_hash=? "
                    "AND status IN ('active','consumed_by_worker')",
                    (run_id, job_id, bound_hash),
                ).fetchone():
                    raise CpError("already_approved", 409)
            if action in _REVIEW_ACTIONS and conn.execute(
                "SELECT 1 FROM commands WHERE run_id=? AND job_id=? AND status='queued' "
                "AND action IN ('approve','edit','reject')",
                (run_id, job_id),
            ).fetchone():
                raise CpError("command_pending", 409)

            # Step 9: atomic single-use record.
            try:
                conn.execute(
                    "INSERT INTO used_tokens(token_hash, typ, run_id, job_id, action, used_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (token_hash, claims.typ, run_id, job_id, action, utc_iso(now)),
                )
            except sqlite3.IntegrityError:
                raise CpError("token_replayed", 409) from None

            # Step 10: the Command (and the approval) in the same transaction.
            command_id = self._new_command_id()
            command = Command(
                command_id=command_id,
                run_id=run_id,
                job_id=job_id,
                action=action,  # type: ignore[arg-type]
                token_hash=token_hash,
                snapshot_hash=bound_hash if action in _REVIEW_ACTIONS else None,
                field_key=claims.field_key if action in ("edit", "answer") else None,
                value=value if action in ("edit", "answer") else None,
            )
            expires_at = utc_iso(claims.exp) if action == "approve" else None
            try:
                conn.execute(
                    "INSERT INTO commands(command_id, run_id, job_id, action, body_json, status, "
                    "token_hash, created_at, approval_expires_at) VALUES(?,?,?,?,?, 'queued', ?,?,?)",
                    (
                        command_id,
                        run_id,
                        job_id,
                        action,
                        json.dumps(command.model_dump(mode="json"), sort_keys=True,
                                   ensure_ascii=False),
                        token_hash,
                        utc_iso(now),
                        expires_at,
                    ),
                )
                if action == "approve":
                    conn.execute(
                        "INSERT INTO approvals(run_id, job_id, snapshot_hash, token_hash, "
                        "command_id, approved_at, expires_at, status) "
                        "VALUES(?,?,?,?,?,?,?, 'active')",
                        (run_id, job_id, bound_hash, token_hash, command_id, utc_iso(now),
                         expires_at),
                    )
            except sqlite3.IntegrityError:
                # commands.token_hash UNIQUE or the one-live-approval index: never a 2nd approve.
                raise CpError(
                    "already_approved" if action == "approve" else "token_replayed", 409
                ) from None

            new_state = {"approve": "approved", "edit": "edit_pending", "reject": "rejected"}.get(
                action
            )
            if new_state:
                conn.execute(
                    "UPDATE jobs SET review_state=? WHERE run_id=? AND job_id=?",
                    (new_state, run_id, job_id),
                )
            else:  # answered / handed off / skipped: the gate is closed (one answer per gate)
                conn.execute(
                    "UPDATE jobs SET gate_kind='none', gate_hash=NULL, gate_field_key=NULL "
                    "WHERE run_id=? AND job_id=?",
                    (run_id, job_id),
                )
        return {"ok": True, "command_id": command_id, "status": "queued", "duplicate": False}

    def queue_run_command(self, run_id: str, action: str) -> dict:
        """pause/resume/cancel for a verified `run` token: idempotent and coalesced (3.2)."""
        if action not in ("pause", "resume", "cancel"):
            raise CpError("forbidden", 403)
        now = self.now()
        with self._tx() as conn:
            run = conn.execute("SELECT terminal FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise CpError("not_found", 404)
            if run["terminal"]:
                raise CpError("run_terminal", 409)
            if action == "cancel":
                existing = conn.execute(
                    "SELECT command_id FROM commands WHERE run_id=? AND status='queued' "
                    "AND action='cancel' ORDER BY seq DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
            else:
                last = conn.execute(
                    "SELECT command_id, action FROM commands WHERE run_id=? AND status='queued' "
                    "AND action IN ('pause','resume') ORDER BY seq DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                existing = last if last is not None and last["action"] == action else None
            if existing is not None:
                return {"ok": True, "command_id": existing["command_id"], "status": "queued",
                        "duplicate": True}
            command_id = self._new_command_id()
            command = Command(command_id=command_id, run_id=run_id, action=action)  # type: ignore[arg-type]
            conn.execute(
                "INSERT INTO commands(command_id, run_id, job_id, action, body_json, status, "
                "token_hash, created_at) VALUES(?,?,NULL,?,?, 'queued', NULL, ?)",
                (command_id, run_id, action,
                 json.dumps(command.model_dump(mode="json"), sort_keys=True), utc_iso(now)),
            )
        return {"ok": True, "command_id": command_id, "status": "queued", "duplicate": False}

    # -------------------------------------------------------------- retention
    def purge_used_tokens(self, now: int | None = None) -> int:
        """Drop used-token rows older than exp + 24 h (never shorter)."""
        cutoff = utc_iso((self.now() if now is None else now) - USED_TOKEN_RETENTION_S)
        with self._tx() as conn:
            return conn.execute("DELETE FROM used_tokens WHERE used_at < ?", (cutoff,)).rowcount

