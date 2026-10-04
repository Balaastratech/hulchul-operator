"""Adversarial requests must leave every logical CP table unchanged."""

import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from control_plane.tokens import TokenService
from tests.control_plane.test_human_routes import (
    JOB,
    RUN,
    act,
    commands,
    post_event,
    ready,
    tamper,
    used_tokens,
    view,
)

ORIGIN = {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"}


def fingerprint(cp):
    """Capture logical content, not SQLite timestamps or WAL bytes."""
    with sqlite3.connect(cp.config.db_path) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize(
    "attack,expected",
    [
        ("forged", 401),
        ("tampered", 401),
        ("expired", 410),
        ("cross-run", 403),
        ("cross-job", 403),
        ("view-as-act", 401),
        ("wrong-action", 403),
        ("huge-token", 401),
    ],
)
def test_approval_attack_has_no_effect(cp, attack, expected):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    body = {"token": token, "run_id": RUN, "job_id": JOB}
    if attack == "forged":
        body["token"] = TokenService(secrets.token_bytes(48), clock=cp.clock).mint(
            "act", RUN, job=JOB, action="approve", snapshot_hash=digest
        )
    elif attack == "tampered":
        body["token"] = tamper(token)
    elif attack == "expired":
        cp.clock.advance(1801)
    elif attack == "cross-run":
        body["run_id"] = "other_run"
    elif attack == "cross-job":
        body["job_id"] = "other_job"
    elif attack == "view-as-act":
        body["token"] = view(cp)
    elif attack == "wrong-action":
        body["token"] = act(cp, "reject", digest)
    elif attack == "huge-token":
        body["token"] = "a" * 10000
    before = fingerprint(cp)
    response = cp.client.post("/api/approve", json=body, headers=ORIGIN)
    assert response.status_code == expected
    assert fingerprint(cp) == before


def test_act_as_view(cp):
    digest = ready(cp)
    before = fingerprint(cp)
    response = cp.client.get(
        f"/r/{RUN}/{JOB}", params={"t": act(cp, "approve", digest)}
    )
    assert response.status_code == 401
    assert fingerprint(cp) == before


def test_checkbox_answer_can_cross_control_plane(cp):  # RT-09 fixed
    """A real signed E06 gate must carry the typed boolean the graph requires."""
    post_event(cp, "E06", JOB, field_key="python", question="Select Python?")
    gate = cp.store.get_job(RUN, JOB)
    response = cp.client.post(
        "/api/answer",
        json={
            "token": act(cp, "answer", gate["gate_hash"], field_key="python"),
            "field_key": "python",
            "value": True,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert len(commands(cp)) == used_tokens(cp) == 1


@pytest.mark.parametrize(
    "header,value",
    [
        ("Origin", "https://attacker.invalid"),
        ("Origin", "null"),
        pytest.param(
            "Sec-Fetch-Site",
            "cross-site",
            marks=pytest.mark.xfail(
                strict=True,
                reason="RT-02: valid Origin masks contradictory Fetch Metadata",
            ),
        ),
        pytest.param(
            "Sec-Fetch-Site",
            "same-site",
            marks=pytest.mark.xfail(
                strict=True,
                reason="RT-02: valid Origin masks contradictory Fetch Metadata",
            ),
        ),
    ],
)
def test_csrf(cp, header, value):
    digest = ready(cp)
    before = fingerprint(cp)
    response = cp.client.post(
        "/api/approve",
        json={"token": act(cp, "approve", digest)},
        headers=dict(ORIGIN, **{header: value}),
    )
    assert response.status_code == 403
    assert fingerprint(cp) == before


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_fetch_metadata_without_origin(cp, site):
    """The wrong Fetch Metadata header alone cannot authorize a write."""
    digest = ready(cp)
    before = fingerprint(cp)
    response = cp.client.post(
        "/api/approve",
        json={"token": act(cp, "approve", digest)},
        headers={"Sec-Fetch-Site": site},
    )
    assert response.status_code == 403
    assert fingerprint(cp) == before


@pytest.mark.parametrize("different_tokens", [False, True])
def test_double_click_queues_exactly_one_command(cp, different_tokens):
    digest = ready(cp)
    token = act(cp, "approve", digest)
    tokens = [token, act(cp, "approve", digest) if different_tokens else token]
    barrier = Barrier(2)

    def click(token):
        barrier.wait(timeout=10)
        return cp.client.post(
            "/api/approve", json={"token": token}, headers=ORIGIN
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(click, tokens))
    assert sorted(statuses) == [200, 409]
    assert len(commands(cp)) == used_tokens(cp) == 1
    before = fingerprint(cp)
    assert (
        cp.client.post(
            "/api/approve", json={"token": token}, headers=ORIGIN
        ).status_code
        == 409
    )
    assert fingerprint(cp) == before


@pytest.mark.parametrize(
    "path,cap",
    [
        ("/api/edit", 128 * 1024),
        (f"/api/worker/runs/{RUN}/heartbeat", 4096),
        (f"/api/worker/runs/{RUN}/ack", 4096),
        (f"/api/worker/runs/{RUN}/events", 256 * 1024),
        (f"/api/worker/runs/{RUN}/jobs/{JOB}/snapshot", 1024 * 1024),
        (f"/api/worker/runs/{RUN}/evidence", 3 * 1024 * 1024),
    ],
)
@pytest.mark.parametrize("chunked", [False, True])
def test_huge_payload_no_effect(cp, path, cap, chunked):
    ready(cp)
    before = fingerprint(cp)
    headers = dict(cp.auth, **ORIGIN, **{"Content-Type": "application/json"})
    payload = b"x" * (cap + 1)
    content = iter([payload[:cap], payload[cap:]]) if chunked else payload
    assert cp.client.post(path, content=content, headers=headers).status_code == 413
    assert fingerprint(cp) == before
