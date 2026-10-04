"""Focused change regression, including opt-in system Chrome form posts."""
import os
import secrets
import socket
import threading
import time

import pytest
import uvicorn
import asyncio
import httpx
from starlette.testclient import TestClient

def make_app(tmp_path, url):
    from control_plane.app import create_app
    from control_plane.config import load_config

    return create_app(load_config(environ={
        "CP_SIGNING_KEY": secrets.token_urlsafe(48),
        "CP_WORKER_TOKEN": secrets.token_urlsafe(48),
        "CP_ENV": "dev", "CP_BASE_URL": url,
        "CP_DB_PATH": str(tmp_path / "cp.sqlite"),
    }))


def seed(app, job="approve"):
    from src.operator.contracts import ReviewSnapshot

    snapshot = {"fields": [
        {"field_key": "Email|email||0", "intended": "a@example.test", "actual": "a@example.test", "matched": True},
        {"field_key": "Terms|checkbox||0", "intended": None, "actual": True, "matched": False},
        {"field_key": "Note|text||0", "intended": None, "actual": "", "matched": False},
        {"field_key": "City|text||0", "intended": "A", "actual": "B", "matched": False},
    ]}
    digest = ReviewSnapshot.model_validate(snapshot).content_hash()
    auth = {"Authorization": "Bearer " + app.state.config.worker_token}
    with TestClient(app) as client:
        assert client.post("/api/worker/runs/r/heartbeat", json={"run_id": "r", "status": "RUNNING"}, headers=auth).status_code == 200
        assert client.post(f"/api/worker/runs/r/jobs/{job}/snapshot", json={"snapshot_hash": digest, "snapshot": snapshot}, headers=auth).status_code == 200
    return f"/r/r/{job}?t=" + app.state.tokens.mint("view", "r", job=job)


def test_review_labels_and_strict_origin(tmp_path):
    app = make_app(tmp_path, "http://127.0.0.1:8000")
    path = seed(app)
    with TestClient(app) as client:
        response = client.get(path)
        assert response.headers["referrer-policy"] == "same-origin"
        assert '<meta name="referrer" content="same-origin">' in response.text
        assert "Left for you" in response.text
        assert "Set by you in the browser" in response.text
        assert response.text.count("MISMATCH") == 1
        assert "Approve and submit (fixture)" in response.text
        for origin in ("null", "https://evil.test"):
            assert client.post("/api/approve", data={}, headers={"Origin": origin}).status_code == 403


def test_worker_upload_and_inline_scoped_image(tmp_path):
    from src.operator.app.factory import RealChannel
    from src.operator.app.delivery import DeliveryLog
    from src.operator.channels.web import HttpSink, WebChannel
    from src.operator.contracts import Event, ReviewSnapshot
    from datetime import UTC, datetime
    from types import SimpleNamespace
    import base64
    import re

    app = make_app(tmp_path, "http://127.0.0.1:8000")
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    # A real decodable 1x1 PNG, no personal data.
    content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    shot = evidence / "shot.png"
    shot.write_bytes(content)
    snapshot = {"fields": [], "screenshots": [str(shot)]}
    digest = ReviewSnapshot.model_validate(snapshot).content_hash()

    async def run():
        channel = object.__new__(RealChannel)
        channel.directory = tmp_path
        channel.goal = channel.run_context = channel.telegram = None
        channel.delivered = DeliveryLog()
        channel.policy = SimpleNamespace(record=lambda *args: None)
        channel.allowlist = SimpleNamespace(permits_submission=lambda url: True)
        class Browser:
            async def submission_urls(self):
                return ["http://127.0.0.1:8780"]
        channel.browser = Browser()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            channel.sink = HttpSink(app.state.config.base_url, "Bearer " + app.state.config.worker_token, client=client)
            channel.web = WebChannel(channel.sink)
            for kind in ("E07", "E08"):
                await channel.emit(Event(event_id=kind, run_id="r", job_id="j", message="Review", created_at=datetime.now(UTC), payload={"review": snapshot, "snapshot_hash": digest}))
    asyncio.run(run())
    with TestClient(app) as client:
        page = client.get("/r/r/j", params={"t": app.state.tokens.mint("view", "r", job="j")})
        href = re.search(r'<img src="([^"]+)"', page.text).group(1)
        response = client.get(href)
        assert response.status_code == 200 and response.content == content
        assert response.headers["content-type"] == "image/png"
        assert client.get(href.split("?")[0]).status_code == 401
        wrong = app.state.tokens.mint("evd", "r", job="other", field_key=href.split("/")[-1].split("?")[0])
        assert client.get(href.split("?")[0], params={"t": wrong}).status_code == 404
        with app.state.store._read() as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1


def test_upload_rejects_unbounded_or_unrelated_files(tmp_path):
    from src.operator.app.evidence import evidence_body, MAX_IMAGE_BYTES
    from src.operator.channels.base import ChannelError

    directory = tmp_path / "evidence"
    directory.mkdir()
    shot = directory / "shot.png"
    shot.write_bytes(b"x" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(ChannelError, match="too_large"):
        evidence_body(directory, str(shot), "j")
    with pytest.raises(ChannelError, match="evidence_outside_directory"):
        evidence_body(directory, str(tmp_path / "private.png"), "j")
    with pytest.raises(ChannelError, match="unsupported_media_type"):
        evidence_body(directory, str(directory / "shot.svg"), "j")


@pytest.mark.skipif(os.environ.get("T052_CHROME") != "1", reason="opt-in real Chrome proof")
def test_real_chrome_forms(tmp_path):
    from playwright.sync_api import sync_playwright

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app = make_app(tmp_path, url)
    review = seed(app)
    edit = seed(app, "edit")
    answer = seed(app, "answer")
    with TestClient(app) as client:
        assert client.post("/api/worker/runs/r/events", headers={"Authorization": "Bearer " + app.state.config.worker_token}, json={
            "event_id": "E06", "run_id": "r", "job_id": "answer", "message": "Your note?",
            "payload": {"field_key": "Note|text||0"}, "links": {}, "created_at": "2026-10-04T10:00:00Z",
        }).status_code == 200
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=False)
            try:
                page = browser.new_page()
                page.goto(url + review)
                old_page = page.content()
                with page.expect_response(lambda r: r.url == url + "/api/approve") as result:
                    page.locator('form[action="/api/approve"] button').click()
                assert result.value.status in (200, 303), f"Approve returned {result.value.status}"
                assert result.value.request.headers["origin"] == url
                page.set_content(old_page)
                with page.expect_response(lambda r: r.url == url + "/api/approve") as second:
                    page.locator('form[action="/api/approve"] button').click()
                assert second.value.status == 409
                page.goto(url + edit)
                form = page.locator('form[action="/api/edit"]').first
                form.locator('input[name="value"]').fill("changed@example.test")
                with page.expect_response(lambda r: r.url == url + "/api/edit") as result:
                    form.locator('button').click()
                assert result.value.status in (200, 303)
                assert result.value.request.headers["origin"] == url
                page.goto(url + answer)
                page.locator('#answer-value').fill("My answer")
                with page.expect_response(lambda r: r.url == url + "/api/answer") as result:
                    page.locator('form[action="/api/answer"] button').click()
                assert result.value.status in (200, 303)
                assert result.value.request.headers["origin"] == url
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
