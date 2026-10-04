"""T-047 item 2 and 7 on the review page: labels instead of internal keys, and an #edit anchor."""
from __future__ import annotations

import re
import secrets
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import TokenService

RUN, JOB = "run_alpha", "job_1"
ELIGIBLE = "Yes|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0"
NO = "No|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0"
JOIN = "TELL US WHY YOU WANT TO JOIN|textarea||0"


@pytest.fixture
def cp(tmp_path):
    config = load_config(
        environ={
            "CP_SIGNING_KEY": secrets.token_urlsafe(48),
            "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
            "CP_ENV": "dev",
            "CP_BASE_URL": "http://127.0.0.1:8000",
            "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
        }
    )
    clock = time.time
    store = Store(config.db_path, clock)
    app = create_app(config, store=store, clock=clock)
    tokens = TokenService(config.signing_key, config.signing_key_previous, clock)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, tokens=tokens, auth={"Authorization": f"Bearer {config.worker_token}"}
        )
    store.close()


def post_snapshot(cp):
    snapshot = {
        "fields": [
            {"field_key": ELIGIBLE, "intended": True, "actual": True, "matched": True},
            {"field_key": NO, "intended": None, "actual": False, "matched": True},
            {"field_key": JOIN, "intended": "Because", "actual": "Because", "matched": True, "generated": True},
        ],
        "unanswered": [NO],
        "generated_texts": {JOIN: "Because"},
    }
    digest = ReviewSnapshot.model_validate(snapshot).content_hash()
    response = cp.client.post(
        f"/api/worker/runs/{RUN}/jobs/{JOB}/snapshot",
        json={"snapshot_hash": digest, "snapshot": snapshot},
        headers=cp.auth,
    )
    assert response.status_code == 200, response.text
    return digest


def post_event(cp, kind, **payload):
    body = {
        "event_id": kind, "run_id": RUN, "job_id": JOB, "message": "m", "payload": payload,
        "links": {}, "created_at": datetime.now(UTC).isoformat(),
    }
    response = cp.client.post(f"/api/worker/runs/{RUN}/events", json=body, headers=cp.auth)
    assert response.status_code == 200, response.text


def open_page(cp):
    view = cp.tokens.mint("view", RUN, job=JOB)
    response = cp.client.get(f"/r/{RUN}/{JOB}", params={"t": view})
    assert response.status_code == 200
    return response.text


def test_review_page_shows_questions_not_internal_keys_and_has_an_edit_anchor(cp):
    digest = post_snapshot(cp)
    post_event(cp, "E07", snapshot_hash=digest)
    html = open_page(cp)
    assert 'id="edit"' in html  # target of the "Edit a field" button (#edit)
    # the radio options Yes/No are ONE question row (T-049), not one row each
    assert html.count('<th scope="row">Are you eligible to work in the country of this role?</th>') == 1
    assert "Tell us why you want to join" in html
    # hidden form inputs keep the real key for the POST; no visible text may show it
    visible = re.sub(r"<input[^>]*>", "", html)
    assert "|radio|" not in visible and "|textarea|" not in visible
    # the unanswered list names the QUESTION once, not each option
    assert html.count("<li>Are you eligible to work in the country of this role?</li>") == 1


def test_ask_gate_shows_the_question_and_no_event_code(cp):
    post_event(cp, "E06", field_key=ELIGIBLE, question=ELIGIBLE)
    html = open_page(cp)
    visible = re.sub(r"<input[^>]*>", "", html)  # tokens live in hidden inputs
    assert "Are you eligible to work in the country of this role?" in visible  # radio gate (T-049)
    assert "Your answer is needed" in html
    assert "E06" not in visible and "|radio|" not in visible


def test_handoff_gate_has_a_neutral_title(cp):
    post_event(cp, "E04", site="acme.example", observed="Legal consent checkbox")
    html = open_page(cp)
    assert "Action needed in the browser" in html
    assert "E04" not in re.sub(r"<input[^>]*>", "", html)
