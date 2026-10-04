"""human question names, question counting, neutral hand-off, two buttons."""
from __future__ import annotations

import re

import pytest

from src.operator.channels import BotApi, TelegramChannel
from src.operator.channels.context import ContextResolver, PostingDirectory
from src.operator.channels.messages import handoff_reason, render_event
from src.operator.channels.questions import field_display, question_id, question_name

from .support import BOT_TOKEN, CHAT, PUBLIC_URL, FakeTelegram, Sleeper, run, sample, tokens

ELIGIBLE = "Yes|radio|ARE YOU ELIGIBLE TO WORK IN THE COUNTRY OF THIS ROLE? *|0"
JOIN = "TELL US WHY YOU WANT TO JOIN|textarea||0"
URL = "https://cp.example.test/s/abcdefghijklm"


def channel(fake, public_url=PUBLIC_URL):
    api = BotApi(BOT_TOKEN, client=fake.client(), sleep=Sleeper())
    return TelegramChannel(api, chat_ids=frozenset({CHAT}), public_url=public_url, tokens=tokens(),
                           resolver=ContextResolver(directory=PostingDirectory()))


def text_of(event, url=URL):
    return render_event(event, url, ContextResolver().facts(event)).html


def field(key, *, actual="x", matched=True, **extra):
    return {"field_key": key, "intended": actual, "actual": actual, "matched": matched, **extra}


def review(fields, unanswered=(), **payload):
    snapshot = {"fields": list(fields), "unanswered": list(unanswered)}
    return sample("E07", review_snapshot=snapshot, counts=None, flagged=None, left_blank=None, **payload)


# ------------------------------------------------------------- item 2: names
@pytest.mark.parametrize("key,expected", [
    (ELIGIBLE, "Are you eligible to work in the country of this role?"),  # group is the question
    (JOIN, "Tell us why you want to join"),
    ("Preferred work mode|radio|Preferred work mode|1", "Preferred work mode"),
    ("Terms *|checkbox||0", "Terms"),
    ("LinkedIn URL|text||0", "LinkedIn URL"),
    ("GITHUB URL|text||0", "GitHub URL"),
    ("why_us", "Why us"),
    ("Notice period", "Notice period"),
    ("|file||0", "Unnamed field"),
])
def test_question_name(key, expected):
    assert question_name(key) == expected


def test_field_display_keeps_the_option_for_group_members_only():
    assert field_display(ELIGIBLE) == "Are you eligible to work in the country of this role? \u2014 Yes"
    assert field_display(JOIN) == "Tell us why you want to join"


@pytest.mark.parametrize("kind,extra", [
    ("E06", {"field_key": ELIGIBLE, "label": None}),
    ("E06", {"field_key": "k", "label": None, "question": JOIN}),
    ("E06", {"field_key": JOIN, "label": None}),
])
def test_question_message_never_shows_an_internal_key(kind, extra):
    fake = FakeTelegram()
    run(channel(fake).emit(sample(kind, **extra)))
    (call,) = fake.sent()
    body = call.body["text"]
    assert "|" not in body and "radio" not in body and "textarea" not in body
    assert not re.search(r"\*|\|\d", body)
    assert re.search(r"Question: (Are you eligible to work in the country of this role\?|Tell us why you want to join)", body)


# ------------------------------------------------------- item 6: counting
def test_a_radio_group_is_one_question_even_when_options_are_separate_inputs():
    group = "Preferred work mode"
    fields = [
        field(f"Remote|radio|{group}|0", actual=True),
        field(f"Hybrid|radio|{group}|0", actual=False, matched=True),
        field(f"Onsite|radio|{group}|0", actual=False, matched=True),
    ]
    text = text_of(review(fields, unanswered=[f"Hybrid|radio|{group}|0"]))
    assert "\u2705 1 field filled \u00b7 \u2753 0 need your answer" in text
    assert "Need your answer" not in text.replace("need your answer", "")


def test_an_unanswered_group_is_one_named_question_not_three_inputs():
    group = "Preferred work mode"
    keys = [f"{o}|radio|{group}|0" for o in ("Remote", "Hybrid", "Onsite")]
    fields = [field(k, actual=False, matched=False, reason="Action was ask_user", escalated=True) for k in keys]
    text = text_of(review(fields, unanswered=keys))
    assert "\u2753 1 need your answer" in text
    assert "Need your answer: Preferred work mode" in text


def test_duplicate_upload_inputs_count_once():
    fields = [field("Resume|file||0", actual="cv.pdf"), field("Resume|file||1", actual="cv.pdf")]
    assert question_id(fields[0]["field_key"]) == question_id(fields[1]["field_key"])
    text = text_of(review(fields))
    assert "\u2705 1 field filled" in text


def test_first_five_unanswered_questions_are_listed_by_name():
    unanswered = ["Preferred work mode|radio|Preferred work mode|0", "Terms *|checkbox||0", "notice_period",
                  "Visa status|select||0", "Expected salary|text||0", "Portfolio|text||0", "Referral|text||0"]
    text = text_of(review([field("Email|text||0", actual="a@x.test")], unanswered=unanswered))
    assert "\u2753 7 need your answer" in text
    assert ("Need your answer: Preferred work mode, Terms, Notice period, Visa status, "
            "Expected salary and 2 more") in text
    assert "Portfolio" not in text and "|" not in text


def test_the_manager_example_line():
    unanswered = ["Preferred work mode|radio|Preferred work mode|0", "Terms|checkbox||0"]
    text = text_of(review([], unanswered=unanswered))
    assert "Need your answer: Preferred work mode, Terms" in text


def test_no_unanswered_line_when_everything_is_answered():
    assert "Need your answer:" not in text_of(review([field("Email|text||0")]))


# ------------------------------------------------- item 4: neutral hand-off
def test_legal_confirmation_handoff_is_not_called_a_login():
    event = sample("E04", observed="Legal consent checkbox: I agree to the privacy terms", page_kind="form")
    assert handoff_reason(event) == "legal"
    text = text_of(event)
    assert "Action needed in the browser" in text and "Login needed" not in text
    assert "log in" not in text.lower()
    assert "legal confirmation" in text and text.rstrip().endswith("then press Done.")


def test_login_reason_says_log_in():
    event = sample("E04", observed="Sign-in form with a password field")
    assert handoff_reason(event) == "login"
    text = text_of(event)
    assert "Action needed in the browser" in text and "log in yourself" in text


def test_explicit_reason_wins_and_a_bare_handoff_never_claims_login():
    assert handoff_reason(sample("E04", reason="legal", observed="Sign-in")) == "legal"
    bare = sample("E04", site="acme.example")
    bare = bare.model_copy(update={"payload": {"site": "acme.example"}})
    assert handoff_reason(bare) == "other"
    assert "log in" not in text_of(bare).lower()


def test_captcha_handoff_is_a_human_check():
    event = sample("E05")
    assert handoff_reason(event) == "human_check"
    text = text_of(event)
    assert "Action needed in the browser" in text and "I never solve CAPTCHAs" in text
    assert "log in" not in text.lower()


# --------------------------------------------------- item 7: the second button
def test_review_message_has_review_and_edit_buttons_to_the_same_page():
    fake = FakeTelegram()
    run(channel(fake).emit(sample("E07")))
    (call,) = fake.sent()
    (row,) = call.body["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in row] == ["Review & approve", "Edit a field"]
    assert row[1]["url"] == row[0]["url"] + "#edit"
    assert all("callback_data" not in b for b in row)
    assert re.fullmatch(r"https://cp\.example\.test/s/[A-Za-z0-9_-]{13}", row[0]["url"])


@pytest.mark.parametrize("kind", ["E01", "E04", "E06", "E10", "E14"])
def test_other_messages_keep_a_single_button(kind):
    fake = FakeTelegram()
    event = sample(kind)
    run(channel(fake).emit(event))
    (call,) = fake.sent()
    buttons = [b for row in call.body.get("reply_markup", {}).get("inline_keyboard", []) for b in row]
    assert len(buttons) <= 1


def test_local_address_sends_no_buttons_at_all():
    fake = FakeTelegram()
    run(channel(fake, public_url="http://127.0.0.1:8790").emit(sample("E07")))
    (call,) = fake.sent()
    assert "reply_markup" not in call.body
