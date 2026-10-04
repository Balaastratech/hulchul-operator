"""AUDIT-026: screenshots are served only through a scoped, signed `evd` capability."""
import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.routes.worker import _store_evidence
from control_plane.store import Store

IMAGE = b"\x89PNG\r\n\x1a\nsynthetic"


@pytest.fixture
def served(tmp_path):
    config = load_config({
        "CP_SIGNING_KEY": secrets.token_hex(32), "CP_WORKER_TOKEN": secrets.token_hex(32),
        "CP_BASE_URL": "https://fixture.invalid", "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
    })
    store = Store(config.db_path)
    app = create_app(config, store=store)
    result = _store_evidence(store, app.state.evidence_dir, "R1", "J1", "shot.png",
                             hashlib.sha256(IMAGE).hexdigest(), "image/png", IMAGE)
    with TestClient(app) as client:
        yield client, app, result["evidence_id"]
    store.close()


def test_valid_token_serves_image_with_safe_headers(served):
    client, app, eid = served
    token = app.state.tokens.mint("evd", "R1", job="J1", field_key=eid)
    response = client.get(f"/evidence/{eid}", params={"t": token})
    assert response.status_code == 200 and response.content == IMAGE
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_no_token_or_wrong_type_is_refused(served):
    client, app, eid = served
    assert client.get(f"/evidence/{eid}").status_code == 401
    view = app.state.tokens.mint("view", "R1", job="J1")
    assert client.get(f"/evidence/{eid}", params={"t": view}).status_code == 401


def test_token_for_another_evidence_job_or_run_is_refused(served):
    client, app, eid = served
    other_id = "ev_" + secrets.token_hex(16)
    for token in (
        app.state.tokens.mint("evd", "R1", job="J1", field_key=other_id),
        app.state.tokens.mint("evd", "R1", job="J2", field_key=eid),
        app.state.tokens.mint("evd", "R2", job="J1", field_key=eid),
    ):
        assert client.get(f"/evidence/{eid}", params={"t": token}).status_code in (403, 404)


def test_path_like_ids_never_reach_the_filesystem(served):
    client, app, _ = served
    token = app.state.tokens.mint("evd", "R1", job="J1", field_key="..")
    for bad in ("..", "ev_../../x", "x%2F..%2Fy"):
        assert client.get(f"/evidence/{bad}", params={"t": token}).status_code == 404


def test_expired_evidence_is_gone(served):
    client, app, eid = served
    with app.state.store._tx() as conn:
        conn.execute("UPDATE evidence SET expires_at='2000-01-01T00:00:00Z'")
    token = app.state.tokens.mint("evd", "R1", job="J1", field_key=eid)
    assert client.get(f"/evidence/{eid}", params={"t": token}).status_code == 404
