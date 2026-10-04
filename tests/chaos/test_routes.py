"""Lose one request before handling or one response after durable route handling."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts import demo_g3
from tests.chaos.flow import finish, reach_review, safety
from tests.chaos.harness import ChaosScenario

LOSS_ROUTES = [
    ("GET", "/r/{run_id}"),
    ("GET", "/r/{run_id}/{job_id}"),
    ("HEAD", "/r/{run_id}"),
    ("HEAD", "/r/{run_id}/{job_id}"),
    ("GET", "/events/{run_id}"),
    *[
        ("POST", "/api/" + action)
        for action in (
            "approve",
            "edit",
            "reject",
            "skip",
            "answer",
            "handoff_done",
            "pause",
            "resume",
            "cancel",
        )
    ],
    ("GET", "/api/worker/runs/{run_id}/commands"),
    *[
        ("POST", "/api/worker/runs/{run_id}/" + suffix)
        for suffix in (
            "ack",
            "heartbeat",
            "events",
            "jobs/{job_id}/snapshot",
            "evidence",
        )
    ],
]

# T-042 additions are inventoried and checked cheaply here. Their loss/restart
# scenarios have not been measured; retain the original 40-case loss matrix.
ROUTES = LOSS_ROUTES + [
    ("GET", "/s/{code}"),
    ("HEAD", "/s/{code}"),
    ("GET", "/evidence/{evidence_id}"),
    ("HEAD", "/evidence/{evidence_id}"),
]


class DropOnce:
    """Test-only ASGI fault: 503 models unavailable request/lost route response."""

    def __init__(self, app, directory, method, path, phase):
        self.app, self.directory = app, directory
        self.method, self.path, self.phase = method, path, phase

    async def __call__(self, scope, receive, send):
        marker = self.directory / "route-fired"
        path = self.path.replace("{run_id}", "g3").replace("{job_id}", "fixture")
        selected = (
            scope["type"] == "http"
            and scope["method"] == self.method
            and scope["path"] == path
            and not marker.exists()
            and (self.directory / "route-armed").exists()
        )
        if not selected:
            return await self.app(scope, receive, send)
        marker.write_text(self.method + " " + self.path + " " + self.phase)

        async def unavailable():
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"test request/response lost"}
            )

        if self.phase == "before":
            return await unavailable()

        # First response header means route/store handling has completed. For SSE,
        # discard only the header then disconnect, so an infinite stream cannot hang.
        class LostResponse(BaseException):
            pass

        async def discard(message):
            if message["type"] == "http.response.start":
                await unavailable()
                raise LostResponse()

        try:
            await self.app(scope, receive, discard)
        except LostResponse:
            pass


def test_route_inventory():
    """New or missing routes fail collection coverage instead of silently escaping."""
    import tempfile

    from tests.control_plane.test_worker_routes import _config

    with tempfile.TemporaryDirectory() as directory:
        app = demo_g3.create_app(_config(Path(directory)))
        from control_plane.routes import human, sse, worker

        try:
            actual = {
                (method, route.path)
                for router in (human.router, worker.router, sse.router)
                for route in router.routes
                for method in route.methods
            }
            assert actual == set(ROUTES)
        finally:
            app.state.store.close()


@pytest.mark.chaos
@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("method,path", LOSS_ROUTES)
def test_route_loss_restart(tmp_path, monkeypatch, method, path, phase):
    """Actual CP routes and worker restart; same-token retry cannot add a command."""
    factory = demo_g3.create_app

    def create(config):
        app = factory(config)
        app.add_middleware(
            DropOnce, directory=tmp_path, method=method, path=path, phase=phase
        )
        return app

    monkeypatch.setattr(demo_g3, "create_app", create)
    with ChaosScenario(tmp_path) as scenario:
        (tmp_path / "route-armed").write_text("G3 started")
        scenario.variant = "answer" if path == "/api/answer" else "normal"
        try:
            _route_flow(scenario, tmp_path, method, path)
        finally:
            outcome = safety(scenario, edited=path == "/api/edit")
            (tmp_path / "outcome.json").write_text(json.dumps(outcome))
            assert (tmp_path / "route-fired").exists(), "route not exercised"
            assert (tmp_path / "route-restarted").exists(), (
                "worker was not restarted after request loss"
            )


def _route_flow(scenario, tmp_path, method, path):
    """Drive actual routes and retry only the request that was lost."""
    original_get = scenario.client.get

    def get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        return original_get(*args, **kwargs) if result.status_code == 503 else result

    scenario.client.get = get

    def post(action, fields, expected=200):
        response = scenario.client.post(
            "/api/" + action,
            json=fields,
            headers={"Origin": scenario.origin, "Sec-Fetch-Site": "same-origin"},
        )
        if response.status_code == 503:
            retry = scenario.client.post(
                "/api/" + action,
                json=fields,
                headers={
                    "Origin": scenario.origin,
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            assert retry.status_code in {200, 409}
            return retry
        assert response.status_code == expected
        return response

    scenario.post = post
    reach_review(scenario)
    if method == "HEAD":
        token = scenario.app.state.tokens.mint(
            "view", "g3", job="fixture" if path.endswith("{job_id}") else None
        )
        scenario.client.head(
            path.replace("{run_id}", "g3").replace("{job_id}", "fixture"),
            params={"t": token},
        )
    if path in {"/api/reject", "/api/skip", "/api/cancel"}:
        action = path.rsplit("/", 1)[1]
        if action == "cancel":
            fields = scenario.form(action, run=True)
        elif action == "skip":
            # The CP's skip route is shortlist-gate-bound, not review-hash-bound.
            # Publish the documented synthetic E02 envelope via the real worker API.
            response = scenario.client.post(
                "/api/worker/runs/g3/events",
                json={
                    "event_id": "E02",
                    "run_id": "g3",
                    "job_id": None,
                    "message": "Synthetic shortlist skip probe",
                    "links": {},
                    "payload": {
                        "chosen": [{"job_id": "fixture", "title": "Synthetic Engineer"}]
                    },
                    "created_at": "2026-10-04T00:00:00+00:00",
                },
                headers={"Authorization": "Bearer " + scenario.config.worker_token},
            )
            assert response.status_code == 200
            fields = scenario.form("skip")
        else:
            fields = {
                "token": scenario.app.state.tokens.mint(
                    "act",
                    "g3",
                    job="fixture",
                    action=action,
                    snapshot_hash=scenario.job["review_snapshot_hash"],
                )
            }
        post(action, fields)
        scenario.worker()
        scenario.worker()
        assert scenario.counter()["total"] == 0
        assert scenario.job["status"] in {"CANCELLED", "REJECTED_BY_USER"}
    else:
        if path.endswith("/evidence"):
            image = Path(scenario.job["review_snapshot"]["screenshots"][0]).read_bytes()
            payload = {
                "job_id": "fixture",
                "name": "chaos.png",
                "sha256": hashlib.sha256(image).hexdigest(),
                "mime": "image/png",
                "content_b64": base64.b64encode(image).decode(),
            }
            response = scenario.client.post(
                "/api/worker/runs/g3/evidence",
                json=payload,
                headers={"Authorization": "Bearer " + scenario.config.worker_token},
            )
            if response.status_code == 503:
                response = scenario.client.post(
                    "/api/worker/runs/g3/evidence",
                    json=payload,
                    headers={"Authorization": "Bearer " + scenario.config.worker_token},
                )
            assert response.status_code == 200
        if path == "/r/{run_id}":
            scenario.page(run=True)
        if path == "/r/{run_id}/{job_id}":
            scenario.page()
        if path == "/events/{run_id}":
            token = scenario.app.state.tokens.mint("view", "g3")
            with scenario.client.stream(
                "GET", "/events/g3", headers={"Authorization": "Bearer " + token}
            ) as stream:
                if stream.status_code == 503:
                    pass
                else:
                    assert stream.status_code == 200
                    next(stream.iter_lines())
        finish(
            scenario,
            pause=path in {"/api/pause", "/api/resume"},
            edit=path == "/api/edit",
        )
    assert (tmp_path / "route-fired").exists(), "route not exercised"
    assert scenario.counter()["total"] <= 1
    safety(scenario, edited=path == "/api/edit")
