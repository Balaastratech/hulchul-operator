"""S5/S6 spike control plane (synthetic, NOT the real control plane).

Run (binds loopback only; the tunnel is the only way in):
    uvicorn app:app --app-dir control_plane/spikes/s5_s6 --host 127.0.0.1 --port 8781

Secrets: CP_SIGNING_KEY comes from the .env named by ENV_FILE (python-dotenv).
It is never printed or logged. The worker bearer token is random per start and
written to a temp file that the spike client reads and deletes.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import tokens

HERE = Path(__file__).resolve().parent
ENV_FILE = os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env")
LOG_PATH = Path(os.environ.get("SPIKE_REQUEST_LOG", HERE / "requests.jsonl"))
TMP = Path(tempfile.gettempdir())
DB_PATH = Path(os.environ.get("SPIKE_DB", TMP / "kiro_spike.db"))
WORKER_TOKEN_FILE = Path(os.environ.get("SPIKE_WORKER_TOKEN_FILE", TMP / "kiro_spike_worker_token.txt"))
MAX_ACTION_TTL = 600  # server refuses action tokens that live longer than 10 min

load_dotenv(ENV_FILE)
_key = os.environ.get("CP_SIGNING_KEY")
if not _key:
    sys.exit("CP_SIGNING_KEY missing from the environment / .env")
KEY = _key.encode()

WORKER_TOKEN = secrets.token_urlsafe(32)
WORKER_TOKEN_FILE.write_text(WORKER_TOKEN, encoding="ascii")

# Current snapshot per (run, job): the server-side truth tokens are bound to.
SNAPSHOTS = {("spike-run", f"job-{n}"): tokens.snapshot_hash("spike-run", f"job-{n}") for n in range(1, 10)}

_lock = threading.Lock()
STATE = {"state_changes": 0, "acks": 0, "heartbeats": 0}

if DB_PATH.exists():
    DB_PATH.unlink()
with sqlite3.connect(DB_PATH) as _c:
    _c.execute("CREATE TABLE used_tokens (nonce TEXT PRIMARY KEY, used_at REAL NOT NULL)")
    _c.execute("CREATE TABLE decisions (run TEXT, job TEXT, snapshot_hash TEXT, PRIMARY KEY (run, job, snapshot_hash))")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- request log
def _redact_ip(value: str | None) -> str | None:
    if not value:
        return None
    out = []
    for part in value.split(","):
        part = part.strip()
        if ":" in part:
            groups = part.split(":")
            out.append(":".join(groups[:2]) + ":x")
        else:
            octets = part.split(".")
            out.append(".".join(octets[:2] + ["x", "x"]) if len(octets) == 4 else "x")
    return ",".join(out)


def _redact_query(qs: str) -> str:
    pairs = []
    for k, v in parse_qsl(qs, keep_blank_values=True):
        pairs.append(f"{k}={v[:6]}..." if k == "t" else f"{k}={v}")
    return "&".join(pairs)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request.state.outcome = None
    response = await call_next(request)
    h = request.headers
    effective = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "")
    conn_host = request.client.host if request.client else ""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "method": request.method,
        "path": request.url.path,
        "query": _redact_query(request.url.query),
        "conn_local": conn_host in ("127.0.0.1", "::1"),
        "client_ip_redacted": _redact_ip(effective),
        "x_forwarded_for_redacted": _redact_ip(h.get("x-forwarded-for")),
        "cf_country": h.get("cf-ipcountry"),
        "user_agent": h.get("user-agent"),
        "accept": h.get("accept"),
        "auth_present": "authorization" in h,
        "status": response.status_code,
        "outcome": request.state.outcome,
    }
    with _lock, LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return response


# --------------------------------------------------------------------- pages
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/_spike/state")
def spike_state():
    with sqlite3.connect(DB_PATH) as c:
        used = c.execute("SELECT COUNT(*) FROM used_tokens").fetchone()[0]
        decided = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    return {**STATE, "used_tokens": used, "decisions": decided}


def _plain(request: Request, code: int, outcome: str, text: str):
    request.state.outcome = outcome
    return PlainTextResponse(text, status_code=code, headers={"Cache-Control": "no-store"})


@app.get("/r/{run}/{job}")
def review_page(run: str, job: str, request: Request, t: str = ""):
    """Read-only review stub. Renders; never mutates server state."""
    try:
        view = tokens.verify(KEY, t)
    except tokens.TokenError as e:
        return _plain(request, e.status, f"view_{e.code}", "invalid link")
    if view.get("typ") != "view" or view.get("run") != run or view.get("job") != job:
        return _plain(request, 403, "view_wrong_scope", "invalid link")
    if int(view.get("exp", 0)) < time.time():
        return _plain(request, 410, "view_expired", "link expired")
    current = SNAPSHOTS.get((run, job))
    if current is None:
        return _plain(request, 403, "view_wrong_scope", "invalid link")
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    head = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SPIKE review</title></head><body style='font-family:sans-serif;max-width:32rem;margin:1rem auto'>")
    if view.get("snapshot_hash") != current:
        request.state.outcome = "view_stale_no_form"
        return HTMLResponse(head + "<h1>SPIKE review</h1><p>This snapshot changed. No approve button is offered.</p></body></html>",
                            headers=headers)
    ttl = min(int(view.get("ttl", 300)), MAX_ACTION_TTL)
    action = tokens.mint_action(KEY, run, job, current, ttl=ttl)  # stateless: minting changes nothing
    request.state.outcome = "view_ok"
    body = (f"<h1>SPIKE review</h1><p>Run <b>{html.escape(run)}</b>, job <b>{html.escape(job)}</b>.</p>"
            f"<p>Snapshot <code>{current[:12]}</code>. Harmless test: nothing is submitted.</p>"
            "<form method='post' action='/api/approve'>"
            f"<input type='hidden' name='token' value='{html.escape(action)}'>"
            f"<input type='hidden' name='run' value='{html.escape(run)}'>"
            f"<input type='hidden' name='job' value='{html.escape(job)}'>"
            "<button type='submit' style='font-size:1.6rem;padding:1rem 2rem;width:100%'>APPROVE (SPIKE)</button>"
            f"</form><p>Action token valid {ttl} s, single use.</p></body></html>")
    return HTMLResponse(head + body, headers=headers)


@app.post("/api/approve")
async def approve(request: Request):
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    try:
        if "json" in ctype:
            data = json.loads(raw or b"{}")
        else:
            data = {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}
    except Exception:
        data = {}
    wants_json = "json" in ctype

    def out(code: int, outcome: str, msg: str):
        request.state.outcome = outcome
        if wants_json:
            return JSONResponse({"ok": code == 200, "reason": outcome, "message": msg}, status_code=code,
                                headers={"Cache-Control": "no-store"})
        return HTMLResponse(f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                            f"<body style='font-family:sans-serif;max-width:32rem;margin:1rem auto'><h1>{html.escape(msg)}</h1>"
                            f"<p>reason: {html.escape(outcome)}</p></body>", status_code=code,
                            headers={"Cache-Control": "no-store"})

    token = str(data.get("token", ""))
    try:
        p = tokens.verify(KEY, token)
    except tokens.TokenError as e:
        return out(e.status, e.code, "rejected")
    if p.get("typ") != "action" or p.get("action") != "approve":
        return out(403, "wrong_typ", "rejected")
    run, job = p.get("run"), p.get("job")
    if (run, job) != (data.get("run"), data.get("job")) or (run, job) not in SNAPSHOTS:
        return out(403, "wrong_scope", "rejected")
    now = time.time()
    exp = p.get("exp")
    if not isinstance(exp, (int, float)) or exp - now > MAX_ACTION_TTL + 5:
        return out(403, "exp_too_far", "rejected")
    if exp < now:
        return out(410, "expired", "token expired")
    if p.get("snapshot_hash") != SNAPSHOTS[(run, job)]:
        return out(409, "stale_snapshot", "snapshot changed, token void")
    nonce = p.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 32:
        return out(403, "bad_nonce", "rejected")

    # Atomic single-use: unique violation on the nonce = replay; one decision per snapshot.
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO used_tokens(nonce, used_at) VALUES (?, ?)", (nonce, now))
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return out(409, "replay", "token already used")
        try:
            conn.execute("INSERT INTO decisions(run, job, snapshot_hash) VALUES (?,?,?)",
                         (run, job, p["snapshot_hash"]))
        except sqlite3.IntegrityError:
            conn.execute("COMMIT")  # the token is spent, but no second decision is recorded
            return out(409, "already_decided", "this snapshot was already approved")
        conn.execute("COMMIT")
    finally:
        conn.close()
    with _lock:
        STATE["state_changes"] += 1
    return out(200, "ok", "approved (SPIKE, nothing was submitted)")


# -------------------------------------------------------------- worker (outbound-only client)
def _worker_auth(request: Request):
    got = request.headers.get("authorization", "")
    expect = f"Bearer {WORKER_TOKEN}"
    if not secrets.compare_digest(got.encode(), expect.encode()):
        request.state.outcome = "worker_unauthorized"
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return None


@app.get("/worker/commands")
def worker_commands(request: Request):
    denied = _worker_auth(request)
    if denied:
        return denied
    request.state.outcome = "worker_ok"
    return {"commands": [{"command_id": "cmd-spike-1", "run_id": "spike-run", "type": "noop"}]}


@app.post("/worker/ack")
async def worker_ack(request: Request):
    denied = _worker_auth(request)
    if denied:
        return denied
    body = await request.json()
    if not isinstance(body, dict) or "command_id" not in body:
        return JSONResponse({"error": "bad request"}, status_code=400)
    with _lock:
        STATE["acks"] += 1
    request.state.outcome = "worker_ok"
    return {"ok": True}


@app.post("/worker/heartbeat")
async def worker_heartbeat(request: Request):
    denied = _worker_auth(request)
    if denied:
        return denied
    body = await request.json()
    if not isinstance(body, dict) or "run_id" not in body or "status" not in body:
        return JSONResponse({"error": "bad request"}, status_code=400)
    with _lock:
        STATE["heartbeats"] += 1
    request.state.outcome = "worker_ok"
    return {"ok": True}
