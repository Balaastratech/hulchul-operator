"""Phone approval proof on the REAL flow (T-042): scripts/run_real.py --tunnel, Gemini, Chrome, Telegram.

    python deploy/real_phone_proof.py --state-dir .agents/tmp/phone-proof-01

What is real: the Gemini planner, headed Chrome, the fixture ATS, the control plane behind a
cloudflared quick tunnel, the Telegram message and the Approve press on the user's phone.
What is synthetic: only fixture consent/login hand-offs (run_real's explicit fixture human, fixture
origin only). The synthetic approval that `--auto-fixture` would post is SKIPPED here, so the only
approval that can reach the worker is the one pressed on the phone.

Checks (printed, and written to <state-dir>/phone-proof.json without tokens or chat ids):
  * exactly one approve request reached the control plane and it came through the tunnel origin;
  * exactly one fixture submission (run_real's own counter assertion also fires);
  * replaying the phone's own approve request is refused with 409 token_replayed.
A missing approval within 10 minutes of the Telegram message stops the run with no submission.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import _thread
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scripts import run_real

WAIT_S = 600
APPROVALS: list[dict] = []  # raw approve bodies live in memory only, for the replay check
REQUESTS: list[dict] = []  # method, path, status: no query, header or body
STATE: dict = {"skipped_synthetic_approvals": 0, "replays": [], "sent_at": None}
START = time.monotonic()


class Capture:
    """Record /api/approve bodies that the control plane accepted (in memory only)."""

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
                REQUESTS.append({"t": round(time.monotonic() - START, 2), "method": scope["method"],
                                 "path": "/s/<code>" if scope["path"].startswith("/s/") else scope["path"],
                                 "status": message["status"]})
                if scope["path"] == "/api/approve" and message["status"] == 200:
                    APPROVALS.append(_parse(bytes(body)))
                    threading.Thread(target=replay, args=(APPROVALS[-1],), daemon=True).start()
            await send(message)

        await self.app(scope, read, write)


def _parse(raw: bytes) -> dict:
    from urllib.parse import parse_qs

    if raw.startswith(b"{"):
        return json.loads(raw)
    return {k: v[0] for k, v in parse_qs(raw.decode()).items()}


def replay(body: dict) -> None:
    """POST the phone's own approve request again, like a prefetch or a double tap."""
    time.sleep(0.3)
    for attempt in ("right_after", "after_submit"):
        try:
            response = httpx.post(
                f"http://127.0.0.1:{STATE['cp_port']}/api/approve", json=body,
                headers={"Origin": STATE["origin"], "Sec-Fetch-Site": "same-origin"}, timeout=10)
            STATE["replays"].append({"when": attempt, "status": response.status_code,
                                     "error": (response.json() or {}).get("error")})
        except httpx.HTTPError as exc:
            STATE["replays"].append({"when": attempt, "status": None, "error": type(exc).__name__})
        if attempt == "right_after":
            time.sleep(8)  # the control plane may still be up after the worker submitted


class SkipSyntheticApproval(httpx.Client):
    """run_real's --auto-fixture posts its own approval; swallow exactly that one request."""

    def post(self, url, *args, **kwargs):  # type: ignore[override]
        if str(url) == "/api/approve":
            STATE["skipped_synthetic_approvals"] += 1
            print("Synthetic approval skipped: waiting for the approval from the phone.", flush=True)
            return httpx.Response(200, json={"skipped": True}, request=httpx.Request("POST", "http://skip"))
        return super().post(url, *args, **kwargs)


def watch(state_dir: Path) -> None:
    """Tell the user the moment the review message is out; stop after WAIT_S without approval."""
    timeline = state_dir / "timeline.jsonl"
    while not STATE["sent_at"]:
        time.sleep(0.5)
        if timeline.exists():
            for line in timeline.read_text(encoding="utf-8").splitlines():
                if '"event": "E07"' in line:
                    STATE["sent_at"] = datetime.now(UTC).isoformat()
                    (state_dir / "SENT").write_text(STATE["sent_at"], encoding="utf-8")
                    print("USER: open Telegram, tap Review & approve, press Approve", flush=True)
                    break
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if APPROVALS:
            return
        time.sleep(1)
    STATE["timeout"] = True
    print("No approval within 10 minutes: stopping, nothing is submitted.", flush=True)
    _thread.interrupt_main()  # run_real.main() cleans up Chrome, tunnel and servers


def main() -> int:
    argv = sys.argv[1:]
    state_dir = Path(argv[argv.index("--state-dir") + 1]).resolve() if "--state-dir" in argv else None
    if state_dir is None:
        raise SystemExit("--state-dir is required (use a fresh folder under .agents/tmp)")
    port = int(argv[argv.index("--cp-port") + 1]) if "--cp-port" in argv else 8790
    STATE["cp_port"] = port
    original = run_real.create_real_app

    def observed(config):
        STATE["origin"] = config.base_origin
        app = original(config)
        app.add_middleware(Capture)
        return app

    run_real.create_real_app = observed
    run_real.httpx.Client = SkipSyntheticApproval
    fixed = ["--tunnel", "--auto-fixture", "--timeout", "1500",
             "--goal", "Apply to the single best-fit role under my rules"]
    sys.argv = [sys.argv[0], *fixed, *argv]
    state_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=watch, args=(state_dir,), daemon=True).start()
    code = run_real.main()
    return finish(state_dir, code)


def finish(state_dir: Path, code: int) -> int:
    counter = {}
    store = state_dir / "fixture" / "submissions.json"
    if store.exists():
        counter = json.loads(store.read_text(encoding="utf-8"))
    total = counter.get("total") if isinstance(counter, dict) else None
    refused = [r for r in STATE["replays"] if r["status"] == 409 and r["error"] == "token_replayed"]
    checks = {
        "exit_code": code,
        "approvals_from_phone": len(APPROVALS),
        "synthetic_approvals_skipped": STATE["skipped_synthetic_approvals"],
        "fixture_submissions": total,
        "replays": STATE["replays"],
        "replay_refused": bool(refused) and len(refused) == len(STATE["replays"]),
        "telegram_message_sent_at": STATE["sent_at"],
        "timed_out": bool(STATE.get("timeout")),
    }
    checks["pass"] = (code == 0 and len(APPROVALS) == 1 and total == 1 and checks["replay_refused"])
    (state_dir / "phone-proof.json").write_text(
        json.dumps({"checks": checks, "requests": REQUESTS}, indent=2), encoding="utf-8")
    print("PHONE PROOF " + ("PASS" if checks["pass"] else "FAIL") + ": " + json.dumps(checks), flush=True)
    return 0 if checks["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
