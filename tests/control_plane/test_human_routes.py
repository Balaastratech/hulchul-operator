"""Human routes: review pages and POST actions (CONTROL_PLANE_API.md sections 4.1, 4.2, 5.4, 5.5).

No network, no real .env: the signing key and worker bearer are generated per test and the
database is a temp SQLite file. A controllable clock drives expiry.
"""
from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from markupsafe import escape
from starlette.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import TokenService, token_hash

RUN = "run_alpha"
OTHER_RUN = "run_beta"
JOB = "job_1"
JOB2 = "job_2"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "job_board_hostile"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Clock:
    def __init__(self) -> None:
        self.t = float(int(time.time()))

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------- fixtures
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
    )  # explicit environ: no .env file is read
    store = Store(config.db_path, clock)
    app = create_app(config, store=store, clock=clock)
    tokens = TokenService(config.signing_key, config.signing_key_previous, clock)
    with TestClient(app) as client:
        yield SimpleNamespace(
            config=config, store=store, app=app, client=client, tokens=tokens, clock=clock,
            auth={"Authorization": f"Bearer {config.worker_token}"},
        )
    store.close()


# ----------------------------------------------------------------- helpers
class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.tags: list[str] = []
        self.attrs: set[str] = set()
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.update(name for name, _ in attrs)
        a = dict(attrs)
        if tag == "form":
            self._cur = {"action": a.get("action"), "method": a.get("method"), "fields": {}}
            self.forms.append(self._cur)
        elif tag in ("input", "textarea") and self._cur is not None and a.get("name"):
            self._cur["fields"][a["name"]] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._cur = None


def parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    return page


def forms_for(html: str, action: str) -> list[dict[str, str]]:
    return [f["fields"] for f in parse(html).forms if f["action"] == f"/api/{action}"]


def snapshot_dict(value: str = "a@example.com", **extra) -> dict:
    base = {"fields": [{"field_key": "email", "intended": value, "actual": value, "matched": True}]}
    base.update(extra)
    return base


def put_snapshot(cp, job: str = JOB, snapshot: dict | None = None, run: str = RUN) -> str:
    snapshot = snapshot or snapshot_dict()
    digest = ReviewSnapshot.model_validate(snapshot).content_hash()
    response = cp.client.post(
        f"/api/worker/runs/{run}/jobs/{job}/snapshot",
        json={"snapshot_hash": digest, "snapshot": snapshot},
        headers=cp.auth,
    )
    assert response.status_code == 200, response.text
    return digest


def post_event(cp, event_id: str, job: str | None = None, message: str = "hello", run: str = RUN,
               **payload):
    response = cp.client.post(
        f"/api/worker/runs/{run}/events",
        json={"event_id": event_id, "run_id": run, "job_id": job, "message": message,
              "payload": payload, "links": {}, "created_at": "2026-10-03T10:00:00+00:00"},
        headers=cp.auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


def heartbeat(cp, run: str = RUN) -> None:
    assert cp.client.post(
        f"/api/worker/runs/{run}/heartbeat", json={"run_id": run, "status": "RUNNING"},
        headers=cp.auth,
    ).status_code == 200


def view(cp, run: str = RUN, job: str | None = None) -> str:
    return cp.tokens.mint("view", run, job=job)


def act(cp, action: str, digest: str, job: str = JOB, run: str = RUN, field_key: str | None = None):
    return cp.tokens.mint("act", run, job=job, action=action, snapshot_hash=digest,
                          field_key=field_key)


def tamper(token: str) -> str:
    """Change the payload (so the MAC can no longer match), deterministically."""
    head, kid, payload, mac = token.split(".")
    flipped = ("B" if payload[10] == "A" else "A")
    return ".".join((head, kid, payload[:10] + flipped + payload[11:], mac))


def run_token(cp, run: str = RUN) -> str:
    return cp.tokens.mint("run", run)


def get_job_page(cp, job: str = JOB, run: str = RUN, t: str | None = None, **kw):
    return cp.client.get(f"/r/{run}/{job}", params={"t": t or view(cp, run)}, **kw)


def post_json(cp, action: str, payload: dict, headers: dict | None = None):
    return cp.client.post(f"/api/{action}", json=payload, headers=headers)


def ready(cp, snapshot: dict | None = None, job: str = JOB) -> str:
    heartbeat(cp)
    return put_snapshot(cp, job, snapshot)


def used_tokens(cp) -> int:
    with cp.store._read() as conn:
        return conn.execute("SELECT COUNT(*) FROM used_tokens").fetchone()[0]


def commands(cp) -> list[dict]:
    return cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()["commands"]


def ack(cp, command_id: str) -> None:
    assert cp.client.post(f"/api/worker/runs/{RUN}/ack", json={"command_id": command_id},
                          headers=cp.auth).status_code == 200


# ------------------------------------------------------------ page content
def test_review_page_shows_readback_flags_and_forms(cp):
    snapshot = {
        "fields": [
            {"field_key": "email", "intended": "a@example.com", "actual": "a@example.com",
             "matched": True},
            {"field_key": "phone", "intended": "+91 1", "actual": "+91 2", "matched": False,
             "reason": "typed value changed by the site"},
            {"field_key": "cover", "intended": "x", "actual": "x", "matched": True,
             "generated": True, "escalated": True},
        ],
        "uploads": [{"field_key": "resume", "name": "cv.pdf", "sha256": "ab" * 32}],
        "generated_texts": {"cover": "Dear team, I like your work."},
        "unanswered": ["notice_period"],
        "screenshots": ["C:\\tmp\\shot_01.png"],
    }
    digest = ready(cp, snapshot)
    response = get_job_page(cp)
    assert response.status_code == 200
    text = response.text
    for expected in ("MISMATCH", "matched", "+91 1", "+91 2", "typed value changed by the site",
                     "generated text", "needs you", "Dear team, I like your work.",
                     "Notice period", "cv.pdf", "abababababab", digest[:8], "current",
                     "shot_01.png (screenshot not available)"):
        assert expected in text, expected
    page = parse(text)
    assert len(forms_for(text, "approve")) == 1 and len(forms_for(text, "reject")) == 1
    assert len(forms_for(text, "edit")) == 3  # one per field
    approve = forms_for(text, "approve")[0]
    assert approve["run_id"] == RUN and approve["job_id"] == JOB
    claims = cp.tokens.verify(approve["token"], "act")
    assert (claims.action, claims.snapshot_hash, claims.run, claims.job) == (
        "approve", digest, RUN, JOB)
    assert all(f["method"] == "post" for f in page.forms)
    assert "<script" not in text.lower()


def test_page_without_snapshot_offers_no_decision(cp):
    heartbeat(cp)
    post_event(cp, "E04", JOB, site="example", observed="captcha")  # creates the job, no snapshot
    text = get_job_page(cp).text
    assert "Waiting for the worker to post a fresh read-back" in text
    assert not forms_for(text, "approve") and not forms_for(text, "edit")


def test_evidence_link_is_a_signed_evd_token(cp):
    import base64
    import hashlib

    heartbeat(cp)
    evidence = cp.client.post(
        f"/api/worker/runs/{RUN}/evidence",
        json={"job_id": JOB, "name": "shot_01.png", "sha256": hashlib.sha256(PNG).hexdigest(),
              "mime": "image/png", "content_b64": base64.b64encode(PNG).decode()},
        headers=cp.auth,
    ).json()
    put_snapshot(cp, snapshot=snapshot_dict(screenshots=["C:\\tmp\\shot_01.png"]))
    text = get_job_page(cp).text
    match = re.search(r'href="/evidence/([^"?]+)\?t=([^"]+)"', text)
    assert match and match.group(1) == evidence["evidence_id"]
    claims = cp.tokens.verify(match.group(2), "evd")
    assert (claims.run, claims.job, claims.field_key) == (RUN, JOB, evidence["evidence_id"])


def test_run_page_lists_jobs_timeline_and_controls(cp):
    ready(cp)
    post_event(cp, "E01", message="goal set")
    t = view(cp)
    response = cp.client.get(f"/r/{RUN}", params={"t": t})
    assert response.status_code == 200
    text = response.text
    assert "goal set" in text and "RUNNING" in text and JOB in text
    link = re.search(r'href="(/r/[^"]+)"', text).group(1)
    assert link.startswith(f"/r/{RUN}/{JOB}?t=") and t in link
    for action in ("pause", "resume", "cancel"):
        (form,) = forms_for(text, action)
        assert cp.tokens.verify(form["token"], "run").run == RUN
        assert form["run_id"] == RUN


def test_security_headers_on_pages_and_errors(cp):
    ready(cp)
    for response in (
        get_job_page(cp),
        cp.client.get(f"/r/{RUN}", params={"t": view(cp)}),
        cp.client.get(f"/r/{RUN}/{JOB}"),  # 401 page
    ):
        headers = response.headers
        assert headers["cache-control"] == "no-store"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["x-content-type-options"] == "nosniff"
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        nonce = re.search(r"style-src 'nonce-([^']+)'", csp).group(1)
        assert f'<style nonce="{nonce}">' in response.text
        assert response.headers["content-type"].startswith("text/html")


# ------------------------------------------------------------ HTML escaping
def _injection_text() -> str:
    html = (FIXTURES / "job-1004.html").read_text(encoding="utf-8")
    found = re.search(r"<p>(Ignore previous rules[^<]*)</p>", html)
    assert found, "hostile fixture text not found"
    return found.group(1)


def test_hostile_strings_render_escaped(cp):
    injection = _injection_text()
    xss = "<script>alert(1)</script>"
    attr = '"><img src=x onerror=alert(1)>'
    snapshot = {
        "fields": [
            {"field_key": attr, "intended": xss, "actual": injection, "matched": False,
             "generated": True, "reason": "<b>bold</b>"},
            {"field_key": "name", "intended": "x", "actual": {"k": xss}, "matched": False},
        ],
        "uploads": [{"field_key": "resume", "name": "<u>cv.pdf</u>", "sha256": "cd" * 32}],
        "generated_texts": {"cover": xss + injection},
        "unanswered": ["<i>question</i>"],
        "screenshots": ["C:\\tmp\\<svg onload=1>.png"],
    }
    ready(cp, snapshot)
    post_event(cp, "E03", JOB, message=xss + injection)
    post_event(cp, "E06", JOB, message=injection, field_key=attr, label=xss, why=injection,
               suggestions=["<em>a</em>"])
    text = get_job_page(cp).text
    run_text = cp.client.get(f"/r/{RUN}", params={"t": view(cp)}).text

    for page_text in (text, run_text):
        page = parse(page_text)
        assert "script" not in page.tags and "img" not in page.tags
        assert not {"onerror", "onload", "onfocus"} & page.attrs
        assert xss not in page_text and attr not in page_text and "<script" not in page_text.lower()
    for raw in ("<b>bold</b>", "<u>cv.pdf</u>", "<i>question</i>", "<svg onload", "<em>a</em>"):
        assert raw not in text, raw
    assert str(escape(injection)) in text  # present, but escaped (the apostrophe becomes &#39;)
    assert injection not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert str(escape(injection)) in run_text  # the timeline message

    # Hidden attribute values round-trip exactly, i.e. they were quoted/escaped correctly.
    edit_forms = forms_for(text, "edit")
    assert attr in {f["field_key"] for f in edit_forms}
    (answer,) = forms_for(text, "answer")
    assert answer["field_key"] == attr
    hostile_edit = next(f for f in edit_forms if f["field_key"] == attr)
    assert cp.tokens.verify(hostile_edit["token"], "act").field_key == attr


# ---------------------------------------------------------- page auth matrix
def test_page_auth_errors(cp):
    ready(cp)
    heartbeat(cp, OTHER_RUN)
    digest = cp.store.current_snapshot_hash(RUN, JOB)
    other_job_token = view(cp, job=JOB2)
    cases = [
        ("no token", f"/r/{RUN}", None, 401),
        ("garbage", f"/r/{RUN}", "v1.garbage", 401),
        ("act token", f"/r/{RUN}/{JOB}", act(cp, "approve", digest), 401),
        ("run token", f"/r/{RUN}", run_token(cp), 401),
        ("tampered", f"/r/{RUN}", tamper(view(cp)), 401),
        ("other run", f"/r/{RUN}", view(cp, OTHER_RUN), 403),
        ("job token on run page", f"/r/{RUN}", view(cp, job=JOB), 403),
        ("job token for other job", f"/r/{RUN}/{JOB}", other_job_token, 403),
        ("unknown run", "/r/run_missing", view(cp, "run_missing"), 404),
        ("unknown job", f"/r/{RUN}/job_missing", view(cp), 404),
        ("bad id", f"/r/{RUN}/bad%20id", view(cp), 404),
    ]
    for label, path, token, status in cases:
        response = cp.client.get(path, params={"t": token} if token else None)
        assert response.status_code == status, label
        assert response.headers["content-type"].startswith("text/html"), label
        if token:
            assert token not in response.text, label
    assert cp.client.get(f"/r/{RUN}/{JOB}", params={"t": view(cp, job=JOB)}).status_code == 200


def test_expired_view_token_is_410(cp):
    ready(cp)
    t = view(cp)
    cp.clock.advance(24 * 3600 + 1)
    response = cp.client.get(f"/r/{RUN}", params={"t": t})
    assert response.status_code == 410 and "expired" in response.text


# ------------------------------------------------ end to end: E07 -> approve
def test_e07_page_approve_poll_ack(cp):
    heartbeat(cp)
    digest = put_snapshot(cp)
    post_event(cp, "E07", JOB, message="ready for review", snapshot_hash=digest,
               counts={"filled": 1, "total": 1, "need_user": 0, "skipped": 0})
    page = get_job_page(cp)
    assert page.status_code == 200
    (form,) = forms_for(page.text, "approve")

    response = cp.client.post("/api/approve", data=form)  # the page's own form post
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "Approved" in response.text and form["token"] not in response.text

    queued = cp.client.get(f"/api/worker/runs/{RUN}/commands", headers=cp.auth).json()  # W1
    (command,) = queued["commands"]
    assert command["action"] == "approve" and command["job_id"] == JOB
    assert command["snapshot_hash"] == digest and command["run_id"] == RUN
    assert command["token_hash"] == token_hash(form["token"]) and form["token"] not in str(command)
    assert queued["approvals"][command["command_id"]]["expires_at"].endswith("+00:00")

    acked = cp.client.post(f"/api/worker/runs/{RUN}/ack", json={"command_id": command["command_id"]},
                           headers=cp.auth)  # W2
    assert acked.status_code == 200 and acked.json()["already_acked"] is False
    assert commands(cp) == []
    assert cp.store.get_job(RUN, JOB)["review_state"] == "approved"
    assert "Approved for this read-back" in get_job_page(cp).text
    assert not forms_for(get_job_page(cp).text, "approve")  # no second approve offered

    replay = cp.client.post("/api/approve", json=dict(form))
    assert replay.status_code == 409 and replay.json()["error"] == "token_replayed"


def test_json_post_returns_json_shape(cp):
    digest = ready(cp)
    response = post_json(cp, "approve", {"token": act(cp, "approve", digest)})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["status"] == "queued" and body["duplicate"] is False
    assert body["command_id"].startswith("cmd_")


# ------------------------------------------------- rejection matrix (doc 5.5)
def test_invalid_token_401(cp):
    digest = ready(cp)
    good = act(cp, "approve", digest)
    for token in (tamper(good), good.replace("v1.", "v2.", 1), "nonsense", "x" * 2000):
        response = post_json(cp, "approve", {"token": token})
        assert response.status_code == 401 and response.json()["error"] == "invalid_token"
    assert used_tokens(cp) == 0 and commands(cp) == []


def test_expired_token_410_and_consumes_nothing(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    cp.clock.advance(30 * 60)
    response = post_json(cp, "approve", {"token": token})
    assert response.status_code == 410 and response.json()["error"] == "token_expired"
    assert used_tokens(cp) == 0 and commands(cp) == []


def test_wrong_scope_403(cp):
    digest = ready(cp)
    put_snapshot(cp, JOB2)
    digest2 = cp.store.current_snapshot_hash(RUN, JOB2)
    approve = act(cp, "approve", digest)
    cases = [
        ("reject", {"token": approve}),                                   # wrong action for route
        ("approve", {"token": approve, "run_id": OTHER_RUN}),             # wrong run
        ("approve", {"token": approve, "job_id": JOB2}),                  # wrong job
        ("edit", {"token": act(cp, "edit", digest, field_key="email"),
                  "field_key": "phone", "value": "x"}),                   # wrong field
        ("approve", {"token": act(cp, "approve", digest2, job=JOB2), "job_id": JOB}),
    ]
    for route, payload in cases:
        response = post_json(cp, route, payload)
        assert response.status_code == 403 and response.json()["error"] == "forbidden", route
    assert used_tokens(cp) == 0 and commands(cp) == []


def test_wrong_token_type_401(cp):
    digest = ready(cp)
    attempts = [
        ("approve", view(cp)),
        ("approve", run_token(cp)),
        ("approve", cp.tokens.mint("evd", RUN, job=JOB, field_key="ev_1")),
        ("pause", act(cp, "approve", digest)),
        ("cancel", view(cp)),
    ]
    for route, token in attempts:
        response = post_json(cp, route, {"token": token})
        assert response.status_code == 401 and response.json()["error"] == "invalid_token", route
    assert used_tokens(cp) == 0 and commands(cp) == []


def test_replay_409_even_while_command_is_queued_and_after_stale(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    assert post_json(cp, "approve", {"token": token}).status_code == 200
    again = post_json(cp, "approve", {"token": token})  # command still queued
    assert again.status_code == 409 and again.json()["error"] == "token_replayed"
    put_snapshot(cp, snapshot=snapshot_dict("b@example.com"))  # the token is now also stale
    again = post_json(cp, "approve", {"token": token})
    assert again.status_code == 409 and again.json()["error"] == "token_replayed"
    assert used_tokens(cp) == 1 and len(cp.store.list_queued_commands(RUN)[0]) == 0  # superseded


def test_stale_snapshot_409_consumes_nothing(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    put_snapshot(cp, snapshot=snapshot_dict("b@example.com"))
    response = post_json(cp, "approve", {"token": token})
    assert response.status_code == 409 and response.json()["error"] == "stale_snapshot"
    assert used_tokens(cp) == 0 and commands(cp) == []
    fresh = act(cp, "approve", cp.store.current_snapshot_hash(RUN, JOB))
    assert post_json(cp, "approve", {"token": fresh}).status_code == 200


def test_edit_pending_409_then_new_snapshot_allows_approve(cp):
    digest = ready(cp)
    edit = post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email"),
                                  "field_key": "email", "value": "new@example.com"})
    assert edit.status_code == 200
    assert cp.store.get_job(RUN, JOB)["review_state"] == "edit_pending"
    text = get_job_page(cp).text
    assert "edit is being applied" in text and not forms_for(text, "approve")

    response = post_json(cp, "approve", {"token": act(cp, "approve", digest)})
    assert response.status_code == 409 and response.json()["error"] == "edit_pending"
    assert used_tokens(cp) == 1  # only the edit token

    new_digest = put_snapshot(cp, snapshot=snapshot_dict("new@example.com"))
    (queued_edit,) = commands(cp)  # edits are never superseded by a snapshot
    assert queued_edit["action"] == "edit" and queued_edit["field_key"] == "email"
    assert queued_edit["value"] == "new@example.com"
    blocked = post_json(cp, "approve", {"token": act(cp, "approve", new_digest)})
    assert blocked.status_code == 409 and blocked.json()["error"] == "command_pending"
    ack(cp, queued_edit["command_id"])  # the worker acks the edit last
    assert post_json(cp, "approve", {"token": act(cp, "approve", new_digest)}).status_code == 200
    (approve,) = commands(cp)
    assert approve["action"] == "approve" and approve["snapshot_hash"] == new_digest


def test_already_approved_409_beats_command_pending(cp):
    digest = ready(cp)
    assert post_json(cp, "approve", {"token": act(cp, "approve", digest)}).status_code == 200
    second = post_json(cp, "approve", {"token": act(cp, "approve", digest)})
    assert second.status_code == 409 and second.json()["error"] == "already_approved"
    assert used_tokens(cp) == 1 and len(commands(cp)) == 1


def test_edit_pending_beats_already_approved(cp):
    digest = ready(cp)
    assert post_json(cp, "approve", {"token": act(cp, "approve", digest)}).status_code == 200
    ack(cp, commands(cp)[0]["command_id"])  # approval is now consumed_by_worker
    assert post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email"),
                                  "field_key": "email", "value": "z"}).status_code == 200
    response = post_json(cp, "approve", {"token": act(cp, "approve", digest)})
    assert response.status_code == 409 and response.json()["error"] == "edit_pending"


def test_command_pending_409_for_other_review_actions(cp):
    digest = ready(cp)
    assert post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email"),
                                  "field_key": "email", "value": "x"}).status_code == 200
    for route, token, extra in (
        ("reject", act(cp, "reject", digest), {}),
        ("edit", act(cp, "edit", digest, field_key="email"), {"field_key": "email", "value": "y"}),
    ):
        response = post_json(cp, route, {"token": token, **extra})
        assert response.status_code == 409 and response.json()["error"] == "command_pending", route
    assert used_tokens(cp) == 1


def test_precedence_chain(cp):
    digest = ready(cp)
    used = act(cp, "approve", digest)
    assert post_json(cp, "approve", {"token": used}).status_code == 200
    # forbidden beats replay
    assert post_json(cp, "reject", {"token": used}).status_code == 403
    # invalid beats expired; expired beats replay
    tampered = tamper(used)
    cp.clock.advance(31 * 60)
    assert post_json(cp, "approve", {"token": tampered}).status_code == 401
    assert post_json(cp, "approve", {"token": used}).status_code == 410
    cp.clock.advance(-31 * 60)
    # stale beats edit_pending: old token (h0), then h1 arrives and an edit is pending on h1
    cp2_old = act(cp, "approve", digest)
    h1 = put_snapshot(cp, snapshot=snapshot_dict("b@example.com"))  # supersedes the queued approve
    assert post_json(cp, "edit", {"token": act(cp, "edit", h1, field_key="email"),
                                  "field_key": "email", "value": "c"}).status_code == 200
    response = post_json(cp, "approve", {"token": cp2_old})
    assert response.status_code == 409 and response.json()["error"] == "stale_snapshot"


# ------------------------------------------------- CSRF / body validation
def test_bad_origin_403(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"},
    ):
        response = post_json(cp, "approve", {"token": token}, headers=headers)
        assert response.status_code == 403 and response.json()["error"] == "bad_origin", headers
    assert used_tokens(cp) == 0
    # Accepted variants (run tokens are multi-use, so one token serves all three).
    control = run_token(cp)
    for headers in ({"Origin": "http://127.0.0.1:8000"}, {"Sec-Fetch-Site": "none"},
                    {"Sec-Fetch-Site": "same-origin"}):
        assert post_json(cp, "pause", {"token": control}, headers=headers).status_code == 200


def test_bad_origin_on_form_post_is_an_html_page(cp):
    digest = ready(cp)
    response = cp.client.post("/api/approve", data={"token": act(cp, "approve", digest)},
                              headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and response.headers["content-type"].startswith("text/html")


def test_content_type_and_body_errors(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    post = cp.client.post
    assert post("/api/approve", content=b"token=x", headers={"content-type": "text/plain"}
                ).status_code == 415
    assert post("/api/approve", content=b"--b--", headers={
        "content-type": "multipart/form-data; boundary=b"}).status_code == 415
    assert post("/api/approve", content=b"token=x").status_code == 415  # no content type at all
    json_ct = {"content-type": "application/json"}
    assert post("/api/approve", content=b"{", headers=json_ct).status_code == 400
    assert post("/api/approve", content=b"[1]", headers=json_ct).status_code == 400
    assert post("/api/approve", content=b'{"token": NaN}', headers=json_ct).status_code == 400
    assert post_json(cp, "approve", {}).status_code == 400
    assert post_json(cp, "approve", {"token": 5}).status_code == 400
    assert post_json(cp, "approve", {"token": token, "run_id": 5}).status_code == 400
    assert post("/api/approve", content=b"token=a&token=b",
                headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 400
    assert post("/api/approve", content=b"x" * (129 * 1024), headers=json_ct).status_code == 413
    assert used_tokens(cp) == 0
    assert post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email")}
                     ).status_code == 400  # field_key missing
    assert post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email"),
                                  "field_key": "email"}).status_code == 400  # value missing
    too_big = post_json(cp, "edit", {"token": act(cp, "edit", digest, field_key="email"),
                                     "field_key": "email", "value": "x" * (65 * 1024)})
    assert too_big.status_code == 400
    bad_answer_type = act(cp, "answer", digest, field_key="email")
    assert post_json(cp, "answer", {"token": bad_answer_type, "field_key": "email",
                                    "value": 5}).status_code == 422
    assert used_tokens(cp) == 0 and commands(cp) == []


def test_state_changing_routes_are_post_only(cp):
    for action in ("approve", "edit", "reject", "skip", "answer", "handoff_done", "pause",
                   "resume", "cancel"):
        for method in ("GET", "PUT", "DELETE", "PATCH"):
            response = cp.client.request(method, f"/api/{action}")
            assert response.status_code == 405, (method, action)


# -------------------------------------------------------------- run controls
def test_run_controls_from_the_page_are_idempotent(cp):
    ready(cp)
    page = cp.client.get(f"/r/{RUN}", params={"t": view(cp)}).text
    (pause,) = forms_for(page, "pause")
    first = cp.client.post("/api/pause", data=pause)
    assert first.status_code == 200 and "Pause requested" in first.text
    second = post_json(cp, "pause", dict(pause))  # run tokens are multi-use; coalesced
    assert second.status_code == 200 and second.json()["duplicate"] is True
    (resume,) = forms_for(page, "resume")
    resumed = post_json(cp, "resume", dict(resume))
    assert resumed.status_code == 200 and resumed.json()["duplicate"] is False
    actions = [c["action"] for c in commands(cp)]
    assert actions == ["pause", "resume"]
    assert all(c["job_id"] is None and c["token_hash"] is None for c in commands(cp))

    wrong_run = post_json(cp, "pause", {**pause, "run_id": OTHER_RUN})
    assert wrong_run.status_code == 403
    cancel = post_json(cp, "cancel", dict(forms_for(page, "cancel")[0]))
    assert cancel.status_code == 200
    ack(cp, [c for c in commands(cp) if c["action"] == "cancel"][0]["command_id"])
    ended = post_json(cp, "pause", dict(pause))
    assert ended.status_code == 409 and ended.json()["error"] == "run_terminal"
    assert "no controls" in cp.client.get(f"/r/{RUN}", params={"t": view(cp)}).text


def test_run_control_for_unknown_run_is_404(cp):
    response = post_json(cp, "pause", {"token": run_token(cp, "run_missing")})
    assert response.status_code == 404 and response.json()["error"] == "not_found"


# ------------------------------------------------------- gates and the rest
def test_answer_gate_round_trip(cp):
    heartbeat(cp)
    post_event(cp, "E06", JOB, message="need an answer", field_key="notice_period",
               label="Notice period", why="required by the form", suggestions=["30 days"])
    text = get_job_page(cp).text
    assert "Notice period" in text and "required by the form" in text and "30 days" in text
    (form,) = forms_for(text, "answer")
    assert form["field_key"] == "notice_period"
    assert post_json(cp, "answer", {**form, "field_key": "other", "value": "1"}).status_code == 403
    response = cp.client.post("/api/answer", data={**form, "value": "30 days"})
    assert response.status_code == 200
    (command,) = commands(cp)
    assert command["action"] == "answer" and command["field_key"] == "notice_period"
    assert command["value"] == "30 days" and command["token_hash"] == token_hash(form["token"])
    assert not forms_for(get_job_page(cp).text, "answer")  # the gate is closed
    again = post_json(cp, "answer", {**form, "value": "x"})
    assert again.status_code == 409 and again.json()["error"] == "token_replayed"


def test_answer_value_must_be_a_short_string(cp):
    heartbeat(cp)
    post_event(cp, "E06", JOB, field_key="notice_period")
    (form,) = forms_for(get_job_page(cp).text, "answer")
    assert post_json(cp, "answer", {**form, "value": 5}).status_code == 422
    assert post_json(cp, "answer", {**form, "value": "x" * 2001}).status_code == 400
    assert used_tokens(cp) == 0  # nothing consumed; the same form still works
    assert post_json(cp, "answer", {**form, "value": "ok"}).status_code == 200


def test_handoff_done_and_skip_gates(cp):
    heartbeat(cp)
    post_event(cp, "E04", JOB, site="example.test", observed="captcha shown")
    text = get_job_page(cp).text
    assert "captcha shown" in text
    (form,) = forms_for(text, "handoff_done")
    assert post_json(cp, "handoff_done", dict(form)).status_code == 200
    assert post_json(cp, "handoff_done", dict(form)).status_code == 409  # replay

    post_event(cp, "E02", message="shortlist",
               chosen=[{"job_id": JOB2, "title": "SRE", "company": "Acme", "reasons": ["fit"]}])
    text = get_job_page(cp, JOB2).text
    assert "SRE" in text and "Acme" in text
    (skip,) = forms_for(text, "skip")
    assert post_json(cp, "skip", dict(skip)).status_code == 200
    assert [c["action"] for c in commands(cp)] == ["handoff_done", "skip"]
    assert {c["job_id"] for c in commands(cp)} == {JOB, JOB2}
    assert not forms_for(get_job_page(cp, JOB2).text, "skip")


def test_reject_from_the_page(cp):
    ready(cp)
    (form,) = forms_for(get_job_page(cp).text, "reject")
    assert cp.client.post("/api/reject", data=form).status_code == 200
    (command,) = commands(cp)
    assert command["action"] == "reject" and command["snapshot_hash"]
    assert cp.store.get_job(RUN, JOB)["review_state"] == "rejected"
    assert not forms_for(get_job_page(cp).text, "approve")


def test_form_error_is_an_html_page_with_same_status(cp):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    put_snapshot(cp, snapshot=snapshot_dict("b@example.com"))
    response = cp.client.post("/api/approve", data={"token": token})
    assert response.status_code == 409 and response.headers["content-type"].startswith("text/html")
    assert "Reload" in response.text and token not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_posts_never_log_tokens_or_values(cp, caplog):
    digest = ready(cp)
    token = act(cp, "edit", digest, field_key="email")
    with caplog.at_level(logging.DEBUG):
        post_json(cp, "edit", {"token": token, "field_key": "email", "value": "secret-value-123"})
        post_json(cp, "edit", {"token": token, "field_key": "email", "value": "secret-value-123"})
    assert token not in caplog.text and "secret-value-123" not in caplog.text
    tid = cp.tokens.verify(token, "act").tid
    assert f"tid={tid}" in caplog.text and "outcome=ok" in caplog.text
    assert "outcome=token_replayed" in caplog.text


def test_no_cross_run_leak_in_commands(cp):
    digest = ready(cp)
    heartbeat(cp, OTHER_RUN)
    other = put_snapshot(cp, JOB, snapshot_dict("o@example.com"), run=OTHER_RUN)
    assert post_json(cp, "approve", {"token": act(cp, "approve", digest)}).status_code == 200
    assert post_json(cp, "approve", {"token": act(cp, "approve", other, run=OTHER_RUN)}
                     ).status_code == 200
    mine = commands(cp)
    assert len(mine) == 1 and mine[0]["run_id"] == RUN and mine[0]["snapshot_hash"] == digest
    theirs = cp.client.get(f"/api/worker/runs/{OTHER_RUN}/commands", headers=cp.auth).json()
    assert [c["snapshot_hash"] for c in theirs["commands"]] == [other]


def test_database_is_consistent_after_the_suite_flows(cp):
    digest = ready(cp)
    post_json(cp, "approve", {"token": act(cp, "approve", digest)})
    conn = sqlite3.connect(cp.config.db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM used_tokens").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM commands WHERE action='approve'").fetchone()[0] == 1
    finally:
        conn.close()
