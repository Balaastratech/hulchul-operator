"""Web channel: makes an event visible on the review pages and the SSE stream (T-020).

`WebChannel.emit` is the ChannelPort for the web side (CONTROL_PLANE_API.md 2.4 "Carrying the
snapshot" and section 7). For E07/E08 it first posts the review snapshot (W5), then strips
`payload.review_snapshot` and posts the event (W4). Any failure raises `ChannelError`, so the
caller pauses at the gate instead of leaving the user without a review link.

The transport is a sink, so one adapter serves both deployments:

* `HttpSink`  - worker side: bearer-authenticated HTTPS calls to the control plane routes
  (`/api/worker/runs/{run}/events`, `.../jobs/{job}/snapshot`); no query string, no redirects,
  at most 3 attempts with backoff 1/2/4 s.
* `StoreSink` - control plane side: the same two operations straight on
  `control_plane.store.Store` (events table and snapshots), no schema duplicated here. An
  optional `publish(run_id, seq, event)` callback feeds the SSE queue (step 5 wires it).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Mapping, Protocol

import httpx
from pydantic import ValidationError

from .base import (
    CHANNEL_UNREACHABLE,
    AsyncSleep,
    ChannelError,
    normalise_public_url,
)
from control_plane.models import Event, ReviewSnapshot  # swap point: contracts models
from control_plane.store import Store
from control_plane.tokens import CpError

log = logging.getLogger("operator.channels.web")

REVIEW_EVENTS = frozenset({"E07", "E08"})
HTTP_TIMEOUT_S = 10.0
HTTP_BACKOFF_S = (1.0, 2.0, 4.0)
HTTP_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 2_000_000


class EventSink(Protocol):
    async def post_snapshot(
        self, run_id: str, job_id: str, snapshot_hash: str, snapshot: Mapping[str, Any]
    ) -> dict: ...

    async def post_event(self, event: Event) -> dict: ...


# -------------------------------------------------------------------- HTTP
class HttpSink:
    """Worker-side sink. `authorization` is the full header value (`Bearer <CP_WORKER_TOKEN>`)."""

    def __init__(
        self,
        base_url: str,
        authorization: str,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: AsyncSleep = asyncio.sleep,
        attempts: int = HTTP_ATTEMPTS,
        backoff: tuple[float, ...] = HTTP_BACKOFF_S,
    ) -> None:
        if not authorization.strip():
            raise ValueError("authorization is required")
        self._origin = normalise_public_url(base_url)
        self._authorization = authorization
        self._client = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, follow_redirects=False)
        self._sleep = sleep
        self._attempts = max(1, attempts)
        self._backoff = backoff

    def __repr__(self) -> str:  # never show the credential
        return f"HttpSink(origin={self._origin!r})"

    async def post_snapshot(
        self, run_id: str, job_id: str, snapshot_hash: str, snapshot: Mapping[str, Any]
    ) -> dict:
        path = f"/api/worker/runs/{run_id}/jobs/{job_id}/snapshot"
        return await self._post(path, {"snapshot_hash": snapshot_hash, "snapshot": dict(snapshot)})

    async def post_event(self, event: Event) -> dict:
        return await self._post(
            f"/api/worker/runs/{event.run_id}/events", event.model_dump(mode="json")
        )

    async def _post(self, path: str, body: Mapping[str, Any]) -> dict:
        url = self._origin + path  # capabilities never go into URLs: no query string, ever
        headers = {"Authorization": self._authorization}
        code = "cp_unreachable"
        for attempt in range(1, self._attempts + 1):
            try:
                response = await self._client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                code = "cp_unreachable"
                log.warning("control plane call failed attempt=%d error=%s", attempt,
                            type(exc).__name__)
            else:
                if 300 <= response.status_code < 400:
                    raise ChannelError("redirect_refused")
                if response.status_code < 300:
                    return self._parse(response)
                error = self._error_code(response)
                if response.status_code != 429 and response.status_code < 500:
                    raise ChannelError(error, f"http {response.status_code}")  # not retryable
                code = error
                log.warning("control plane call status=%d attempt=%d", response.status_code, attempt)
            if attempt < self._attempts:
                await self._sleep(self._backoff[min(attempt - 1, len(self._backoff) - 1)])
        raise ChannelError(code, "control plane did not accept the event")

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        return error if isinstance(error, str) and error else f"http_{response.status_code}"

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ChannelError("response_too_large")
        try:
            body = response.json() if response.content else {}
        except ValueError:
            raise ChannelError("bad_response") from None
        return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------- store
class StoreSink:
    """Control-plane-side sink: writes through `control_plane.store.Store` (SQLite)."""

    def __init__(
        self,
        store: Store,
        publish: Callable[[str, int, Event], Any] | None = None,
    ) -> None:
        self._store = store
        self._publish = publish

    async def post_snapshot(
        self, run_id: str, job_id: str, snapshot_hash: str, snapshot: Mapping[str, Any]
    ) -> dict:
        try:
            model = ReviewSnapshot.model_validate(dict(snapshot))
        except ValidationError:
            raise ChannelError("schema_invalid", "review_snapshot does not match the schema") from None
        try:
            return await asyncio.to_thread(self._store.put_snapshot, run_id, job_id, snapshot_hash, model)
        except CpError as exc:
            raise ChannelError(exc.code, exc.detail) from None

    async def post_event(self, event: Event) -> dict:
        try:
            result = await asyncio.to_thread(self._store.insert_event, event)
        except CpError as exc:
            raise ChannelError(exc.code, exc.detail) from None
        run = await asyncio.to_thread(self._store.get_run, event.run_id)
        result = {**result, "channel_unreachable": bool(run and run.get("channel_unreachable"))}
        seq = result.get("seq")
        if self._publish is not None and not result.get("duplicate") and isinstance(seq, int):
            try:
                self._publish(event.run_id, seq, event)
            except Exception:  # noqa: BLE001 - a broken SSE queue must not fail the event
                log.exception("publish to the progress stream failed")
        return result


# ----------------------------------------------------------------- channel
class WebChannel:
    """ChannelPort for the web side: snapshot first (E07/E08), then the event."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    async def emit(self, event: Event) -> None:
        payload = dict(event.payload)
        snapshot = payload.pop("review_snapshot", None)  # the body is stored once, by W5
        if snapshot is not None and event.event_id in REVIEW_EVENTS:
            claimed = payload.get("snapshot_hash")
            if not isinstance(snapshot, dict) or not isinstance(claimed, str) or not event.job_id:
                raise ChannelError("payload_invalid", "review_snapshot needs snapshot_hash and job_id")
            await self._sink.post_snapshot(event.run_id, event.job_id, claimed, snapshot)
        stripped = event.model_copy(update={"payload": payload})
        result = await self._sink.post_event(stripped)
        if result.get("channel_unreachable") is True:
            raise ChannelError(CHANNEL_UNREACHABLE, "control plane reports no reachable channel")
