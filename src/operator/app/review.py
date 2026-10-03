"""Fail-closed fill-only composition around the unmodified control plane."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from control_plane.config import Config
from control_plane.tokens import CpError

DISABLED = "submission disabled for this site (D-014)"


class SubmissionPolicy:
    """Persist worker-computed submission authority, separate from capability tokens."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS policy(run TEXT, job TEXT, digest TEXT, "
                "allowed INTEGER NOT NULL, PRIMARY KEY(run, job))"
            )

    def record(self, run: str, job: str, digest: str, allowed: bool) -> None:
        """Replace authority for exactly the latest reviewed snapshot."""
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO policy VALUES(?,?,?,?)",
                (run, job, digest, int(allowed)),
            )

    def permits(self, run: str, job: str, digest: str | None = None) -> bool:
        """Unknown review/site is always fill-only, including after a CP restart."""
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT digest, allowed FROM policy WHERE run=? AND job=?", (run, job)
            ).fetchone()
        return bool(row and row[1] and (digest is None or digest == row[0]))


class FillOnlyReview(BaseHTTPMiddleware):
    """Hide Approve for public sites and refuse even a forged/manual approval POST."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        policy = request.app.state.submission_policy
        if request.method == "POST" and request.url.path == "/api/approve":
            # Bounded by the same public API limit; reject before reading an unbounded body.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"error": "too_large"}, status_code=413)
            request._body = bytes(
                body
            )  # downstream CP still verifies all bindings/CSRF/single-use
            try:
                if "application/json" in request.headers.get("content-type", ""):
                    import json

                    token = json.loads(body).get("token")
                else:
                    token = parse_qs(body.decode()).get("token", [None])[0]
                claims = (
                    request.app.state.tokens.verify(token, "act")
                    if isinstance(token, str)
                    else None
                )
            except (CpError, ValueError, UnicodeError, AttributeError):
                claims = None
            if (
                claims
                and claims.action == "approve"
                and not policy.permits(claims.run, claims.job, claims.snapshot_hash)
            ):
                return JSONResponse(
                    {"error": "submission_disabled", "detail": DISABLED},
                    status_code=403,
                )
        response = await call_next(request)
        path = request.url.path.split("/")
        if (
            request.method == "GET"
            and len(path) == 4
            and path[1] == "r"
            and response.status_code == 200
            and not policy.permits(path[2], path[3])
        ):
            raw = b"".join([chunk async for chunk in response.body_iterator]).decode(
                "utf-8"
            )
            raw = re.sub(
                r'<form\b[^>]*action="/api/approve"[^>]*>.*?</form>',
                "",
                raw,
                flags=re.DOTALL,
            )
            raw = raw.replace("<h1>", '<p class="note">' + DISABLED + "</p><h1>", 1)
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() != "content-length"
            }
            return Response(raw, status_code=200, headers=headers)
        return response


def create_real_app(config: Config) -> FastAPI:
    """Compose the real-run CP with a local submission policy boundary."""
    from control_plane.app import create_app

    app = create_app(config)
    app.state.submission_policy = SubmissionPolicy(
        config.db_path.parent / "submission-policy.sqlite"
    )
    app.add_middleware(FillOnlyReview)
    return app
