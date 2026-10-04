"""Human-facing routes (CONTROL_PLANE_API.md sections 4.1, 4.2, 5.4, 5.5).

Pages (GET/HEAD, read-only):
    /r/{run}?t=<view token>          run summary, jobs, timeline, pause/resume/cancel forms
    /r/{run}/{job}?t=<view token>    review page: intended vs read-back, flags, gates, forms

POST actions (token in the BODY, never in a URL): approve, edit, reject, skip, answer,
handoff_done (single-use `act` tokens) and pause, resume, cancel (`run` tokens).

GET never writes: pages only call Store read helpers and *mint* tokens, and minting is pure
(tokens.py). Every state change happens in Store.consume_act / Store.queue_run_command, which
create the Command row in the same transaction as the token consume.

Pages are rendered with Jinja2 (autoescape on, no inline script, nonce'd style block, strict CSP).
Nothing here logs a token, a request body or a URL with `?t=`; only `tid` (nonce prefix).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.concurrency import run_in_threadpool

from src.operator.channels.questions import (
    InvalidAnswer,
    coerce_value,
    control_kind,
    field_display,
    group_siblings,
    option_label,
    parse_key,
    question_id,
    question_name,
)

from ..models import ReviewSnapshot
from ..store import Store
from ..tokens import (
    ID_PATTERN,
    MAX_LIFETIME_S,
    Claims,
    CpError,
    TokenService,
    token_hash,
    utc_iso,
)

log = logging.getLogger("control_plane.human")

router = APIRouter()

MAX_BODY_BYTES = 128 * 1024  # edit values are <= 64 KB JSON; room for the envelope
MAX_FORM_FIELDS = 20
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TIMELINE_LIMIT = 50
DISPLAY_LIMIT = 2000

ACT_ROUTES = ("approve", "edit", "reject", "skip", "answer", "handoff_done")
RUN_ROUTES = ("pause", "resume", "cancel")
_CSRF_SAME_SITE = frozenset({"same-origin", "none"})

_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,  # every template, not just *.html
    undefined=StrictUndefined,
)


def _show(value: Any, limit: int = DISPLAY_LIMIT) -> str:
    """Display text for a JSON value; the template engine escapes it on output."""
    if value is None or value == "":
        return "(empty)"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "..."


def _prefill(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _hhmm(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%H:%M UTC")


def _short(value: str | None) -> str:
    return value[:8] if value else "-"


def _questions(keys: Any) -> list[str]:
    """Human names of field keys, one per QUESTION (a radio group or duplicate upload once)."""
    seen: set[tuple] = set()
    names: list[str] = []
    for key in keys or []:
        if question_id(key) not in seen:
            seen.add(question_id(key))
            names.append(question_name(key))
    return names


# T-047: the page never shows internal field keys (`label|type|group|index`), only the
# question or field label.
_ENV.filters.update(
    show=_show, prefill=_prefill, hhmm=_hhmm, short=_short,
    question=question_name, field_label=field_display, questions=_questions,
)

_GATE_TITLES = {
    "ask": "Your answer is needed",
    "handoff": "Action needed in the browser",
    "shortlist": "Shortlisted role",
}

_MESSAGES = {
    "invalid_token": "This link or form is not valid. Open the latest link from the bot.",
    "token_expired": "This link or form has expired. Ask the bot for a fresh link, or reload "
    "the review page if you are still signed in with a valid link.",
    "forbidden": "This form does not match this action, run or job.",
    "bad_origin": "The request did not come from this site.",
    "not_found": "Nothing was found for this link.",
    "link_expired": "This link has expired or is not valid. Send /status to the bot for a fresh one.",
    "token_replayed": "This form was already used. Nothing was changed.",
    "stale_snapshot": "The read-back changed after this page was loaded. Reload the page and "
    "review it again.",
    "no_open_gate": "That question or hand-off is no longer open. Reload the page.",
    "edit_pending": "An edit is still being applied. Wait for the new read-back, then reload.",
    "already_approved": "This read-back is already approved. Waiting for the worker.",
    "command_pending": "Another decision for this job is still waiting for the worker. "
    "Try again shortly.",
    "run_terminal": "This run has ended.",
    "bad_request": "The request was incomplete or too large.",
    "unsupported_media_type": "Unsupported content type.",
    "invalid_value": "That value is not acceptable.",
    "too_large": "The request is too large.",
}
_SUCCESS = {
    "approve": "Approved. The worker will submit after it picks this up.",
    "edit": "Edit sent. Wait for the new read-back, then review again.",
    "reject": "Rejected. The worker will not submit this application.",
    "skip": "Skipped.",
    "answer": "Answer sent.",
    "handoff_done": "Thanks. The worker will look at the page again.",
    "pause": "Pause requested.",
    "resume": "Resume requested.",
    "cancel": "Cancel requested. No application will be sent.",
}


# ------------------------------------------------------------------ rendering
def _render(template: str, http_status: int, **context: Any) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    body = _ENV.get_template(template).render(nonce=nonce, **context)
    headers = {
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"style-src 'nonce-{nonce}'; "
            "img-src 'self'; "
            "form-action 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'"
        ),
        "Cache-Control": "no-store",
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
    return HTMLResponse(body, status_code=http_status, headers=headers)


def _error_page(exc: CpError) -> HTMLResponse:
    return _render(
        "error.html",
        exc.status,
        status=exc.status,
        code=exc.code,
        message=_MESSAGES.get(exc.code, "The request could not be completed."),
    )


# ---------------------------------------------------------------------- reads
def _loads(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _authorize_view(request: Request, run_id: str, job_id: str | None) -> Claims:
    """View token from `?t=`: a missing/invalid/wrong-typ token is 401, a token for another
    run/job is 403, an expired one is 410 (section 4.1)."""
    if not ID_PATTERN.match(run_id) or (job_id is not None and not ID_PATTERN.match(job_id)):
        raise CpError("not_found", 404)
    presented = request.query_params.get("t")
    if not presented:
        raise CpError("invalid_token", 401)
    claims = request.app.state.tokens.verify(presented, "view")
    if claims.run != run_id:
        raise CpError("forbidden", 403)
    if job_id is None:
        if claims.job is not None:  # a job-level token may not open the run page
            raise CpError("forbidden", 403)
    elif claims.job not in (None, job_id):
        raise CpError("forbidden", 403)
    return claims


def _run_data(store: Store, run_id: str) -> dict:
    with store._read() as conn:
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise CpError("not_found", 404)
        jobs = conn.execute(
            "SELECT j.job_id, j.review_state, j.gate_kind, "
            "(SELECT s.snapshot_hash FROM snapshots s WHERE s.run_id=j.run_id AND "
            " s.job_id=j.job_id AND s.status='current') AS current_hash "
            "FROM jobs j WHERE j.run_id=? ORDER BY j.job_id",
            (run_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT seq, event_id, job_id, body_json FROM events WHERE run_id=? "
            "ORDER BY seq DESC LIMIT ?",
            (run_id, TIMELINE_LIMIT),
        ).fetchall()
        queued = conn.execute(
            "SELECT COUNT(*) AS n FROM commands WHERE run_id=? AND status='queued'", (run_id,)
        ).fetchone()["n"]
    timeline = []
    for row in events:
        body = _loads(row["body_json"])
        timeline.append(
            {
                "seq": row["seq"],
                "event_id": row["event_id"],
                "job_id": row["job_id"],
                "message": body.get("message", ""),
                "created_at": body.get("created_at", ""),
            }
        )
    return {"run": dict(run), "jobs": [dict(j) for j in jobs], "timeline": timeline, "queued": queued}


def _gate_details(kind: str, body: dict, job_id: str) -> list[tuple[str, Any]]:
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    details: list[tuple[str, Any]] = []
    if kind == "ask":
        asked = payload.get("label") or payload.get("question") or payload.get("field_key")
        if isinstance(asked, str) and asked:
            details.append(("question", question_name(asked)))
        for key in ("why", "suggestions"):
            if key in payload:
                details.append((key, payload[key]))
    elif kind == "handoff":
        for key in ("reason", "site", "observed", "page_kind"):
            if key in payload:
                details.append((key, payload[key]))
    elif kind == "shortlist":
        for item in payload.get("chosen") or []:
            if isinstance(item, dict) and item.get("job_id") == job_id:
                for key in ("title", "company", "reasons"):
                    if key in item:
                        details.append((key, item[key]))
    return details


def _job_data(store: Store, run_id: str, job_id: str) -> dict:
    now_iso = utc_iso(store.now())
    with store._read() as conn:
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        job = conn.execute(
            "SELECT * FROM jobs WHERE run_id=? AND job_id=?", (run_id, job_id)
        ).fetchone()
        if run is None or job is None:
            raise CpError("not_found", 404)
        snap = conn.execute(
            "SELECT snapshot_hash, body_json FROM snapshots "
            "WHERE run_id=? AND job_id=? AND status='current'",
            (run_id, job_id),
        ).fetchone()
        evidence = conn.execute(
            "SELECT evidence_id, name FROM evidence WHERE run_id=? AND job_id=? "
            "AND expires_at > ? ORDER BY rowid ASC",
            (run_id, job_id, now_iso),
        ).fetchall()
        gate_event = None
        if job["gate_kind"] != "none" and job["gate_hash"]:
            gate_event = conn.execute(
                "SELECT event_id, body_json FROM events WHERE run_id=? AND dedup_key=?",
                (run_id, job["gate_hash"]),
            ).fetchone()
        queued = conn.execute(
            "SELECT COUNT(*) AS n FROM commands WHERE run_id=? AND job_id=? AND status='queued'",
            (run_id, job_id),
        ).fetchone()["n"]
    gate_body = _loads(gate_event["body_json"]) if gate_event else {}
    snapshot = ReviewSnapshot.model_validate(_loads(snap["body_json"])) if snap else None
    return {
        "run": dict(run),
        "job": dict(job),
        "snapshot": snapshot,
        "snapshot_hash": snap["snapshot_hash"] if snap else None,
        "evidence": [dict(e) for e in evidence],
        "gate_event_id": gate_event["event_id"] if gate_event else None,
        "gate_message": gate_body.get("message", ""),
        "gate_details": _gate_details(job["gate_kind"], gate_body, job_id),
        "gate_payload": gate_body.get("payload") if isinstance(gate_body.get("payload"), dict) else {},
        "queued": queued,
    }


def _act_form(tokens: TokenService, run: str, job: str, action: str, bound: str,
              field_key: str | None = None) -> dict:
    """A freshly minted (pure) act token plus its displayed expiry."""
    token = tokens.mint("act", run, job=job, action=action, snapshot_hash=bound, field_key=field_key)
    return {"token": token, "expires": tokens.now() + MAX_LIFETIME_S["act"], "field_key": field_key}


def _edit_rows(snapshot: ReviewSnapshot, tokens: TokenService, run_id: str, job_id: str,
               snap_hash: str | None, can_edit: bool) -> list[dict]:
    """One row per QUESTION for the read-back table, each with the control that edits it.

    A radio/checkbox group is one row with one control per option (every option keeps its own
    single-use edit token bound to its own field key); duplicate inputs of a group never
    repeat. Other fields keep one row: a text box, textarea, or a checkbox / select-this button
    chosen from the type segment of the field key. The row never shows the raw key.
    """

    def form(key: str) -> dict | None:
        if can_edit and snap_hash:
            return _act_form(tokens, run_id, job_id, "edit", snap_hash, key)
        return None

    rows: list[dict] = []
    groups: dict[tuple, dict] = {}
    for item in snapshot.fields:
        key = item.field_key
        parsed = parse_key(key)
        if parsed is not None and parsed.is_choice_group:
            row = groups.get(question_id(key))
            if row is None:
                row = {
                    "control": parsed.type, "name": question_name(key), "options": [],
                    "intended": [], "actual": [], "matched": True, "escalated": False,
                    "generated": False, "derived": False, "source": None, "reason": None,
                    "has_intended": False, "has_actual": False,
                }
                groups[question_id(key)] = row
                rows.append(row)
            label = option_label(key)
            selected = item.actual is True
            row["options"].append(
                {"label": label, "field_key": key, "selected": selected, "edit": form(key)}
            )
            if item.intended is True:
                row["intended"].append(label)
            if selected:
                row["actual"].append(label)
            row["matched"] = row["matched"] and (item.intended is None or item.matched)
            row["has_intended"] = row["has_intended"] or item.intended is not None
            row["has_actual"] = row["has_actual"] or selected
            row["escalated"] = row["escalated"] or item.escalated
            row["generated"] = row["generated"] or item.generated
            row["derived"] = row["derived"] or getattr(item, "derived", False)
            if getattr(item, "source", None) and not row["source"]:
                row["source"] = getattr(item, "source", None)
            row["reason"] = row["reason"] or item.reason
            continue
        control = control_kind(key)
        if control == "text" and parsed is not None and parsed.type == "textarea":
            control = "textarea"
        rows.append({
            "control": control, "name": field_display(key), "field_key": key,
            "intended": _show(item.intended), "actual": _show(item.actual),
            "matched": item.matched, "escalated": item.escalated, "generated": item.generated,
            "has_intended": item.intended is not None,
            "has_actual": item.actual not in (None, "", False, []),
            "derived": getattr(item, "derived", False), "source": getattr(item, "source", None),
            "reason": item.reason, "edit": form(key), "checked": item.actual is True,
            "prefill": _prefill(item.intended),
        })
    for row in groups.values():  # a group reads as the chosen option labels
        row["intended"] = ", ".join(row["intended"]) or "(empty)"
        row["actual"] = ", ".join(row["actual"]) or "(empty)"
    return rows


def _gate_control(field_key: str, payload: dict, snapshot: ReviewSnapshot | None) -> dict | None:
    """The control that answers an ask gate, from the field key's type segment.

    radio -> a button for the asked option; checkbox -> a checkbox; select -> a dropdown when the
    event carries its options, else text. Returns None for a plain text box. Sibling options of
    a radio group (from the snapshot, when one exists) are listed only as information: the graph
    asks about one field key at a time and a radio can only be selected, so only the asked
    option can be submitted here.
    """
    raw = payload.get("options")
    options = [o for o in raw if isinstance(o, str) and o][:50] if isinstance(raw, list) else []
    kind = control_kind(field_key, options)
    if kind == "text":
        return None
    parsed = parse_key(field_key)
    control: dict = {"kind": kind, "question": question_name(field_key), "options": options}
    if kind in ("radio", "checkbox"):
        grouped = parsed is not None and parsed.is_choice_group
        control["asked"] = option_label(field_key) if grouped else "Yes"
        siblings = group_siblings(
            field_key, [f.field_key for f in snapshot.fields] if snapshot is not None else []
        )
        control["others"] = [option_label(k) for k in siblings if k != field_key]
    return control


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


# ---------------------------------------------------------------------- pages
@router.api_route("/r/{run_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def run_page(run_id: str, request: Request) -> Response:
    try:
        claims = _authorize_view(request, run_id, None)
        tokens: TokenService = request.app.state.tokens
        data = _run_data(request.app.state.store, run_id)
    except CpError as exc:
        return _error_page(exc)
    view_t = request.query_params["t"]
    for job in data["jobs"]:
        job["href"] = f"/r/{quote(run_id, safe='')}/{quote(job['job_id'], safe='')}?t={quote(view_t, safe='')}"
    run = data["run"]
    controls = None
    if not run["terminal"]:
        controls = {
            action: {
                "token": tokens.mint("run", claims.run),
                "expires": tokens.now() + MAX_LIFETIME_S["run"],
            }
            for action in RUN_ROUTES
        }
    return _render(
        "run.html",
        200,
        run_id=run_id,
        run=run,
        jobs=data["jobs"],
        timeline=data["timeline"],
        queued=data["queued"],
        controls=controls,
    )


@router.api_route("/r/{run_id}/{job_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def job_page(run_id: str, job_id: str, request: Request) -> Response:
    try:
        claims = _authorize_view(request, run_id, job_id)
        tokens: TokenService = request.app.state.tokens
        data = _job_data(request.app.state.store, run_id, job_id)
    except CpError as exc:
        return _error_page(exc)

    job, run, snapshot = data["job"], data["run"], data["snapshot"]
    snap_hash = data["snapshot_hash"]
    state = job["review_state"]
    live = not run["terminal"]
    if snapshot is None:
        snapshot_status = "none"
    elif state == "edit_pending":
        snapshot_status = "stale (an edit is pending; waiting for a fresh read-back)"
    else:
        snapshot_status = "current"

    approval_expired = (
        live and state == "approved"
        and request.app.state.store.approval_expired(run_id, job_id)
    )
    can_decide = live and snapshot is not None and (
        state not in ("edit_pending", "approved", "rejected") or approval_expired
    )
    can_edit = live and snapshot is not None and state in ("ready", "edit_pending")
    approve = reject = None
    if can_decide and snap_hash:
        approve = _act_form(tokens, run_id, job_id, "approve", snap_hash)
        reject = _act_form(tokens, run_id, job_id, "reject", snap_hash)

    fields = (
        _edit_rows(snapshot, tokens, run_id, job_id, snap_hash, can_edit)
        if snapshot is not None
        else []
    )

    evidence_by_name = {e["name"]: e["evidence_id"] for e in data["evidence"]}
    shots = []
    for entry in snapshot.screenshots if snapshot is not None else []:
        name = _basename(entry)
        evidence_id = evidence_by_name.get(name)
        href = None
        if evidence_id:
            evd = tokens.mint("evd", run_id, job=job_id, field_key=evidence_id)
            href = f"/evidence/{quote(evidence_id, safe='')}?t={quote(evd, safe='')}"
        shots.append({"name": name, "href": href})

    gate = None
    gate_form = None
    gate_control = None
    kind = job["gate_kind"]
    if live and kind != "none" and job["gate_hash"]:
        gate = {
            "kind": kind,
            "title": _GATE_TITLES.get(kind, "Needs you"),
            "event_id": data["gate_event_id"],
            "message": data["gate_message"],
            "details": data["gate_details"],
        }
        action = {"ask": "answer", "handoff": "handoff_done", "shortlist": "skip"}[kind]
        if kind != "ask" or job["gate_field_key"]:  # an ask gate needs its field_key
            gate_form = _act_form(
                tokens, run_id, job_id, action, job["gate_hash"],
                job["gate_field_key"] if kind == "ask" else None,
            )
            gate_form["action"] = action
            if kind == "ask":
                gate_control = _gate_control(job["gate_field_key"], data["gate_payload"], snapshot)

    run_href = None
    if claims.job is None:
        run_href = f"/r/{quote(run_id, safe='')}?t={quote(request.query_params['t'], safe='')}"
    return _render(
        "job.html",
        200,
        run_id=run_id,
        job_id=job_id,
        run=run,
        job=job,
        snapshot=snapshot,
        snapshot_hash=snap_hash,
        snapshot_status=snapshot_status,
        fields=fields,
        approve=approve,
        reject=reject,
        shots=shots,
        gate=gate,
        gate_form=gate_form,
        gate_control=gate_control,
        queued=data["queued"],
        run_href=run_href,
        approval_expired=approval_expired,
    )


# --------------------------------------------------------------- short links
def _resolve_short(request: Request, code: str) -> tuple[str, str | None, int]:
    """Blocking, read-only: which run/job does this code open, and until when."""
    tokens: TokenService = request.app.state.tokens
    match = tokens.match_short_code(code, request.app.state.store.short_link_targets())
    if match is None:
        raise CpError("link_expired", 404)
    return match


@router.api_route("/s/{code}", methods=["GET", "HEAD"])
async def short_link(code: str, request: Request) -> Response:
    """Opaque chat link -> review page. GET only reads: it mints a fresh (pure) VIEW token whose
    life cannot outlast the short link, then redirects. It can never approve anything."""
    try:
        run_id, job_id, expiry = await run_in_threadpool(_resolve_short, request, code)
    except CpError as exc:
        return _error_page(exc)
    tokens: TokenService = request.app.state.tokens
    ttl = max(1, min(MAX_LIFETIME_S["view"], expiry - tokens.now()))
    view = tokens.mint("view", run_id, job=job_id, ttl=ttl)
    path = f"/r/{quote(run_id, safe='')}" + (f"/{quote(job_id, safe='')}" if job_id else "")
    return Response(
        status_code=302,
        headers={
            "Location": f"{path}?t={quote(view, safe='')}",
            "Cache-Control": "no-store",
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ------------------------------------------------------------------ evidence
_EVIDENCE_ID = re.compile(r"^ev_[0-9a-f]{32}$")
_EVIDENCE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})


def _read_evidence(store: Store, directory: Path, claims: Claims, evidence_id: str) -> tuple[bytes, str]:
    """Blocking read for GET /evidence: row lookup, scope check, contained file read."""
    with store._read() as conn:
        row = conn.execute(
            "SELECT run_id, job_id, mime, path, expires_at FROM evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
    # One answer for unknown / other run / other job / expired: no oracle for guessing ids.
    if (
        row is None
        or row["run_id"] != claims.run
        or row["job_id"] != claims.job
        or row["expires_at"] <= utc_iso(store.now())
        or row["mime"] not in _EVIDENCE_MIMES
    ):
        raise CpError("not_found", 404)
    root = directory.resolve()
    target = (root / row["path"]).resolve()
    if target.parent != root:  # the stored name is a bare generated filename, never a path
        raise CpError("not_found", 404)
    try:
        return target.read_bytes(), row["mime"]
    except OSError:
        raise CpError("not_found", 404) from None


@router.api_route("/evidence/{evidence_id}", methods=["GET", "HEAD"])
async def evidence_file(evidence_id: str, request: Request) -> Response:
    """AUDIT-026: serve a stored screenshot to the holder of a scoped `evd` capability.

    The token is bound to one run, one job and this exact evidence id. Read-only: nothing is
    written on GET. Served as a download-safe image with no sniffing, no caching and a
    sandboxing CSP, so a stored file can never run as a page."""
    if not _EVIDENCE_ID.match(evidence_id):
        return _json_error(CpError("not_found", 404))
    presented = request.query_params.get("t")
    if not presented:
        return _json_error(CpError("invalid_token", 401))
    try:
        claims = request.app.state.tokens.verify(presented, "evd")
        if claims.field_key != evidence_id or claims.job is None:
            raise CpError("forbidden", 403)
        data, mime = await run_in_threadpool(
            _read_evidence, request.app.state.store, request.app.state.evidence_dir,
            claims, evidence_id,
        )
    except CpError as exc:
        return _json_error(exc)
    return Response(
        data if request.method == "GET" else b"",
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Disposition": "inline",
            "Content-Length": str(len(data)),
        },
    )


def _json_error(exc: CpError) -> JSONResponse:
    return JSONResponse({"error": exc.code, "detail": exc.detail}, status_code=exc.status)


# ----------------------------------------------------------------- POST side
def _check_origin(request: Request) -> None:
    """CSRF defence (section 4.2 rules 1-2); non-browser clients send neither header."""
    origin = request.headers.get("origin")
    if origin is not None:
        if origin != request.app.state.config.base_origin:
            raise CpError("bad_origin", 403)
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in _CSRF_SAME_SITE:
        raise CpError("bad_origin", 403)


def _content_kind(request: Request) -> str | None:
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media == "application/json":
        return "json"
    if media == "application/x-www-form-urlencoded":
        return "form"
    return None


async def _read_body(request: Request, kind: str) -> dict[str, Any]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise CpError("too_large", 413)
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY_BYTES:
            raise CpError("too_large", 413)
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        text = raw.decode("utf-8")
        if kind == "json":
            data = json.loads(text, parse_constant=_reject_constant)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        pairs = parse_qs(text, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS)
    except (UnicodeDecodeError, ValueError):
        raise CpError("bad_request", 400, "body is malformed") from None
    if any(len(values) != 1 for values in pairs.values()):
        raise CpError("bad_request", 400, "duplicate form field")
    return {key: values[0] for key, values in pairs.items()}


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CpError("bad_request", 400, f"{key} must be a string")
    return value


def _perform(request: Request, action: str, claims: Claims, token: str, body: dict[str, Any]) -> dict:
    """Blocking part: binding checks (step 5) and the atomic store call (steps 6-10)."""
    store: Store = request.app.state.store
    run_id, job_id = _opt_str(body, "run_id"), _opt_str(body, "job_id")
    if action in RUN_ROUTES:
        TokenService.check_bindings(claims, action="control", run_id=run_id)
        return store.queue_run_command(claims.run, action)
    field_key = None
    value: Any = None
    if action in ("edit", "answer"):
        field_key = body.get("field_key")
        if not isinstance(field_key, str) or not field_key:
            raise CpError("bad_request", 400, "field_key is required")
        if "value" not in body:
            raise CpError("bad_request", 400, "value is required")
        try:
            # Radio/checkbox answers are the booleans the graph requires (RT-09); a radio can
            # only be selected. Other controls keep their text.
            value = coerce_value(field_key, body["value"])
        except InvalidAnswer as exc:
            raise CpError("invalid_value", 422, str(exc)) from None
    TokenService.check_bindings(
        claims, action=action, field_key=field_key, run_id=run_id, job_id=job_id
    )
    return store.consume_act(claims, token_hash(token), value=value)


async def _post_action(request: Request, action: str) -> Response:
    kind = _content_kind(request)
    claims: Claims | None = None
    try:
        _check_origin(request)
        if kind is None:
            raise CpError("unsupported_media_type", 415)
        body = await _read_body(request, kind)
        if (
            kind == "form"
            and action in ("edit", "answer")
            and "value" not in body
            and control_kind(body.get("field_key")) == "checkbox"
        ):
            body["value"] = "false"  # an unticked HTML checkbox posts nothing at all
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise CpError("bad_request", 400, "token is required")
        typ = "run" if action in RUN_ROUTES else "act"
        claims = request.app.state.tokens.verify(token, typ)  # 401 before 410, no DB
        result = await run_in_threadpool(_perform, request, action, claims, token, body)
    except CpError as exc:
        _log(action, claims, exc.code)
        if kind == "form":
            return _error_page(exc)
        return JSONResponse({"error": exc.code, "detail": exc.detail}, status_code=exc.status)
    _log(action, claims, "ok")
    if kind == "form":
        return _render(
            "result.html",
            200,
            ok=True,
            action=action,
            message=_SUCCESS[action],
            duplicate=bool(result.get("duplicate")),
        )
    return JSONResponse(result)


def _log(action: str, claims: Claims | None, outcome: str) -> None:
    # Only non-secret identifiers: never the token, body or URL.
    log.info(
        "human_post action=%s outcome=%s typ=%s run=%s job=%s tid=%s",
        action,
        outcome,
        claims.typ if claims else "-",
        claims.run if claims else "-",
        claims.job if claims else "-",
        claims.tid if claims else "-",
    )


def _register(action: str) -> None:
    async def endpoint(request: Request) -> Response:
        return await _post_action(request, action)

    router.add_api_route(
        f"/api/{action}",
        endpoint,
        methods=["POST"],
        name=f"post_{action}",
        response_class=JSONResponse,
    )


for _action in (*ACT_ROUTES, *RUN_ROUTES):
    _register(_action)
