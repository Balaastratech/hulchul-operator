"""Worker-facing routes W1-W6 (CONTROL_PLANE_API.md section 2).

Every route needs `Authorization: Bearer <CP_WORKER_TOKEN>` (constant-time compare) and lives
under `/api/worker/runs/{run_id}`. The worker only ever makes outbound calls; these routes are
the whole surface it uses:

- W1 GET  .../commands   read-only; writes nothing (not even a "last polled" stamp)
- W2 POST .../ack        idempotent
- W3 POST .../heartbeat  upserts the run
- W4 POST .../events                      (PROPOSED, additive)
- W5 POST .../jobs/{job_id}/snapshot      (PROPOSED, additive)
- W6 POST .../evidence                    (PROPOSED, additive)
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..models import Event, ReviewSnapshot
from ..store import Store
from ..tokens import ID_PATTERN, CpError, utc_iso

router = APIRouter(prefix="/api/worker")

T = TypeVar("T")

# Request size caps (section 1.2); exceeded -> 413 too_large.
MAX_SMALL_BYTES = 4 * 1024  # heartbeat, ack
MAX_EVENT_BYTES = 256 * 1024
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_EVIDENCE_REQUEST_BYTES = 3 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024  # decoded image

EVIDENCE_TTL_S = 48 * 3600  # section 9.3 default (CP_EVIDENCE_TTL_HOURS is not in Config yet)
EVIDENCE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

_STATUS = re.compile(r"^[A-Z_]{1,32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class BearerError(CpError):
    """401 with a `WWW-Authenticate: Bearer` header."""

    headers = {"WWW-Authenticate": "Bearer"}


# ------------------------------------------------------------------ helpers
def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _authenticate(request: Request) -> None:
    """Bearer check for every worker route (section 1.1 class (a))."""
    config = request.app.state.config
    expected = config.worker_token
    if expected is None:
        if config.dev_allow_unauth_worker:  # only ever true in CP_ENV=dev on loopback
            return
        raise BearerError("invalid_bearer", 401)  # fail closed
    header = request.headers.get("authorization")
    if not header:
        raise BearerError("missing_bearer", 401)
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise BearerError("invalid_bearer", 401)
    # Compare fixed-length digests so neither content nor length leaks through timing.
    if not hmac.compare_digest(_digest(presented.strip()), _digest(expected)):
        raise BearerError("invalid_bearer", 401)


def _check_ids(run_id: str, job_id: str | None = None) -> None:
    if not ID_PATTERN.match(run_id) or (job_id is not None and not ID_PATTERN.match(job_id)):
        raise CpError("not_found", 404)


def _reject_constant(name: str) -> Any:  # NaN / Infinity are not valid JSON
    raise ValueError(name)


async def _read_json(request: Request, cap: int) -> Any:
    """Read at most `cap` bytes (413 beyond) and parse strict JSON (400 if malformed)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise CpError("too_large", 413)
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > cap:
            raise CpError("too_large", 413)
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise CpError("bad_request", 400, "body is not valid JSON") from None


async def _json_object(request: Request, cap: int) -> dict[str, Any]:
    data = await _read_json(request, cap)
    if not isinstance(data, dict):
        raise CpError("bad_request", 400, "body must be a JSON object")
    return data


def _store(request: Request) -> Store:
    return request.app.state.store


async def _blocking(function: Callable[..., T], *args: Any) -> T:
    """SQLite calls run off the event loop."""
    return await run_in_threadpool(function, *args)


def _validation_detail(exc: ValidationError) -> str:
    locations = sorted({".".join(str(part) for part in err["loc"]) or "<body>" for err in exc.errors()})
    return "invalid fields: " + ", ".join(locations[:10])


# ------------------------------------------------------------------- W1 GET
@router.get("/runs/{run_id}/commands")
async def worker_commands(run_id: str, request: Request) -> JSONResponse:
    """W1: queued commands of this run plus the mandatory `approvals` sibling.

    Read-only by construction: `Store.list_queued_commands` only reads. An unseen run is a
    plain `200 {"commands": [], "approvals": {}}` (never 404, never an empty body).
    """
    _authenticate(request)
    _check_ids(run_id)
    commands, approvals = await _blocking(_store(request).list_queued_commands, run_id)
    return JSONResponse({"commands": commands, "approvals": approvals})


# ------------------------------------------------------------------ W2 ack
@router.post("/runs/{run_id}/ack")
async def worker_ack(run_id: str, request: Request) -> JSONResponse:
    """W2: idempotent acknowledgement; 200 for every command of this run, any status."""
    _authenticate(request)
    _check_ids(run_id)
    data = await _json_object(request, MAX_SMALL_BYTES)
    command_id = data.get("command_id")
    if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
        raise CpError("invalid_value", 422, "command_id is required")
    return JSONResponse(await _blocking(_store(request).ack, run_id, command_id))


# ------------------------------------------------------------ W3 heartbeat
@router.post("/runs/{run_id}/heartbeat")
async def worker_heartbeat(run_id: str, request: Request) -> JSONResponse:
    """W3: liveness; upserts the run row. Carries no candidate data and no capability."""
    _authenticate(request)
    _check_ids(run_id)
    data = await _json_object(request, MAX_SMALL_BYTES)
    body_run = data.get("run_id")
    if not isinstance(body_run, str):
        raise CpError("invalid_value", 422, "run_id is required")
    if body_run != run_id:
        raise CpError("run_mismatch", 400)
    status = data.get("status")
    if not isinstance(status, str) or not _STATUS.match(status):
        raise CpError("invalid_value", 422, "status must match ^[A-Z_]{1,32}$")
    store = _store(request)
    result = await _blocking(store.heartbeat, run_id, status)
    return JSONResponse(
        {
            "ok": True,
            "server_time": utc_iso(store.now()),
            "channel_unreachable": result["channel_unreachable"],
        }
    )


# --------------------------------------------------------------- W4 events
@router.post("/runs/{run_id}/events")
async def worker_event(run_id: str, request: Request) -> JSONResponse:
    """W4: store an Event idempotently; the CP mints every link itself (never the worker)."""
    _authenticate(request)
    _check_ids(run_id)
    data = await _json_object(request, MAX_EVENT_BYTES)
    if data.get("run_id") != run_id and isinstance(data.get("run_id"), str):
        raise CpError("run_mismatch", 400)
    try:
        event = Event.model_validate(data)
    except ValidationError as exc:
        raise CpError("schema_invalid", 422, _validation_detail(exc)) from None
    if event.run_id != run_id:
        raise CpError("run_mismatch", 400)
    if event.job_id is not None and not ID_PATTERN.match(event.job_id):
        raise CpError("invalid_value", 422, "job_id is not a valid id")
    store = _store(request)
    result = await _blocking(store.insert_event, event)
    telegram = "queued" if request.app.state.config.telegram_enabled else "disabled"
    return JSONResponse({**result, "delivery": {"web": "available", "telegram": telegram}})


# ------------------------------------------------------------- W5 snapshot
@router.post("/runs/{run_id}/jobs/{job_id}/snapshot")
async def worker_snapshot(run_id: str, job_id: str, request: Request) -> JSONResponse:
    """W5: `{"snapshot_hash", "snapshot"}`; the CP recomputes the hash and never trusts the claim.

    The snapshot is normalised through the model first (defaulted keys filled) and hashed with
    `ReviewSnapshot.content_hash()`, so a worker that omits defaulted keys still matches.
    """
    _authenticate(request)
    _check_ids(run_id, job_id)
    data = await _json_object(request, MAX_SNAPSHOT_BYTES)
    claimed = data.get("snapshot_hash")
    if not isinstance(claimed, str) or not _HEX64.match(claimed):
        raise CpError("schema_invalid", 422, "snapshot_hash must be 64 lowercase hex")
    raw_snapshot = data.get("snapshot")
    if not isinstance(raw_snapshot, dict):
        raise CpError("schema_invalid", 422, "snapshot must be an object")
    try:
        snapshot = ReviewSnapshot.model_validate(raw_snapshot)
    except ValidationError as exc:
        raise CpError("schema_invalid", 422, _validation_detail(exc)) from None
    result = await _blocking(_store(request).put_snapshot, run_id, job_id, claimed, snapshot)
    return JSONResponse(result)


# ------------------------------------------------------------- W6 evidence
def _looks_like(mime: str, data: bytes) -> bool:
    """Cheap magic-byte check so a stored file matches the mime the CP will later serve."""
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"  # image/webp


def _write_file(directory: Path, filename: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    temporary = directory / (filename + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target


def _store_evidence(
    store: Store,
    directory: Path,
    run_id: str,
    job_id: str,
    name: str,
    sha256: str,
    mime: str,
    data: bytes,
) -> dict:
    """Idempotent on (run, job, sha256); the run row is upserted (unknown-run rule)."""
    now = store.now()
    expires_at = utc_iso(now + EVIDENCE_TTL_S)
    written: Path | None = None
    try:
        with store._tx() as conn:  # same package; keeps row + file in one atomic unit
            store._ensure_run(conn, run_id, now)
            row = conn.execute(
                "SELECT evidence_id, path, expires_at FROM evidence "
                "WHERE run_id=? AND job_id=? AND sha256=?",
                (run_id, job_id, sha256),
            ).fetchone()
            if row is not None and row["expires_at"] > utc_iso(now):
                return {
                    "accepted": True,
                    "evidence_id": row["evidence_id"],
                    "duplicate": True,
                    "expires_at": row["expires_at"],
                }
            if row is not None:  # expired copy: refresh the TTL and the file, keep the id
                evidence_id, filename = row["evidence_id"], row["path"]
                conn.execute(
                    "UPDATE evidence SET expires_at=?, name=?, mime=? WHERE evidence_id=?",
                    (expires_at, name, mime, evidence_id),
                )
            else:
                evidence_id = "ev_" + secrets.token_hex(16)
                filename = evidence_id + EVIDENCE_EXT[mime]
                conn.execute(
                    "INSERT INTO evidence(evidence_id, run_id, job_id, name, sha256, mime, path, "
                    "expires_at) VALUES(?,?,?,?,?,?,?,?)",
                    (evidence_id, run_id, job_id, name, sha256, mime, filename, expires_at),
                )
            written = _write_file(directory, filename, data)
    except BaseException:
        if written is not None:
            # The transaction rolled back after the file was written: do not leave an orphan.
            written.unlink(missing_ok=True)
        raise
    return {
        "accepted": True,
        "evidence_id": evidence_id,
        "duplicate": False,
        "expires_at": expires_at,
    }


@router.post("/runs/{run_id}/evidence")
async def worker_evidence(run_id: str, request: Request) -> JSONResponse:
    """W6: a screenshot as base64 in JSON (the worker's transport can only send JSON)."""
    _authenticate(request)
    _check_ids(run_id)
    data = await _json_object(request, MAX_EVIDENCE_REQUEST_BYTES)
    job_id, name, sha256 = data.get("job_id"), data.get("name"), data.get("sha256")
    mime, content_b64 = data.get("mime"), data.get("content_b64")
    if not isinstance(job_id, str) or not ID_PATTERN.match(job_id):
        raise CpError("invalid_value", 422, "job_id is required")
    if not isinstance(name, str) or not _EVIDENCE_NAME.match(name):
        raise CpError("invalid_value", 422, "name is not a valid file label")
    if not isinstance(sha256, str) or not _HEX64.match(sha256):
        raise CpError("invalid_value", 422, "sha256 must be 64 lowercase hex")
    if mime not in EVIDENCE_EXT:
        raise CpError("unsupported_media_type", 422, "mime must be png, jpeg or webp")
    if not isinstance(content_b64, str):
        raise CpError("invalid_value", 422, "content_b64 is required")
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise CpError("invalid_value", 422, "content_b64 is not valid base64") from None
    if len(content) > MAX_EVIDENCE_BYTES:
        raise CpError("too_large", 413)
    if not content or not _looks_like(mime, content):
        raise CpError("invalid_value", 422, "content does not match the declared mime")
    if hashlib.sha256(content).hexdigest() != sha256:
        raise CpError("hash_mismatch", 422)
    result = await _blocking(
        _store_evidence,
        _store(request),
        request.app.state.evidence_dir,
        run_id,
        job_id,
        name,
        sha256,
        mime,
        content,
    )
    return JSONResponse(result)
