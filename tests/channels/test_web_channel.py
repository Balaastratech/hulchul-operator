"""WebChannel over the real SQLite store (StoreSink) and over HTTP (HttpSink, MockTransport)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import httpx
import pytest

from control_plane.models import FieldResult, ReviewSnapshot
from control_plane.store import Store
from src.operator.channels import ChannelError, ChannelPort, HttpSink, StoreSink, WebChannel

from .support import JOB, RUN, Sleeper, make_event, run, sample

BEARER = "Bearer " + "w" * 40
ORIGIN = "https://cp.example.test"


def snapshot() -> ReviewSnapshot:
    return ReviewSnapshot(fields=[FieldResult(field_key="email", intended="a@example.test",
                                              actual="a@example.test", matched=True)])


def review_event(kind="E07", snap: ReviewSnapshot | None = None, claimed: str | None = None, **extra):
    snap = snap or snapshot()
    return make_event(kind, snapshot_hash=claimed or snap.content_hash(),
                      review_snapshot=snap.model_dump(mode="json"),
                      counts={"filled": 1, "total": 1, "need_user": 0, "skipped": 0}, **extra)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cp.sqlite")
    yield s
    s.close()


def stored_events(store: Store) -> list[dict]:
    with closing(sqlite3.connect(store.path)) as conn:
        return [json.loads(row[0]) for row in conn.execute("SELECT body_json FROM events ORDER BY seq")]


# ------------------------------------------------------------------ StoreSink
def test_e07_posts_the_snapshot_first_then_the_event_without_the_snapshot_body(store):
    snap = snapshot()
    channel = WebChannel(StoreSink(store))
    event = review_event(snap=snap)
    run(channel.emit(event))
    current = store.get_current_snapshot(RUN, JOB)
    assert current is not None and current[0] == snap.content_hash()
    (body,) = stored_events(store)
    assert body["event_id"] == "E07" and "review_snapshot" not in body["payload"]
    assert body["payload"]["snapshot_hash"] == snap.content_hash()
    assert "review_snapshot" in event.payload  # the caller's event is not mutated


def test_emit_is_idempotent_and_publishes_only_new_events(store):
    published = []
    channel = WebChannel(StoreSink(store, publish=lambda run_id, seq, ev: published.append((run_id, seq, ev.event_id))))
    event = review_event()
    run(channel.emit(event))
    run(channel.emit(event))
    assert len(stored_events(store)) == 1 and published == [(RUN, 1, "E07")]


def test_a_broken_progress_queue_does_not_fail_delivery(store):
    def boom(*_):
        raise RuntimeError("sse down")

    run(WebChannel(StoreSink(store, publish=boom)).emit(sample("E01")))
    assert len(stored_events(store)) == 1


def test_e07_without_a_snapshot_raises_so_the_caller_pauses(store):
    with pytest.raises(ChannelError) as info:
        run(WebChannel(StoreSink(store)).emit(sample("E07")))  # payload has snapshot_hash but no body
    assert info.value.code == "snapshot_unknown" and stored_events(store) == []


def test_hash_mismatch_is_rejected_and_nothing_is_stored(store):
    with pytest.raises(ChannelError) as info:
        run(WebChannel(StoreSink(store)).emit(review_event(claimed="f" * 64)))
    assert info.value.code == "hash_mismatch"
    assert stored_events(store) == [] and store.get_current_snapshot(RUN, JOB) is None


def test_invalid_snapshot_body_is_a_channel_error(store):
    event = make_event("E07", snapshot_hash="a" * 64, review_snapshot={"fields": "nope"})
    with pytest.raises(ChannelError) as info:
        run(WebChannel(StoreSink(store)).emit(event))
    assert info.value.code == "schema_invalid"


def test_review_event_without_job_id_or_hash_is_refused_locally(store):
    snap = snapshot()
    for event in (review_event(job=None), make_event("E07", review_snapshot=snap.model_dump(mode="json"))):
        with pytest.raises(ChannelError) as info:
            run(WebChannel(StoreSink(store)).emit(event))
        assert info.value.code == "payload_invalid"


def test_other_events_drop_a_stray_review_snapshot(store):
    run(WebChannel(StoreSink(store)).emit(
        make_event("E12", blocker="x", review_snapshot=snapshot().model_dump(mode="json"))))
    (body,) = stored_events(store)
    assert "review_snapshot" not in body["payload"] and store.get_current_snapshot(RUN, JOB) is None


def test_channel_unreachable_flag_makes_emit_raise_after_storing(store):
    channel = WebChannel(StoreSink(store))
    run(channel.emit(sample("E01")))
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("UPDATE runs SET channel_unreachable=1 WHERE run_id=?", (RUN,))
        conn.commit()
    with pytest.raises(ChannelError) as info:
        run(channel.emit(sample("E13")))
    assert info.value.code == "CHANNEL_UNREACHABLE"
    assert len(stored_events(store)) == 2  # the event is stored (and visible on the web) regardless


# ------------------------------------------------------------------- HttpSink
class Cp:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.script: list[object] = []

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item  # type: ignore[return-value]
        return httpx.Response(200, json={"accepted": True, "duplicate": False, "seq": 1})


def http_channel(cp: Cp, sleeper: Sleeper | None = None) -> WebChannel:
    return WebChannel(HttpSink(ORIGIN, BEARER, client=cp.client(), sleep=sleeper or Sleeper()))


def test_http_posts_snapshot_then_event_to_the_worker_routes_with_the_bearer():
    cp, snap = Cp(), snapshot()
    run(http_channel(cp).emit(review_event(snap=snap)))
    snapshot_req, event_req = cp.requests
    assert snapshot_req.url == f"{ORIGIN}/api/worker/runs/{RUN}/jobs/{JOB}/snapshot"
    assert event_req.url == f"{ORIGIN}/api/worker/runs/{RUN}/events"
    for request in cp.requests:
        assert request.method == "POST" and request.url.query == b""  # no capability in any URL
        assert request.headers["authorization"] == BEARER
    envelope = json.loads(snapshot_req.content)
    assert envelope == {"snapshot_hash": snap.content_hash(), "snapshot": snap.model_dump(mode="json")}
    body = json.loads(event_req.content)
    assert body["event_id"] == "E07" and "review_snapshot" not in body["payload"]
    assert body["payload"]["snapshot_hash"] == snap.content_hash()


def test_http_other_events_go_to_the_events_route_only():
    cp = Cp()
    run(http_channel(cp).emit(sample("E01")))
    assert [r.url.path for r in cp.requests] == [f"/api/worker/runs/{RUN}/events"]


def test_http_retries_5xx_with_1_2_4_backoff_then_succeeds():
    cp, sleeper = Cp(), Sleeper()
    cp.script = [httpx.Response(503, json={"error": "busy"}), httpx.ConnectError("down")]
    run(http_channel(cp, sleeper).emit(sample("E01")))
    assert len(cp.requests) == 3 and sleeper.delays == [1.0, 2.0]


def test_http_gives_up_after_three_attempts():
    cp, sleeper = Cp(), Sleeper()
    cp.script = [httpx.Response(503, json={"error": "busy"})] * 5
    with pytest.raises(ChannelError) as info:
        run(http_channel(cp, sleeper).emit(sample("E01")))
    assert info.value.code == "busy" and len(cp.requests) == 3 and sleeper.delays == [1.0, 2.0]


def test_http_snapshot_failure_means_the_event_is_never_posted():
    cp = Cp()
    cp.script = [httpx.Response(422, json={"error": "hash_mismatch", "detail": ""})]
    with pytest.raises(ChannelError) as info:
        run(http_channel(cp).emit(review_event()))
    assert info.value.code == "hash_mismatch" and len(cp.requests) == 1  # 4xx: no retry, no event


@pytest.mark.parametrize("status,code", [(401, "invalid_bearer"), (409, "snapshot_unknown"), (422, "payload_invalid")])
def test_http_client_errors_are_not_retried_and_keep_the_cp_error_code(status, code):
    cp, sleeper = Cp(), Sleeper()
    cp.script = [httpx.Response(status, json={"error": code, "detail": ""})]
    with pytest.raises(ChannelError) as info:
        run(http_channel(cp, sleeper).emit(sample("E12")))
    assert info.value.code == code and len(cp.requests) == 1 and sleeper.delays == []


def test_http_redirects_are_refused():
    cp = Cp()
    cp.script = [httpx.Response(302, headers={"location": "https://evil.example/"})]
    with pytest.raises(ChannelError) as info:
        run(http_channel(cp).emit(sample("E01")))
    assert info.value.code == "redirect_refused" and len(cp.requests) == 1


def test_http_response_reporting_channel_unreachable_raises():
    cp = Cp()
    cp.script = [httpx.Response(200, json={"accepted": True, "channel_unreachable": True})]
    with pytest.raises(ChannelError) as info:
        run(http_channel(cp).emit(sample("E01")))
    assert info.value.code == "CHANNEL_UNREACHABLE"


def test_http_sink_requires_https_and_a_credential_and_hides_it():
    with pytest.raises(ValueError):
        HttpSink("http://cp.example.test", BEARER)
    with pytest.raises(ValueError):
        HttpSink(ORIGIN, "  ")
    assert BEARER not in repr(HttpSink(ORIGIN, BEARER, client=Cp().client()))


def test_web_channel_satisfies_the_channel_port(store):
    assert isinstance(WebChannel(StoreSink(store)), ChannelPort)
