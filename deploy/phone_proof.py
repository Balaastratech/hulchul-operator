"""Phone approval proof using scripts/demo_g3.py without scripted approval.

Run from the repo root: python deploy/phone_proof.py --auto
ENV_FILE supplies Telegram settings; CP_BASE_URL must forward to --cp-port.
Proof artifacts contain only synthetic data and redacted request metadata.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from control_plane.config import Config
from scripts import demo_g3 as demo

REQUESTS: list[dict] = []
APPROVALS: list[dict] = []  # Tokens live only in memory for the replay check.
START = time.monotonic()
ORIGINAL_CREATE = demo.create_app


class RedactedRequests:
    """Record methods, paths and statuses, omitting queries, headers and bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()

        async def read() -> Message:
            message = await receive()
            if scope["path"] == "/api/approve":
                body.extend(message.get("body", b""))
            return message

        async def write(message: Message) -> None:
            if message["type"] == "http.response.start":
                REQUESTS.append({"elapsed_s": round(time.monotonic() - START, 3),
                                 "method": scope["method"], "path": scope["path"],
                                 "status": message["status"]})
                if scope["path"] == "/api/approve" and message["status"] in (200, 303):
                    if body.startswith(b"{"):
                        APPROVALS.append(json.loads(body))
                    else:
                        APPROVALS.append({k: v[0] for k, v in parse_qs(body.decode()).items()})
            await send(message)

        await self.app(scope, read, write)


def observed_app(config: Config) -> FastAPI:
    """Wrap only this fixture rehearsal, leaving production logging unchanged."""
    app = ORIGINAL_CREATE(config)
    app.add_middleware(RedactedRequests)
    return app


def phone_exercise(scenario: demo.Scenario, **unused) -> dict:
    """Consume only a phone-issued approval and prove exactly one fixture submit."""
    scenario.reach_review()  # main's wrapper sends the real Telegram message.
    sent_at = datetime.now(UTC).isoformat()
    print("USER: open Telegram on your phone now, tap the review link, press Approve", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(scenario.config.db_path)) as db:
            count = db.execute("SELECT count(*) FROM commands WHERE action='approve'").fetchone()[0]
        if count and APPROVALS:
            break
        time.sleep(1)
    else:
        persist({"result": "approval_timeout", "telegram_sent_at": sent_at,
                 "timed_out_at": datetime.now(UTC).isoformat(), "approval_commands": count,
                 "submissions": scenario.counter()["total"],
                 "elapsed_s": round(time.monotonic() - START, 3)})
        raise RuntimeError("Phone approval not received within 600 seconds; no submission")
    assert count == 1 and len(APPROVALS) == 1, "exactly one actual approval required"
    arrived_at = datetime.now(UTC).isoformat()
    assert scenario.counter()["total"] == 0
    scenario.worker()
    scenario.worker()
    assert scenario.counter()["total"] == 1
    assert scenario.job["status"] == "SUBMITTED_VERIFIED"
    assert (scenario.directory / "operations.txt").read_text().splitlines().count("submit") == 1
    replay = scenario.post("approve", APPROVALS[0], 409)
    assert replay.json()["error"] == "token_replayed"
    with closing(sqlite3.connect(scenario.config.db_path)) as db:
        rows = db.execute("SELECT action,status FROM commands WHERE action='approve'").fetchall()
    assert rows == [("approve", "acked")]
    result = {"status": scenario.job["status"], "submissions": 1,
              "approval_commands": len(rows), "replay_status": 409,
              "replay_error": "token_replayed", "telegram_sent_at": sent_at,
              "approval_arrived_at": arrived_at, "elapsed_s": round(time.monotonic() - START, 3)}
    persist(result)
    return result


def persist(result: dict) -> None:
    """Save a reproducible redacted proof without bearer or view/action tokens."""
    path = ROOT / "deploy" / "phone-proof-result.json"
    path.write_text(json.dumps({"result": result, "requests": REQUESTS}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    demo.create_app = observed_app
    demo.exercise = phone_exercise
    demo.main()
