"""T-042: /s/<code> short links are opaque, read-only, expiring and scope-bound."""
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.config import load_config
from control_plane.models import ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import SHORT_WINDOW_BUCKETS, SHORT_BUCKET_S, TokenService


@pytest.fixture
def web(tmp_path):
    clock = [1_800_000_000]
    config = load_config({"CP_SIGNING_KEY": secrets.token_hex(32), "CP_WORKER_TOKEN": secrets.token_hex(32),
                          "CP_BASE_URL": "https://fixture.invalid", "CP_DB_PATH": str(tmp_path / "cp.sqlite")})
    store = Store(config.db_path, clock=lambda: clock[0])
    app = create_app(config, store=store, clock=lambda: clock[0])
    snap = ReviewSnapshot(fields=[])
    store.put_snapshot("R1", "J1", snap.content_hash(), snap)
    with TestClient(app, follow_redirects=False) as client:
        yield client, app, clock
    store.close()


def test_job_code_redirects_to_the_job_page_with_a_fresh_view_token(web):
    client, app, _ = web
    code = app.state.tokens.short_code("R1", "J1")
    response = client.get(f"/s/{code}")
    assert response.status_code == 302 and response.headers["cache-control"] == "no-store"
    parts = urlsplit(response.headers["location"])
    assert parts.path == "/r/R1/J1"
    claims = app.state.tokens.verify(parse_qs(parts.query)["t"][0], "view")
    assert (claims.run, claims.job, claims.action) == ("R1", "J1", None)
    assert client.get(response.headers["location"]).status_code == 200


def test_run_code_opens_the_run_page_not_a_job(web):
    client, app, _ = web
    location = client.get(f"/s/{app.state.tokens.short_code('R1')}").headers["location"]
    assert urlsplit(location).path == "/r/R1"


def test_view_token_cannot_outlive_the_link(web):
    client, app, clock = web
    code = app.state.tokens.short_code("R1", "J1")
    clock[0] += 23 * 3600 + 30 * 60
    location = client.get(f"/s/{code}").headers["location"]
    claims = app.state.tokens.verify(parse_qs(urlsplit(location).query)["t"][0], "view")
    assert claims.exp - claims.iat <= 3600


def test_code_expires_after_24_hours(web):
    client, app, clock = web
    code = app.state.tokens.short_code("R1", "J1")
    clock[0] += 24 * 3600 - 1
    assert client.get(f"/s/{code}").status_code == 302
    clock[0] += (SHORT_BUCKET_S * 2)
    assert client.get(f"/s/{code}").status_code == 404


def test_unknown_malformed_and_foreign_codes_are_refused(web):
    client, app, _ = web
    other = TokenService(secrets.token_bytes(48)).short_code("R1", "J1")  # another deployment's key
    for bad in (other, "aaaaaaaaaaaaa", "SHORT", "../etc/passwd", "a" * 40):
        assert client.get(f"/s/{bad}").status_code == 404
    assert client.get(f"/s/{app.state.tokens.short_code('R1', 'NOPE')}").status_code == 404


def test_short_link_is_get_only_and_writes_nothing(web):
    client, app, _ = web
    store = app.state.store
    before = [tuple(r) for r in store._conn().execute("SELECT * FROM used_tokens")]
    code = app.state.tokens.short_code("R1", "J1")
    assert client.post(f"/s/{code}").status_code == 405
    client.get(f"/s/{code}")
    assert [tuple(r) for r in store._conn().execute("SELECT * FROM used_tokens")] == before
    assert SHORT_WINDOW_BUCKETS == 96
