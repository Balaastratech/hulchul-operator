"""T-042 / AUDIT-020: an expired, never-executed approval must not block a fresh approval,
but an approval that led to a submit must stay single-use (D-015, D-030)."""
import secrets

import pytest

from control_plane.models import Event, ReviewSnapshot
from control_plane.store import Store
from control_plane.tokens import CpError, TokenService, token_hash


@pytest.fixture
def env(tmp_path):
    clock = [1_800_000_000]
    store = Store(tmp_path / "cp.sqlite", clock=lambda: clock[0])
    tokens = TokenService(secrets.token_bytes(48), clock=lambda: clock[0])
    snapshot = ReviewSnapshot(fields=[{"field_key": "email", "intended": "a@example.test",
                                       "actual": "a@example.test", "matched": True}])
    store.put_snapshot("R1", "J1", snapshot.content_hash(), snapshot)
    yield store, tokens, snapshot, clock
    store.close()


def approve(store, tokens, snapshot, ttl=None):
    token = tokens.mint("act", "R1", job="J1", action="approve",
                        snapshot_hash=snapshot.content_hash(), ttl=ttl)
    return token, store.consume_act(tokens.verify(token, "act"), token_hash(token))


def test_expired_unacked_command_is_expired_and_reapproval_works(env):
    store, tokens, snapshot, clock = env
    _, first = approve(store, tokens, snapshot, ttl=60)
    clock[0] += 61
    assert store.approval_expired("R1", "J1")
    _, second = approve(store, tokens, snapshot)
    assert second["command_id"] != first["command_id"]
    assert store.get_command(first["command_id"])["status"] == "expired"
    assert [c["command_id"] for c in store.list_queued_commands("R1")[0]] == [second["command_id"]]


def test_expired_approval_token_can_never_be_replayed(env):
    store, tokens, snapshot, clock = env
    old, _ = approve(store, tokens, snapshot, ttl=60)
    clock[0] += 61
    approve(store, tokens, snapshot)
    with pytest.raises(CpError) as err:
        store.consume_act(tokens.verify(old, "act"), token_hash(old))
    assert err.value.code in {"token_replayed", "token_expired"}


def test_approval_inside_window_still_blocks_second_approval(env):
    store, tokens, snapshot, clock = env
    approve(store, tokens, snapshot)
    clock[0] += 60
    with pytest.raises(CpError) as err:
        approve(store, tokens, snapshot)
    assert err.value.code in {"already_approved", "command_pending"}


def test_approval_that_led_to_a_submit_stays_single_use(env):
    store, tokens, snapshot, clock = env
    _, first = approve(store, tokens, snapshot)
    store.insert_event(Event(event_id="E09", run_id="R1", job_id="J1", message="Submitting",
                             created_at="2026-10-04T00:00:00Z", payload={}))
    store.ack("R1", first["command_id"])
    clock[0] += 31 * 60  # long after the 30-minute window
    assert not store.approval_expired("R1", "J1")
    with pytest.raises(CpError) as err:
        approve(store, tokens, snapshot)
    assert err.value.code == "already_approved"
