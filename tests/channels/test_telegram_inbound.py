"""Telegram inbound: allowlist, reply-bound answers only, long-poll mechanics."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.operator.channels import BotApi, ChatGate, InMemoryTelegramState, TelegramInbound, make_telegram
from src.operator.channels.telegram import NOT_A_REPLY_TEXT, PollConflict
from control_plane.config import load_config

from .support import BOT_TOKEN, CHAT, JOB, PUBLIC_URL, RUN, FakeTelegram, Sleeper, run, tokens

GATE_MSG = 500  # message id of the bot's E06 question


def build(fake: FakeTelegram, state: InMemoryTelegramState | None = None):
    state = state or InMemoryTelegramState(active_run=RUN)
    state.remember_link(GATE_MSG, ChatGate(RUN, JOB, "notice_period"))
    api = BotApi(BOT_TOKEN, client=fake.client(), sleep=Sleeper())
    inbound = TelegramInbound(api, chat_ids=frozenset({CHAT}), state=state, tokens=tokens(),
                              public_url=PUBLIC_URL, sleep=Sleeper())
    return inbound, state


def message(text, *, chat_id=CHAT, chat_type="private", reply_to=None, update_id=1):
    msg = {"message_id": 9, "chat": {"id": int(chat_id), "type": chat_type}, "text": text}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": update_id, "message": msg}


def replies(fake):
    return [c.body["text"] for c in fake.sent()]


# ---------------------------------------------------------------- allowlist
@pytest.mark.parametrize("kwargs", [
    {"chat_id": "999"},                       # not on the allowlist
    {"chat_type": "group"},                   # group chats are never accepted
    {"chat_type": "supergroup"},
    {"chat_id": "999", "reply_to": GATE_MSG},  # a stranger replying to the question
])
def test_updates_from_non_allowlisted_chats_are_dropped_silently(kwargs, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("hello", **kwargs)))
    run(inbound.handle_update(message("/status", **kwargs)))
    assert fake.calls == [] and state.submitted == []  # not even a reply
    assert "999" not in caplog.text and CHAT not in caplog.text  # no ids in the log


def test_malformed_updates_are_ignored():
    fake = FakeTelegram()
    inbound, state = build(fake)
    for update in ({}, {"message": "x"}, {"message": {"chat": "x"}}, {"message": {"chat": {"id": 1}}},
                   {"edited_message": {"chat": {"id": int(CHAT), "type": "private"}, "text": "x"}}):
        run(inbound.handle_update(update))
    assert fake.calls == [] and state.submitted == []


# ------------------------------------------------- what text may (and may not) do
def test_reply_to_the_question_becomes_exactly_one_answer():
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("30 days", reply_to=GATE_MSG)))
    assert state.submitted == [(ChatGate(RUN, JOB, "notice_period"), "answer", "30 days")]
    run(inbound.handle_update(message("60 days", reply_to=GATE_MSG)))  # the gate is closed now
    assert len(state.submitted) == 1
    assert "no longer open" in replies(fake)[-1]


@pytest.mark.parametrize("text", ["yes", "ok", "approve", "APPROVE", "submit it", "edit notice_period 10"])
def test_plain_text_never_approves_or_edits(text):
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message(text)))  # not a reply to a question
    assert state.submitted == []
    assert replies(fake) == [NOT_A_REPLY_TEXT]


def test_approving_words_as_a_reply_are_just_an_answer_value_never_an_approve():
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("approve", reply_to=GATE_MSG)))
    assert [action for _, action, _ in state.submitted] == ["answer"]  # chat can only ever answer/skip


@pytest.mark.parametrize("command", ["/approve", "/edit x y", "/submit", "/cancel", "/handoff_done", "/pause"])
def test_no_mutating_slash_commands_exist(command):
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message(command, reply_to=GATE_MSG)))
    assert state.submitted == []
    assert replies(fake) == [NOT_A_REPLY_TEXT]


def test_reply_to_an_unrelated_message_is_not_an_answer():
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("30 days", reply_to=12345)))
    assert state.submitted == [] and replies(fake) == [NOT_A_REPLY_TEXT]


def test_skip_as_a_reply_creates_a_skip_only_for_that_question():
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("/skip", reply_to=GATE_MSG)))
    assert state.submitted == [(ChatGate(RUN, JOB, "notice_period"), "skip", None)]
    run(inbound.handle_update(message("/skip")))  # not a reply: nothing happens
    assert len(state.submitted) == 1


def test_overlong_answer_is_refused_not_truncated():
    fake = FakeTelegram()
    inbound, state = build(fake)
    run(inbound.handle_update(message("x" * 2001, reply_to=GATE_MSG)))
    assert state.submitted == [] and "too long" in replies(fake)[0]


def test_status_sends_a_fresh_run_level_view_link():
    fake = FakeTelegram()
    inbound, _ = build(fake)
    run(inbound.handle_update(message("/status")))
    (call,) = fake.sent()
    assert call.body["disable_web_page_preview"] is True and "callback_data" not in json.dumps(call.body)
    url = call.body["reply_markup"]["inline_keyboard"][0][0]["url"]
    assert urlsplit(url).path == f"/r/{RUN}" and list(parse_qs(urlsplit(url).query)) == ["t"]
    claims = tokens().verify(parse_qs(urlsplit(url).query)["t"][0], "view")
    assert claims.typ == "view" and claims.job is None


def test_status_without_an_active_run_and_help_commands():
    fake = FakeTelegram()
    inbound, _ = build(fake, InMemoryTelegramState(active_run=None))
    for text in ("/status", "/start", "/help@hulchul_bot"):
        run(inbound.handle_update(message(text)))
    out = replies(fake)
    assert "no active run" in out[0] and "review page" in out[1] and "review page" in out[2]
    assert all("http" not in t for t in out)


# ------------------------------------------------------------- long polling
def test_poll_once_uses_offset_long_poll_and_saves_the_next_offset():
    fake = FakeTelegram()
    inbound, state = build(fake)
    state.offset = 40
    fake.script["getUpdates"] = [(200, {"ok": True, "result": [
        message("30 days", reply_to=GATE_MSG, update_id=41), message("hi", update_id=42)]})]
    assert run(inbound.poll_once()) == 2
    (poll,) = fake.sent("getUpdates")
    assert poll.body == {"timeout": 50, "allowed_updates": ["message"], "offset": 40}
    assert state.offset == 43 and len(state.submitted) == 1
    assert poll.request.extensions["timeout"]["read"] > 50  # client timeout exceeds the long poll


def test_a_failing_update_does_not_lose_the_offset_or_stop_the_round():
    fake = FakeTelegram()
    inbound, state = build(fake)

    class Boom(InMemoryTelegramState):
        def submit_reply(self, gate, action, text):
            raise RuntimeError("db locked")

    inbound._state = boom = Boom()
    boom.remember_link(GATE_MSG, ChatGate(RUN, JOB, "notice_period"))
    fake.script["getUpdates"] = [(200, {"ok": True, "result": [message("x", reply_to=GATE_MSG, update_id=5),
                                                               message("y", update_id=6)]})]
    assert run(inbound.poll_once()) == 2
    assert boom.offset == 7


def test_start_deletes_the_webhook_and_a_409_conflict_stops_polling():
    fake = FakeTelegram()
    inbound, _ = build(fake)
    fake.script["getUpdates"] = [(409, {"ok": False, "description": "Conflict: terminated by other getUpdates"})]
    with pytest.raises(PollConflict):
        run(inbound.poll_once())

    fake2 = FakeTelegram()
    inbound2, _ = build(fake2)
    fake2.script["getUpdates"] = [(409, {"ok": False, "description": "Conflict"})]
    stop = asyncio.Event()
    run(inbound2.run_forever(stop))  # returns instead of looping forever
    assert [c.method for c in fake2.calls] == ["deleteWebhook", "getUpdates"]


def test_run_forever_backs_off_on_errors_and_stops_on_request():
    fake = FakeTelegram()
    sleeper = Sleeper()
    state = InMemoryTelegramState(active_run=RUN)
    api = BotApi(BOT_TOKEN, client=fake.client(), sleep=sleeper)
    inbound = TelegramInbound(api, chat_ids=frozenset({CHAT}), state=state, tokens=tokens(),
                              public_url=PUBLIC_URL, sleep=sleeper)
    fake.script["getUpdates"] = [httpx.ConnectError("down"), httpx.ConnectError("down")]

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(inbound.run_forever(stop))
        for _ in range(500):
            await asyncio.sleep(0.01)
            if len(fake.sent("getUpdates")) >= 3:
                break
        stop.set()
        await asyncio.wait_for(task, 5)

    run(scenario())
    assert sleeper.delays[:2] == [1.0, 2.0]


# ------------------------------------------------------------------ wiring
def _config(**extra):
    env = {"CP_SIGNING_KEY": "s" * 40, "CP_WORKER_TOKEN": "w" * 40, "CP_BASE_URL": PUBLIC_URL, **extra}
    return load_config(environ=env)


def test_make_telegram_builds_both_halves_from_config_and_is_none_when_disabled():
    assert make_telegram(_config(), tokens(), InMemoryTelegramState()) is None
    pair = make_telegram(_config(TELEGRAM_BOT_TOKEN=BOT_TOKEN, TELEGRAM_CHAT_ID=f"{CHAT}, 77"),
                         tokens(), InMemoryTelegramState(), client=FakeTelegram().client())
    assert pair is not None
    channel, inbound = pair
    assert channel._chat_ids == ("4242", "77") and inbound._chat_ids == frozenset({"4242", "77"})
    assert channel._public_url == PUBLIC_URL
