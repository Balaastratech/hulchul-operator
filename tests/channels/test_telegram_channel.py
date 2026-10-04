"""TelegramChannel outbound behaviour against httpx.MockTransport (no real sends)."""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.operator.channels import BotApi, ChannelError, ChannelPort, ChatGate, InMemoryTelegramState, TelegramChannel
from src.operator.channels.base import _TOKEN_SHAPE

from .support import (
    ALL_EVENT_IDS, BOT_TOKEN, CHAT, HASH, JOB, PUBLIC_URL, RUN, FakeTelegram, Sleeper,
    make_event, run, sample, tokens,
)

LINKED = {"E01": "run", "E02": "run", "E03": "job", "E04": "job", "E05": "job", "E06": "job",
          "E07": "job", "E08": "job", "E10": "job", "E11": "job", "E12": "job", "E13": "run",
          "E14": "run"}


def build(fake: FakeTelegram, *, chats=frozenset({CHAT}), state=None, sleeper=None,
          **api_kwargs) -> TelegramChannel:
    api = BotApi(BOT_TOKEN, client=fake.client(), sleep=sleeper or Sleeper(), **api_kwargs)
    return TelegramChannel(api, chat_ids=chats, public_url=PUBLIC_URL, tokens=tokens(), state=state)


def urls_of(call) -> list[str]:
    found = [b["url"] for row in call.body.get("reply_markup", {}).get("inline_keyboard", []) for b in row]
    return found


# ------------------------------------------------------------- payload shape
def test_e07_payload_shape_and_template():
    fake = FakeTelegram()
    run(build(fake).emit(sample("E07")))
    (call,) = fake.sent()
    assert call.request.url.host == "api.telegram.org"
    assert call.request.url.path == f"/bot{BOT_TOKEN}/sendMessage"
    body = call.body
    assert body["chat_id"] == CHAT
    assert body["parse_mode"] == "HTML"
    assert body["text"].split("\n") == [
        "\U0001f4dd <b>Ready for your review</b>",
        "Role: Backend Engineer \u2014 Acme",
        "Job 2 of 3",
        "\u2705 31 fields filled \u00b7 \u2753 2 need your answer \u00b7 \u23ed 1 left blank by your rules",
        "\u26a0\ufe0f Please check: Why us",
        "Not filled by rule: Gender, Ethnicity",
        "The link works 24 h; your approval is valid 30 min after you press Approve.",
    ]
    button, edit = [b for row in body["reply_markup"]["inline_keyboard"] for b in row]
    assert button["text"] == "Review & approve"  # URL buttons only (D-008), plus "Edit a field"
    assert edit["text"] == "Edit a field" and edit["url"] == button["url"] + "#edit"
    assert re.fullmatch(rf"{re.escape(PUBLIC_URL)}/s/[a-z2-7]{{13}}", button["url"])
    assert button["url"] not in body["text"] and "?t=" not in json.dumps(body)


@pytest.mark.parametrize("kind", ALL_EVENT_IDS)
def test_every_event_disables_preview_and_has_no_callback_buttons(kind):
    fake = FakeTelegram()
    run(build(fake).emit(sample(kind)))
    (call,) = fake.sent()
    assert call.body["disable_web_page_preview"] is True  # S5: never a preview-enabled link
    assert "callback_data" not in json.dumps(call.body)
    buttons = urls_of(call)
    if kind in LINKED:
        # link offered as URL buttons to ONE page; the review messages add "Edit a field"
        assert len(buttons) == (2 if kind in ("E07", "E08") else 1)
        assert len({u.split("#")[0] for u in buttons}) == 1
    else:
        assert buttons == [] and "http" not in call.body["text"]
    assert call.body["text"].strip()


def test_run_level_events_link_to_the_run_page_and_job_events_to_the_job_page():
    fake = FakeTelegram()
    channel = build(fake)
    for kind in ("E01", "E07"):
        run(channel.emit(sample(kind)))
    run_url, job_url = (urls_of(c)[0] for c in fake.sent())
    known = [(RUN, None), (RUN, JOB)]
    assert tokens().match_short_code(urlsplit(run_url).path.split("/")[-1], known)[:2] == (RUN, None)
    assert tokens().match_short_code(urlsplit(job_url).path.split("/")[-1], known)[:2] == (RUN, JOB)


def test_job_event_without_job_id_falls_back_to_the_run_page():
    fake = FakeTelegram()
    run(build(fake).emit(sample("E12", job=None)))
    code = urlsplit(urls_of(fake.sent()[0])[0]).path.split("/")[-1]
    assert tokens().match_short_code(code, [(RUN, None), (RUN, JOB)])[:2] == (RUN, None)


# ------------------------------------------------- only a VIEW token leaves
@pytest.mark.parametrize("kind", sorted(LINKED))
def test_links_carry_only_a_view_token(kind):
    fake = FakeTelegram()
    service = tokens()
    run(build(fake).emit(sample(kind)))
    (call,) = fake.sent()
    for url in urls_of(call):  # the "Edit a field" button is the same link plus #edit
        parts = urlsplit(url)
        assert parts.query == "" and parts.path.startswith("/s/")  # an opaque code, no token at all
        assert parts.fragment in ("", "edit")
        assert service.match_short_code(parts.path[3:], [(RUN, None), (RUN, JOB)]) is not None
    assert _TOKEN_SHAPE.findall(call.body["text"] + json.dumps(call.body["reply_markup"])) == []


def test_no_act_or_run_token_can_appear_in_any_sent_request():
    fake = FakeTelegram()
    channel = build(fake)
    for kind in ALL_EVENT_IDS:
        run(channel.emit(sample(kind)))
    service = tokens()
    for call in fake.sent():
        for token in _TOKEN_SHAPE.findall(json.dumps(call.body)):
            assert service.verify(token, "view").typ == "view"


@pytest.mark.parametrize("field", ["message", "payload"])
def test_an_action_token_inside_event_data_is_blocked_before_any_request(field):
    service = tokens()
    act = service.mint("act", RUN, job=JOB, action="approve", snapshot_hash=HASH)
    fake = FakeTelegram()
    if field == "message":
        event = sample("E12", message=f"see {act}")
    else:
        event = sample("E12", blocker=f"token {act}")
    with pytest.raises(ChannelError) as info:
        run(build(fake).emit(event))
    assert info.value.code == "action_token_blocked"
    assert fake.calls == []
    assert act not in str(info.value)


def test_links_supplied_by_the_worker_are_never_forwarded():
    fake = FakeTelegram()
    event = sample("E07", links={"approve": "https://evil.example/approve?t=xyz", "review": "https://evil.example/r"})
    run(build(fake).emit(event))
    (call,) = fake.sent()
    assert "evil.example" not in json.dumps(call.body)
    assert urls_of(call)[0].startswith(PUBLIC_URL + "/")


def test_dynamic_text_is_html_escaped_and_clipped():
    fake = FakeTelegram()
    run(build(fake).emit(sample("E12", blocker="<script>alert(1)</script>" + "x" * 500)))
    text = fake.sent()[0].body["text"]
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert len(text) < 1000


# ------------------------------------------------ tokens stay out of the logs
def test_bot_token_is_never_logged_or_leaked_in_errors(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeTelegram()
    fake.script["sendMessage"] = [(500, {"ok": False, "description": f"oops {BOT_TOKEN}"}),
                                  httpx.ConnectError(f"cannot reach {BOT_TOKEN}"),
                                  (200, {"ok": True, "result": {"message_id": 1}})]
    channel = build(fake)
    run(channel.emit(sample("E07")))
    failing = FakeTelegram()
    failing.script["sendMessage"] = [(500, {"ok": False, "description": BOT_TOKEN})] * 5
    with pytest.raises(ChannelError) as info:
        run(build(failing).emit(sample("E01")))
    assert BOT_TOKEN not in caplog.text
    assert BOT_TOKEN not in str(info.value) and BOT_TOKEN not in repr(info.value)
    assert BOT_TOKEN not in repr(vars(channel)) and "redacted" in repr(channel._api)
    view = re.findall(r"\?t=\S+", caplog.text)
    assert view == []  # view tokens are not logged either


# ------------------------------------------------------------ retry / backoff
def test_retries_server_errors_and_rate_limits_with_backoff():
    fake, sleeper = FakeTelegram(), Sleeper()
    fake.script["sendMessage"] = [
        (500, {"ok": False, "description": "boom"}),
        (429, {"ok": False, "description": "slow down", "parameters": {"retry_after": 7}}),
    ]
    run(build(fake, sleeper=sleeper).emit(sample("E07")))
    assert len(fake.sent()) == 3
    assert sleeper.delays == [1.0, 7.0]  # 1 s backoff, then retry_after beats the 4 s step


def test_network_error_is_retried():
    fake, sleeper = FakeTelegram(), Sleeper()
    fake.script["sendMessage"] = [httpx.ReadTimeout("slow")]
    run(build(fake, sleeper=sleeper).emit(sample("E01")))
    assert len(fake.sent()) == 2 and sleeper.delays == [1.0]


def test_gives_up_after_five_attempts_with_1_4_16_60_backoff():
    fake, sleeper = FakeTelegram(), Sleeper()
    fake.script["sendMessage"] = [(502, {"ok": False, "description": "bad gateway"})] * 9
    with pytest.raises(ChannelError) as info:
        run(build(fake, sleeper=sleeper).emit(sample("E01")))
    assert info.value.code == "telegram_delivery_failed"
    assert len(fake.sent()) == 5
    assert sleeper.delays == [1.0, 4.0, 16.0, 60.0]


def test_client_errors_are_not_retried():
    fake, sleeper = FakeTelegram(), Sleeper()
    fake.script["sendMessage"] = [(403, {"ok": False, "description": "bot was blocked"})]
    with pytest.raises(ChannelError):
        run(build(fake, sleeper=sleeper).emit(sample("E01")))
    assert len(fake.sent()) == 1 and sleeper.delays == []


def test_rejected_button_url_is_retried_once_without_the_keyboard_but_still_no_preview():
    fake = FakeTelegram()
    fake.script["sendMessage"] = [(400, {"ok": False, "description": "Bad Request: BUTTON_URL_INVALID"})]
    run(build(fake).emit(sample("E07")))
    first, second = fake.sent()
    assert "reply_markup" in first.body and "reply_markup" not in second.body
    assert second.body["disable_web_page_preview"] is True
    assert "/s/" in second.body["text"]


def test_request_timeout_is_15_seconds():
    fake = FakeTelegram()
    seen = []

    def hook(call):
        seen.append(call.request.extensions.get("timeout"))

    fake.hook = hook
    run(build(fake).emit(sample("E01")))
    assert seen and seen[0]["read"] == 15.0


# -------------------------------------------------------------- dedup/allowlist
def test_resending_the_same_event_does_not_duplicate_the_message():
    fake = FakeTelegram()
    channel = build(fake)
    event = sample("E07")
    run(channel.emit(event))
    run(channel.emit(event))
    later = sample("E07", created_at=datetime(2026, 10, 3, 12, 5, tzinfo=UTC))  # same snapshot hash
    run(channel.emit(later))
    assert len(fake.sent()) == 1
    run(channel.emit(sample("E07", snapshot_hash="b" * 64)))  # new snapshot: a new message
    assert len(fake.sent()) == 2


def test_repeated_pause_events_at_different_times_are_separate_messages():
    fake = FakeTelegram()
    channel = build(fake)
    run(channel.emit(sample("E13")))
    run(channel.emit(sample("E13", created_at=datetime(2026, 10, 3, 13, 0, tzinfo=UTC))))
    assert len(fake.sent()) == 2


def test_messages_go_only_to_allowlisted_chats_and_partial_failure_is_tolerated():
    fake = FakeTelegram()
    fake.hook = lambda call: (
        httpx.Response(403, json={"ok": False, "description": "blocked"}) if call.body.get("chat_id") == "2" else None
    )
    run(build(fake, chats=frozenset({"1", "2"})).emit(sample("E01")))
    assert sorted(c.body["chat_id"] for c in fake.sent()) == ["1", "2"]  # nothing else is ever targeted


def test_all_chats_failing_raises_so_the_caller_can_fall_back():
    fake = FakeTelegram()
    fake.hook = lambda call: httpx.Response(403, json={"ok": False, "description": "blocked"})
    with pytest.raises(ChannelError):
        run(build(fake, chats=frozenset({"1", "2"})).emit(sample("E01")))


def test_an_empty_allowlist_is_refused_at_construction():
    with pytest.raises(ValueError):
        TelegramChannel(BotApi(BOT_TOKEN, client=FakeTelegram().client()), chat_ids=frozenset(),
                        public_url=PUBLIC_URL, tokens=tokens())


def test_public_url_must_be_https_origin():
    for bad in ("http://cp.example.test", "https://cp.example.test/x", "https://cp.example.test/?a=1", ""):
        with pytest.raises(ValueError):
            TelegramChannel(BotApi(BOT_TOKEN, client=FakeTelegram().client()), chat_ids=frozenset({CHAT}),
                            public_url=bad, tokens=tokens())


def test_e06_message_is_bound_to_its_gate_for_replies():
    fake, state = FakeTelegram(), InMemoryTelegramState()
    run(build(fake, state=state).emit(sample("E06")))
    message_id = 101
    assert state.resolve_link(message_id) == ChatGate(RUN, JOB, "notice_period")
    run(build(FakeTelegram(), state=state).emit(sample("E07")))  # other events bind nothing
    assert len(state.links) == 1


def test_channel_satisfies_the_channel_port():
    assert isinstance(build(FakeTelegram()), ChannelPort)


def test_bot_api_requires_a_token():
    with pytest.raises(ValueError):
        BotApi("")


def test_message_without_optional_payload_keys_degrades_gracefully():
    fake = FakeTelegram()
    run(build(fake).emit(make_event("E07", snapshot_hash=HASH)))  # no counts/flagged/left_blank
    text = fake.sent()[0].body["text"]
    assert "Ready for your review" in text and "fields filled" not in text
