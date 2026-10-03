"""Base helpers, the token guard, FallbackChannel and the WhatsApp stub."""
from __future__ import annotations

import pytest

from src.operator.channels import ChannelError, ChannelPort, FallbackChannel, NotConfigured, WhatsAppStub
from src.operator.channels.base import (
    CHANNEL_UNREACHABLE, assert_only_view_tokens, build_review_url, clip, normalise_public_url,
)

from .support import HASH, JOB, PUBLIC_URL, RUN, run, sample, tokens


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.events = fail, []

    async def emit(self, event) -> None:
        self.events.append(event)
        if self.fail:
            raise ChannelError("boom")


# ------------------------------------------------------------------ helpers
def test_build_review_url_shape():
    url = build_review_url(PUBLIC_URL + "/", "run a", JOB, "v1.aaaaaaaa.bbbb.cccc")
    assert url == f"{PUBLIC_URL}/r/run%20a/{JOB}?t=v1.aaaaaaaa.bbbb.cccc"
    assert build_review_url(PUBLIC_URL, RUN, None, "t").endswith(f"/r/{RUN}?t=t")


@pytest.mark.parametrize("bad", ["", "cp.example.test", "http://cp.example.test", "https://x/y", "https://x/?q=1", "ftp://x"])
def test_public_url_validation(bad):
    with pytest.raises(ValueError):
        normalise_public_url(bad)


def test_loopback_http_is_allowed_for_development():
    assert normalise_public_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_clip_collapses_whitespace_and_limits_length():
    assert clip("a \n b\t c") == "a b c" and len(clip("x" * 500, 50)) == 50 and clip(None) == ""


def test_token_guard_allows_view_and_blocks_every_other_type():
    service = tokens()
    assert_only_view_tokens(f"link {service.mint('view', RUN)} end")
    blocked = {
        "act": service.mint("act", RUN, job=JOB, action="approve", snapshot_hash=HASH),
        "run": service.mint("run", RUN),
        "evd": service.mint("evd", RUN, job=JOB, field_key="shot1"),
    }
    for kind, token in blocked.items():
        with pytest.raises(ChannelError) as info:
            assert_only_view_tokens(f"https://x/r/{RUN}?t={token}")
        assert info.value.code == "action_token_blocked" and token not in str(info.value), kind


# ----------------------------------------------------------- FallbackChannel
def make(primary_fail=False, web_fail=False, seen=True):
    primary, web, calls = Recorder(primary_fail), Recorder(web_fail), []
    channel = FallbackChannel(
        primary, web,
        web_subscriber_seen=lambda run_id: seen,
        on_unreachable=lambda run_id: calls.append(("unreachable", run_id)),
        on_recovered=lambda run_id: calls.append(("recovered", run_id)),
    )
    return channel, primary, web, calls


def test_primary_success_uses_only_the_primary_and_clears_the_flag():
    channel, primary, web, calls = make()
    run(channel.emit(sample("E07")))
    assert len(primary.events) == 1 and web.events == [] and calls == [("recovered", RUN)]


def test_primary_failure_falls_back_to_web_when_someone_is_watching():
    channel, primary, web, calls = make(primary_fail=True, seen=True)
    run(channel.emit(sample("E07")))  # no raise: the web page shows it
    assert len(web.events) == 1 and calls == []


def test_nobody_reachable_raises_channel_unreachable_so_the_worker_pauses():
    channel, _, web, calls = make(primary_fail=True, seen=False)
    with pytest.raises(ChannelError) as info:
        run(channel.emit(sample("E07")))
    assert info.value.code == CHANNEL_UNREACHABLE == "CHANNEL_UNREACHABLE"
    assert len(web.events) == 1 and calls == [("unreachable", RUN)]


def test_web_failure_too_is_unreachable_even_if_a_viewer_was_seen():
    channel, _, _, calls = make(primary_fail=True, web_fail=True, seen=True)
    with pytest.raises(ChannelError) as info:
        run(channel.emit(sample("E07")))
    assert info.value.code == CHANNEL_UNREACHABLE and calls == [("unreachable", RUN)]


# -------------------------------------------------------------- WhatsApp stub
@pytest.mark.parametrize("enabled", [False, True])
def test_whatsapp_stub_always_raises_not_implemented(enabled):
    stub = WhatsAppStub(enabled=enabled)
    assert isinstance(stub, ChannelPort)
    with pytest.raises(NotImplementedError):
        run(stub.emit(sample("E01")))
    with pytest.raises(ChannelError) as info:
        run(stub.emit(sample("E01")))
    assert isinstance(info.value, NotConfigured)
