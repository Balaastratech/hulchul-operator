"""Outbound-only control-plane transport, with explicit endpoint configuration."""

import json
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.operator.contracts import Command
from src.operator.policy.allowlist import origin


class WorkerTransport(Protocol):
    """The control-plane adapter owns command authorization and delivery."""

    def poll(self, run_id: str) -> list[Command]:
        """Read authorized commands for one run."""
        ...

    def acknowledge(self, command_id: str) -> None:
        """Acknowledge only after graph checkpoint persistence."""
        ...

    def heartbeat(self, run_id: str, status: str) -> None:
        """Publish liveness, never candidate data or capabilities."""
        ...


class HttpTransport:
    """JSON endpoint adapter; no endpoint paths are guessed or hardcoded."""

    def __init__(
        self,
        commands_url: str,
        acknowledgement_url: str,
        heartbeat_url: str,
        *,
        authorization: str | None = None,
        timeout: float = 10,
    ) -> None:
        """Require same-origin HTTPS or loopback-only HTTP."""
        urls = [commands_url, acknowledgement_url, heartbeat_url]
        origins = {origin(url) for url in urls}
        if len(origins) != 1:
            raise ValueError("worker endpoints must share an origin")
        scheme, host, _ = next(iter(origins))
        if scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-local worker endpoints require HTTPS")
        if host not in {"localhost", "127.0.0.1", "::1"} and not authorization:
            raise ValueError("remote control-plane endpoints require authorization")
        if any(urlsplit(url).query for url in urls):
            raise ValueError("capabilities cannot be placed in endpoint query strings")
        self.commands_url, self.acknowledgement_url, self.heartbeat_url = urls
        self.authorization = authorization
        self.timeout = timeout
        self.approvals: dict[str, datetime] = {}

    def _request(self, url: str, payload: dict | None = None):
        headers = {"Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise PermissionError("control-plane redirects are forbidden")

        with build_opener(NoRedirect).open(request, timeout=self.timeout) as response:
            # Never forward credentials through a redirect to a different origin.
            if origin(response.url) != origin(url):
                raise PermissionError("control-plane redirect changed origin")
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError("control-plane response exceeds limit")
            return json.loads(content) if content else None

    def poll(self, run_id: str) -> list[Command]:
        """GET reads only; filtering occurs before any graph resume."""
        result = self._request(self.commands_url)
        commands = []
        for item in result["commands"]:
            command = Command.model_validate(item.get("command", item))
            if command.action == "approve":
                expiry = datetime.fromisoformat(item["approval_expires_at"])
                if expiry.tzinfo is None:
                    raise ValueError("approval expiry must be aware")
                self.approvals[command.command_id] = expiry
            commands.append(command)
        if any(command.run_id != run_id for command in commands):
            raise PermissionError("control plane returned another run's commands")
        return commands

    def approval_expiry(self, command: Command) -> datetime | None:
        """Verified CP metadata stays transient; raw tokens are never accepted."""
        return self.approvals.get(command.command_id)

    def acknowledge(self, command_id: str) -> None:
        """POST acknowledgement; no raw capability values in this payload."""
        self._request(self.acknowledgement_url, {"command_id": command_id})

    def heartbeat(self, run_id: str, status: str) -> None:
        """POST heartbeat without state contents."""
        self._request(self.heartbeat_url, {"run_id": run_id, "status": status})
