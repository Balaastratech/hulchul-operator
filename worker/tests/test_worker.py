"""Restart, command replay, pause boundary and outbound transport restrictions."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.operator.contracts import Command, FieldSpec, FillAction
from src.operator.graph import sqlite_graph
from src.operator.graph.runtime import AnswerPlan
from src.operator.graph.tests.fakes import FakeBrowser, FakeLLM, services_at
from worker.main import Worker
from worker.transport import HttpTransport


class Transport:
    def __init__(self):
        self.commands = []
        self.acks = []
        self.heartbeats = []
        self.fail_ack = False

    def poll(self, run_id):
        return list(self.commands)

    def acknowledge(self, command_id):
        if self.fail_ack:
            raise OSError("Synthetic ack failure")
        self.acks.append(command_id)
        self.commands = [
            command for command in self.commands if command.command_id != command_id
        ]

    def heartbeat(self, run_id, status):
        self.heartbeats.append((run_id, status))


def test_worker_restart_and_failed_ack_do_not_reapply_command(tmp_path):
    services = services_at(tmp_path)
    transport = Transport()
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        worker = Worker(graph, services, transport, "r", tmp_path / "commands.sqlite")
        worker.start_or_resume("Fill a fixture")
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        worker = Worker(graph, services, transport, "r", tmp_path / "commands.sqlite")
        worker.start_or_resume()
        assert services.browser.executions == ["name"]
        command = Command(
            command_id="reject", run_id="r", job_id="fixture", action="reject"
        )
        transport.fail_ack = True
        with pytest.raises(OSError):
            worker.handle(command)
        transport.fail_ack = False
        worker.handle(command)
        assert transport.acks == ["reject"]
        assert graph.get_state(worker.config).values["run"]["status"] == "COMPLETED"
        assert services.browser.executions == ["name"]
    services.close()


def test_pause_between_two_atomic_fields(tmp_path):
    transport = Transport()
    pause = Command(command_id="pause-between", run_id="r", action="pause")

    class Browser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.values["email"] = ""

        async def extract_fields(self):
            return await super().extract_fields() + [
                FieldSpec(id="email", key="email", label="Email", type="text")
            ]

        async def execute(self, action):
            result = await super().execute(action)
            if action.field_key == "name":
                transport.commands = [pause]
            return result

    class LLM(FakeLLM):
        async def structured(self, prompt, response_model):
            if response_model is AnswerPlan:
                return AnswerPlan(
                    actions=[
                        FillAction(
                            field_key="name",
                            action="fill",
                            value="Synthetic",
                            source="profile.name",
                        ),
                        FillAction(
                            field_key="email",
                            action="fill",
                            value="synthetic@example.test",
                            source="profile.email",
                        ),
                    ]
                )
            return await super().structured(prompt, response_model)

    services = services_at(tmp_path, Browser())
    services.llm = LLM()
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        worker = Worker(graph, services, transport, "r", tmp_path / "commands.sqlite")
        result = worker.start_or_resume("Fill a fixture")
        assert result["__interrupt__"][0].value["kind"] == "paused"
        assert services.browser.executions == ["name"]
        worker.tick()  # acknowledges persisted pause, never resumes the paused graph
        assert services.browser.executions == ["name"]
        assert transport.acks == ["pause-between"]
        result = worker.handle(
            Command(command_id="resume", run_id="r", action="resume")
        )
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name", "email"]
    services.close()


@pytest.mark.parametrize(
    "urls,auth",
    [
        (["http://remote.test/a"] * 3, None),
        (["https://remote.test/a"] * 3, None),
        (
            ["https://one.test/a", "https://two.test/b", "https://one.test/c"],
            "synthetic",
        ),
        (["http://localhost/a?t=synthetic"] * 3, None),
    ],
)
def test_transport_rejects_unsafe_configuration(urls, auth):
    with pytest.raises(ValueError):
        HttpTransport(*urls, authorization=auth)


def test_redirect_is_rejected_before_following_or_forwarding_auth():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/leak"
            )
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/commands"
    transport = HttpTransport(url, url, url, authorization="synthetic-test-value")
    try:
        with pytest.raises(PermissionError):
            transport.poll("r")
        assert requests == ["/commands"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_remote_approval_metadata_registers_local_ledger_and_consumes_once(tmp_path):
    from datetime import datetime, timedelta, timezone

    services = services_at(tmp_path)

    class RemoteTransport(Transport):
        def approval_expiry(self, command):
            return datetime.now(timezone.utc) + timedelta(minutes=5)

    transport = RemoteTransport()
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        worker = Worker(graph, services, transport, "r", tmp_path / "commands.sqlite")
        gate = worker.start_or_resume("Fill a fixture")["__interrupt__"][0].value
        command = Command(
            command_id="remote-approve",
            run_id="r",
            job_id="fixture",
            action="approve",
            token_hash="a" * 64,
            snapshot_hash=gate["snapshot_hash"],
        )
        result = worker.handle(command)
        assert result["run"]["status"] == "COMPLETED"
        assert services.browser.submissions == 1
        worker.handle(command)
        assert services.browser.submissions == 1
    services.close()


def test_polled_approval_cannot_contain_raw_token_and_requires_aware_expiry():
    from pydantic import ValidationError

    transport = HttpTransport(*(["http://localhost:8000/commands"] * 3))
    command = {
        "command_id": "c",
        "run_id": "r",
        "job_id": "j",
        "action": "approve",
        "token_hash": "a" * 64,
        "snapshot_hash": "b" * 64,
    }
    transport._request = lambda url: {
        "commands": [
            {"command": command, "approval_expires_at": "2026-10-03T12:00:00+05:30"}
        ]
    }
    parsed = transport.poll("r")[0]
    assert transport.approval_expiry(parsed).tzinfo is not None
    command["token"] = "synthetic-raw-token"
    with pytest.raises(ValidationError):
        transport.poll("r")


def test_cp_sibling_approval_map_is_supported_and_missing_metadata_rejected():
    transport = HttpTransport(*(["http://localhost:8000/commands"] * 3))
    command = {
        "command_id": "c",
        "run_id": "r",
        "job_id": "j",
        "action": "approve",
        "token_hash": "a" * 64,
        "snapshot_hash": "b" * 64,
    }
    response = {
        "commands": [command],
        "approvals": {"c": {"expires_at": "2026-10-03T12:00:00Z"}},
    }
    transport._request = lambda url: response
    parsed = transport.poll("r")[0]
    assert transport.approval_expiry(parsed).isoformat() == "2026-10-03T12:00:00+00:00"
    response["approvals"] = {}
    with pytest.raises(ValueError, match="metadata is required"):
        transport.poll("r")
    response["approvals"] = {"c": {"expires_at": "2026-10-03T12:00:00"}}
    with pytest.raises(ValueError, match="aware"):
        transport.poll("r")
