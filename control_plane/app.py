"""FastAPI app factory for the control plane (CONTROL_PLANE_API.md sections 1.2, 2, 4).

`create_app(config)` wires the SQLite store, the token service and the routers. It never
reads the environment or a .env file: the caller builds a `Config` (see config.py).

Cross-cutting rules enforced here (section 1.2):
- error bodies are `{"error": "<code>", "detail": "<short text>"}`;
- no redirects (`redirect_slashes=False`, so a trailing slash is a plain 404);
- `Cache-Control: no-store` on every response unless a route set its own;
- no CORS headers, no interactive docs, no OpenAPI schema.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Config, ConfigError
from .routes import worker as worker_routes
from .store import Store
from .tokens import CpError, TokenService

_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


class NoStoreMiddleware:
    """Adds `Cache-Control: no-store` to every HTTP response that has no cache header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_header)


async def _cp_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CpError)
    return JSONResponse(
        {"error": exc.code, "detail": exc.detail},
        status_code=exc.status,
        headers=getattr(exc, "headers", None),
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        {"error": _HTTP_ERROR_CODES.get(exc.status_code, "http_error"), "detail": ""},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def create_app(
    config: Config,
    *,
    store: Store | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the app. Fails closed (ConfigError) if no worker credential is configured."""
    if config.worker_token is None and not config.dev_allow_unauth_worker:
        raise ConfigError("CP_WORKER_TOKEN", "missing")
    owns_store = store is None
    active_store = store if store is not None else Store(config.db_path, clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owns_store:
            active_store.close()

    app = FastAPI(
        title="Hulchul control plane",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.store = active_store
    app.state.tokens = TokenService(config.signing_key, config.signing_key_previous, clock)
    app.state.evidence_dir = config.db_path.parent / "evidence"

    app.add_exception_handler(CpError, _cp_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_middleware(NoStoreMiddleware)
    app.include_router(worker_routes.router)
    return app
