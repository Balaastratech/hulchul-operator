"""Control plane and delivery audit: no network, only generated test keys."""
import asyncio
import hashlib
import secrets
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.operator.contracts import Event


@pytest.fixture
def cp(audit_dir):
    # Keep CP imports lazy: tests/control_plane is a same-name legacy package.
    from control_plane.models import ReviewSnapshot
    from control_plane.store import Store
    from control_plane.tokens import TokenService
    clock = [1_800_000_000]
    store = Store(audit_dir / "cp.sqlite", clock=lambda: clock[0])
    tokens = TokenService(secrets.token_bytes(48), clock=lambda: clock[0])
    snapshot = ReviewSnapshot(fields=[{"field_key": "email", "intended": "synthetic@example.test", "actual": "synthetic@example.test", "matched": True}])
    store.put_snapshot("R1", "J1", snapshot.content_hash(), snapshot)
    yield store, tokens, snapshot, clock
    store.close()


def consume(store, tokens, snapshot, action, **kwargs):
    from control_plane.tokens import token_hash
    token = tokens.mint("act", "R1", job="J1", action=action, snapshot_hash=snapshot.content_hash(), field_key="email" if action == "edit" else None, ttl=kwargs.pop("ttl", None))
    return store.consume_act(tokens.verify(token, "act"), token_hash(token), **kwargs)


def test_expired_approval_can_be_reviewed_again(cp):
    store, tokens, snapshot, clock = cp
    consume(store, tokens, snapshot, "approve", ttl=1)
    clock[0] += 2
    # Worker acknowledges the expired command without submitting it.
    command = store.list_queued_commands("R1")[0][0]
    store.ack("R1", command["command_id"])
    consume(store, tokens, snapshot, "approve")


@pytest.mark.xfail(strict=True, reason="AUDIT-021")
def test_noop_edit_returns_to_reviewable_state(cp):
    store, tokens, snapshot, _ = cp
    result = consume(store, tokens, snapshot, "edit", value="synthetic@example.test")
    store.ack("R1", result["command_id"])
    store.put_snapshot("R1", "J1", snapshot.content_hash(), snapshot)
    assert store.get_job("R1", "J1")["review_state"] == "ready"


@pytest.mark.xfail(strict=True, reason="AUDIT-022")
def test_unauth_dev_mode_requires_actual_loopback_host():
    from control_plane.config import ConfigError, load_config
    values = {"CP_ENV": "dev", "CP_DEV_ALLOW_UNAUTH_WORKER": "1", "CP_SIGNING_KEY": secrets.token_hex(32), "CP_BASE_URL": "http://localhost.attacker.invalid"}
    with pytest.raises(ConfigError):
        load_config(values)


@pytest.mark.xfail(strict=True, reason="AUDIT-023")
def test_public_url_refuses_embedded_credentials():
    from src.operator.channels.base import normalise_public_url
    with pytest.raises(ValueError):
        normalise_public_url("https://user:SYNTHETIC_SECRET@fixture.invalid")


@pytest.mark.xfail(strict=True, reason="AUDIT-024")
def test_graph_review_payload_reaches_web_snapshot(audit_dir):
    from control_plane.models import ReviewSnapshot
    from control_plane.store import Store
    from src.operator.channels.web import StoreSink, WebChannel
    store = Store(audit_dir / "web.sqlite")
    snapshot = ReviewSnapshot(fields=[])
    event = Event(event_id="E07", run_id="R1", job_id="J1", message="Synthetic review", created_at=datetime.now(UTC), payload={"snapshot_hash": snapshot.content_hash(), "review": snapshot.model_dump(mode="json")})
    try:
        asyncio.run(WebChannel(StoreSink(store)).emit(event))
        assert store.get_current_snapshot("R1", "J1") is not None
    finally:
        store.close()


@pytest.mark.xfail(strict=True, reason="AUDIT-025")
def test_reply_message_id_collision_cannot_answer_other_chat_gate():
    from src.operator.channels.telegram import (
        ChatGate,
        InMemoryTelegramState,
        TelegramInbound,
    )
    state = InMemoryTelegramState()
    gate_a = ChatGate("R1", "JA", "email")
    gate_b = ChatGate("R1", "JB", "phone")
    # Telegram message ids are scoped to their chat, not to the bot.
    state.remember_link(7, gate_a)
    state.remember_link(7, gate_b)
    class Api:
        async def call(self, *args, **kwargs):
            return {}
    inbound = TelegramInbound(Api(), chat_ids={"1", "2"}, state=state, tokens=None, public_url="https://fixture.invalid")
    asyncio.run(inbound.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "text": "synthetic answer", "reply_to_message": {"message_id": 7}}}))
    assert not state.submitted or state.submitted[0][0] == gate_a


@pytest.mark.xfail(strict=True, reason="AUDIT-026")
def test_uploaded_evidence_link_is_served(cp, audit_dir):
    from control_plane.app import create_app
    from control_plane.config import load_config
    from control_plane.routes.worker import _store_evidence
    store, _tokens, _snapshot, _ = cp
    config = load_config({"CP_SIGNING_KEY": secrets.token_hex(32), "CP_WORKER_TOKEN": secrets.token_hex(32), "CP_BASE_URL": "https://fixture.invalid", "CP_DB_PATH": str(audit_dir / "cp.sqlite")})
    app = create_app(config, store=store)
    image = b"\x89PNG\r\n\x1a\nsynthetic"
    result = _store_evidence(store, app.state.evidence_dir, "R1", "J1", "synthetic.png", hashlib.sha256(image).hexdigest(), "image/png", image)
    token = app.state.tokens.mint("evd", "R1", job="J1", field_key=result["evidence_id"])
    with TestClient(app) as client:
        response = client.get(f"/evidence/{result['evidence_id']}", params={"t": token})
    assert response.status_code == 200
    assert response.content == image
