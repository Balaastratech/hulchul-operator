"""Transactional SQLite ledger. Durable intent precedes every submit click."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

from src.operator.contracts import JobStatus


def application_key(company: str, canonical_url: str, external_job_id: str | None = None) -> str:
    """Normalize company and stable job identifier without merging distinct URLs."""
    company = " ".join(company.casefold().split())
    parts = urlsplit(canonical_url)
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
    return hashlib.sha256(json.dumps([company, external_job_id or url]).encode()).hexdigest()


def _digest(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected SHA-256 digest")


class SQLiteLedger:
    """Connections per operation support concurrent workers with atomic claims."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        """Open a durable file; clock injection allows deterministic expiry tests."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with closing(self._connect()) as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS applications (
                    run_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    snapshot_hash TEXT, PRIMARY KEY (run_id, job_id));
                CREATE TABLE IF NOT EXISTS actions (
                    run_id TEXT NOT NULL, job_id TEXT NOT NULL, action TEXT NOT NULL,
                    field_key TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY (run_id, job_id, action, field_key));
                CREATE TABLE IF NOT EXISTS approvals (
                    token_hash TEXT PRIMARY KEY, run_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL, expires_at REAL NOT NULL,
                    used_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    job_id TEXT, event_id TEXT NOT NULL, message TEXT NOT NULL,
                    created_at REAL NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _now(self) -> float:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must return an aware datetime")
        return now.timestamp()

    def register_application(self, run_id: str, job_id: str, company: str, canonical_url: str,
                             external_job_id: str | None = None) -> bool:
        """Reserve a dedupe identity; same run/job reservation is idempotent."""
        key = application_key(company, canonical_url, external_job_id)
        with self._transaction() as connection:
            existing = connection.execute("SELECT dedupe_key FROM applications WHERE run_id=? AND job_id=?",
                                          (run_id, job_id)).fetchone()
            if existing:
                if existing[0] != key:
                    raise ValueError("job identity cannot change")
                return True
            result = connection.execute("INSERT OR IGNORE INTO applications VALUES (?, ?, ?, ?, NULL)",
                                        (run_id, job_id, key, JobStatus.QUEUED.value))
            return result.rowcount == 1

    def has_application(self, company: str, url: str, external_job_id: str | None = None) -> bool:
        """Read the application dedupe index."""
        with self._transaction() as connection:
            return connection.execute("SELECT 1 FROM applications WHERE dedupe_key=?",
                                      (application_key(company, url, external_job_id),)).fetchone() is not None

    def claim_action(self, run_id: str, job_id: str, action: str, field_key: str) -> bool:
        """Exactly one owner of any action tuple; never reclaim unknown outcomes."""
        if action.casefold() == "submit":
            raise ValueError("submit requires begin_submission")
        with self._transaction() as connection:
            result = connection.execute("INSERT OR IGNORE INTO actions VALUES (?, ?, ?, ?, 'CLAIMED')",
                                        (run_id, job_id, action, field_key))
            return result.rowcount == 1

    def mark_success(self, run_id: str, job_id: str, action: str, field_key: str) -> None:
        """Only an existing reversible action claim can be completed."""
        with self._transaction() as connection:
            result = connection.execute("UPDATE actions SET status='SUCCESS' WHERE run_id=? AND job_id=? "
                                        "AND action=? AND field_key=? AND status IN ('CLAIMED','SUCCESS')",
                                        (run_id, job_id, action, field_key))
            if result.rowcount != 1:
                raise ValueError("action is not claimed")

    def is_done(self, run_id: str, job_id: str, action: str, field_key: str) -> bool:
        """Read completion without altering the action record."""
        with self._transaction() as connection:
            row = connection.execute("SELECT status FROM actions WHERE run_id=? AND job_id=? "
                                     "AND action=? AND field_key=?", (run_id, job_id, action, field_key)).fetchone()
            return row is not None and row[0] == "SUCCESS"

    def get_status(self, run_id: str, job_id: str) -> JobStatus | None:
        """Return durable application state or None for an unknown job."""
        with self._transaction() as connection:
            row = connection.execute("SELECT status FROM applications WHERE run_id=? AND job_id=?",
                                     (run_id, job_id)).fetchone()
            return JobStatus(row[0]) if row else None

    def record_review(self, run_id: str, job_id: str, snapshot_hash: str) -> None:
        """Bind a new gate and invalidate every earlier unused approval."""
        _digest(snapshot_hash)
        with self._transaction() as connection:
            row = connection.execute("SELECT status FROM applications WHERE run_id=? AND job_id=?",
                                     (run_id, job_id)).fetchone()
            if row is None or row[0] in {JobStatus.SUBMITTING, JobStatus.SUBMITTED_VERIFIED,
                                        JobStatus.SUBMITTED_UNVERIFIED}:
                raise ValueError("cannot review missing or submitted application")
            connection.execute("UPDATE applications SET status=?, snapshot_hash=? WHERE run_id=? AND job_id=?",
                               (JobStatus.READY_FOR_REVIEW, snapshot_hash, run_id, job_id))
            connection.execute("UPDATE approvals SET revoked=1 WHERE run_id=? AND job_id=?",
                               (run_id, job_id))

    def record_approval(self, run_id: str, job_id: str, token_hash: str,
                        snapshot_hash: str, expires_at: datetime) -> None:
        """Persist a signature-verified capability without permitting overwrite."""
        _digest(token_hash)
        _digest(snapshot_hash)
        if expires_at.tzinfo is None or expires_at.timestamp() > self._now() + 1800:
            raise ValueError("expiry must be aware and at most 30 minutes")
        with self._transaction() as connection:
            row = connection.execute("SELECT status, snapshot_hash FROM applications WHERE run_id=? AND job_id=?",
                                     (run_id, job_id)).fetchone()
            if row is None or tuple(row) != (JobStatus.READY_FOR_REVIEW, snapshot_hash):
                raise ValueError("approval does not match the current review gate")
            try:
                connection.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?, NULL, 0)",
                                   (token_hash, run_id, job_id, snapshot_hash, expires_at.timestamp()))
            except sqlite3.IntegrityError as error:
                raise ValueError("capability digest already exists") from error

    def consume_approval(self, run_id: str, job_id: str, token_hash: str, snapshot_hash: str) -> bool:
        """Consume the capability and release its gate in one transaction."""
        now = self._now()
        with self._transaction() as connection:
            gate = connection.execute("SELECT status, snapshot_hash FROM applications WHERE run_id=? AND job_id=?",
                                      (run_id, job_id)).fetchone()
            if gate is None or tuple(gate) != (JobStatus.READY_FOR_REVIEW, snapshot_hash):
                return False
            result = connection.execute("UPDATE approvals SET used_at=? WHERE token_hash=? AND run_id=? "
                                        "AND job_id=? AND snapshot_hash=? AND used_at IS NULL "
                                        "AND revoked=0 AND expires_at>?",
                                        (now, token_hash, run_id, job_id, snapshot_hash, now))
            if result.rowcount != 1:
                return False
            connection.execute("UPDATE applications SET status=? WHERE run_id=? AND job_id=?",
                               (JobStatus.APPROVED, run_id, job_id))
            return True

    def begin_submission(self, run_id: str, job_id: str, snapshot_hash: str) -> bool:
        """Atomically record irreversible intent; a restart may only verify."""
        with self._transaction() as connection:
            result = connection.execute("UPDATE applications SET status=? WHERE run_id=? AND job_id=? "
                                        "AND status=? AND snapshot_hash=? AND EXISTS (SELECT 1 FROM approvals "
                                        "WHERE approvals.run_id=applications.run_id AND approvals.job_id=applications.job_id "
                                        "AND approvals.snapshot_hash=applications.snapshot_hash AND used_at IS NOT NULL "
                                        "AND revoked=0 AND expires_at>?)",
                                        (JobStatus.SUBMITTING, run_id, job_id, JobStatus.APPROVED, snapshot_hash, self._now()))
            if result.rowcount != 1:
                return False
            connection.execute("INSERT INTO actions VALUES (?, ?, 'submit', '', 'SUBMITTING')",
                               (run_id, job_id))
            return True

    def finish_submission(self, run_id: str, job_id: str, *, verified: bool) -> None:
        """Record observed outcome; an uncertain attempt remains unverified."""
        status = JobStatus.SUBMITTED_VERIFIED if verified else JobStatus.SUBMITTED_UNVERIFIED
        with self._transaction() as connection:
            previous = connection.execute("SELECT status FROM applications WHERE run_id=? AND job_id=?",
                                          (run_id, job_id)).fetchone()
            if previous and previous[0] == JobStatus.SUBMITTED_VERIFIED:
                return
            result = connection.execute("UPDATE applications SET status=? WHERE run_id=? AND job_id=? "
                                        "AND status IN (?, ?, ?)",
                                        (status, run_id, job_id, JobStatus.SUBMITTING,
                                         JobStatus.SUBMITTED_UNVERIFIED, JobStatus.SUBMITTED_VERIFIED))
            if result.rowcount != 1:
                raise ValueError("no durable submission intent")
            if verified:
                connection.execute("UPDATE actions SET status='SUCCESS' WHERE run_id=? AND job_id=? "
                                   "AND action='submit'", (run_id, job_id))

    def append_event(self, run_id: str, job_id: str | None, event_id: str, message: str) -> int:
        """Persist a redacted timeline message, without arbitrary payloads or URLs."""
        message = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted email]", message)
        message = re.sub(r"(?i)(token|key|password|secret)\s*[:=]\s*\S+", r"\1=[redacted]", message)
        with self._transaction() as connection:
            cursor = connection.execute("INSERT INTO events(run_id,job_id,event_id,message,created_at) "
                                        "VALUES (?, ?, ?, ?, ?)",
                                        (run_id, job_id, event_id, message, self._now()))
            return int(cursor.lastrowid)
