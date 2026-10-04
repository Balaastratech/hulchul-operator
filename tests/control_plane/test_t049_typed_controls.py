"""T-049: answer and edit forms follow the control type in the field key; radio/checkbox values
cross the control plane as the booleans the graph expects (RT-09). Offline, generated keys only."""
from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from tests.control_plane.test_human_routes import (  # noqa: F401 - cp is the shared fixture
    JOB,
    RUN,
    act,
    commands,
    cp,
    get_job_page,
    post_event,
    ready,
    used_tokens,
)

ORIGIN = {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"}
QUESTION = "Are you eligible to work in the country of this role? *"
YES = f"Yes|radio|{QUESTION}|0"
NO = f"No|radio|{QUESTION}|0"
MODE = "Preferred work mode"
REMOTE, HYBRID, ONSITE = (f"{name}|checkbox|{MODE}|0" for name in ("Remote", "Hybrid", "Onsite"))
TERMS = "Terms *|checkbox||0"
VISA = "Visa status|select||0"
WHY = "Tell us why you want to join|textarea||0"
PHONE = "Phone|text||0"


class Page(HTMLParser):
    """Forms with their hidden fields, inputs, buttons, selects and textareas."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._button: dict | None = None
        self._select: dict | None = None
        self._area: dict | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"action": a.get("action"), "hidden": {}, "inputs": [], "buttons": [],
                          "selects": [], "areas": []}
            self.forms.append(self._form)
        elif self._form is None:
            return
        elif tag == "input":
            if a.get("type") == "hidden":
                self._form["hidden"][a["name"]] = a.get("value", "")
            else:
                self._form["inputs"].append({"type": a.get("type"), "name": a.get("name"),
                                             "value": a.get("value"), "checked": "checked" in a})
        elif tag == "button":
            self._button = {"name": a.get("name"), "value": a.get("value"), "text": ""}
            self._form["buttons"].append(self._button)
        elif tag == "select":
            self._select = {"name": a.get("name"), "options": []}
            self._form["selects"].append(self._select)
        elif tag == "option" and self._select is not None and a.get("value"):
            self._select["options"].append(a["value"])
        elif tag == "textarea":
            self._area = {"name": a.get("name"), "text": ""}
            self._form["areas"].append(self._area)

    def handle_data(self, data):
        if self._button is not None:
            self._button["text"] += data
        if self._area is not None:
            self._area["text"] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "button":
            self._button = None
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._area = None


def forms(cp, action):
    html = get_job_page(cp).text
    return [f for f in Page(html).forms if f["action"] == f"/api/{action}"], html


def visible(html):
    return re.sub(r"<input[^>]*>", "", html)  # hidden inputs carry the real key for the POST


def submit(cp, form, action, **extra):
    data = {**form["hidden"], **extra}
    return cp.client.post(f"/api/{action}", data=data, headers=ORIGIN)


def field(key, intended=None, actual=None, matched=True, **extra):
    return {"field_key": key, "intended": intended, "actual": actual, "matched": matched, **extra}


RADIO_SNAPSHOT = {"fields": [field(YES, True, True), field(NO, None, False)]}
CHECKBOX_SNAPSHOT = {"fields": [field(REMOTE, True, True), field(HYBRID, True, True),
                                field(ONSITE, None, False)]}


def by_key(forms_, key):
    return next(f for f in forms_ if f["hidden"]["field_key"] == key)


# ------------------------------------------------------------ edit: radio group
def test_yes_no_radio_group_is_one_row_with_a_button_per_option(cp):
    ready(cp, RADIO_SNAPSHOT)
    edit, html = forms(cp, "edit")
    text = visible(html)
    assert text.count("<th scope=\"row\">Are you eligible to work in the country of this role?</th>") == 1
    assert [f["buttons"][0]["text"].strip() for f in edit] == ["Selected: Yes", "No"]
    assert all(f["buttons"][0]["name"] == "value" and f["buttons"][0]["value"] == "true" for f in edit)
    assert all(not f["inputs"] and not f["selects"] for f in edit)  # buttons, not text boxes
    assert len({f["hidden"]["token"] for f in edit}) == 2  # each option has its own single-use token
    assert "|radio|" not in text and "Yes|" not in text


def test_choosing_a_radio_option_queues_a_boolean_true_for_that_option(cp):
    ready(cp, RADIO_SNAPSHOT)
    edit, _ = forms(cp, "edit")
    no = by_key(edit, NO)
    response = submit(cp, no, "edit", value=no["buttons"][0]["value"])  # what the browser sends
    assert response.status_code == 200
    (command,) = commands(cp)
    assert (command["action"], command["field_key"], command["value"]) == ("edit", NO, True)
    assert command["value"] is True  # a boolean, not the text "true"


# -------------------------------------------------------- edit: checkbox group
def test_three_option_checkbox_group_has_a_checkbox_per_option(cp):
    ready(cp, CHECKBOX_SNAPSHOT)
    edit, html = forms(cp, "edit")
    assert len(edit) == 3
    boxes = {f["hidden"]["field_key"]: f["inputs"][0] for f in edit}
    assert all(box["type"] == "checkbox" and box["name"] == "value" for box in boxes.values())
    assert [boxes[k]["checked"] for k in (REMOTE, HYBRID, ONSITE)] == [True, True, False]
    text = visible(html)
    assert text.count("<th scope=\"row\">Preferred work mode</th>") == 1
    assert "|checkbox|" not in text


@pytest.mark.parametrize("extra,expected", [({"value": "true"}, True), ({}, False)])
def test_checkbox_submits_true_or_false(cp, extra, expected):
    ready(cp, CHECKBOX_SNAPSHOT)
    edit, _ = forms(cp, "edit")
    response = submit(cp, by_key(edit, ONSITE), "edit", **extra)  # unticked boxes post nothing
    assert response.status_code == 200
    (command,) = commands(cp)
    assert command["field_key"] == ONSITE and command["value"] is expected


# ------------------------------------------------------------- edit: text, textarea
def test_text_and_textarea_fields_keep_their_text_controls(cp):
    ready(cp, {"fields": [field(PHONE, "+91 90000 00000", "+91 90000 00000"),
                          field(WHY, "Because I like payments", "Because I like payments")]})
    edit, html = forms(cp, "edit")
    phone, why = by_key(edit, PHONE), by_key(edit, WHY)
    assert phone["inputs"][0]["type"] == "text" and phone["inputs"][0]["value"] == "+91 90000 00000"
    assert why["areas"][0]["text"].strip() == "Because I like payments" and not why["inputs"]
    text = visible(html)
    assert "Tell us why you want to join" in text and "|textarea|" not in text and "Phone|" not in text
    assert submit(cp, phone, "edit", value="+91 1").status_code == 200
    assert commands(cp)[0]["value"] == "+91 1"


# -------------------------------------------------------------- ask gate: radio
def test_radio_ask_gate_offers_the_asked_option_as_a_true_answer(cp):
    ready(cp, RADIO_SNAPSHOT)
    post_event(cp, "E06", JOB, field_key=YES, question=QUESTION)
    answer, html = forms(cp, "answer")
    (form,) = answer
    assert [b["text"].strip() for b in form["buttons"]] == ["Yes"] and form["buttons"][0]["value"] == "true"
    assert not form["inputs"]  # not a text box
    assert "Are you eligible to work in the country of this role?" in visible(html)
    assert "Other options (No)" in visible(html)  # sibling options are only named, not submittable
    assert submit(cp, form, "answer", value="true").status_code == 200
    (command,) = commands(cp)
    assert (command["action"], command["field_key"], command["value"]) == ("answer", YES, True)


def test_a_radio_option_can_only_be_selected(cp):
    ready(cp, RADIO_SNAPSHOT)
    post_event(cp, "E06", JOB, field_key=YES, question=QUESTION)
    gate = cp.store.get_job(RUN, JOB)
    body = {"token": act(cp, "answer", gate["gate_hash"], field_key=YES), "run_id": RUN,
            "job_id": JOB, "field_key": YES}
    for bad in ("false", "maybe", "", 1, None, ["true"]):
        response = cp.client.post("/api/answer", json={**body, "value": bad}, headers=ORIGIN)
        assert response.status_code == 422, bad
    assert used_tokens(cp) == 0 and commands(cp) == []  # nothing consumed, nothing queued
    ok = cp.client.post("/api/answer", json={**body, "value": "true"}, headers=ORIGIN)
    assert ok.status_code == 200 and commands(cp)[0]["value"] is True


# ----------------------------------------------------------- ask gate: checkbox
def test_checkbox_ask_gate_shows_a_checkbox(cp):
    ready(cp)
    post_event(cp, "E06", JOB, field_key=TERMS, question="Accept the terms?")
    (form,), html = forms(cp, "answer")
    assert form["inputs"][0]["type"] == "checkbox" and form["inputs"][0]["name"] == "value"
    assert "Terms" in visible(html) and "|checkbox|" not in visible(html)
    assert submit(cp, form, "answer", value="true").status_code == 200
    assert commands(cp)[0]["value"] is True


def test_unticked_checkbox_answer_is_false(cp):
    ready(cp)
    post_event(cp, "E06", JOB, field_key=TERMS, question="Accept the terms?")
    (form,), _ = forms(cp, "answer")
    assert submit(cp, form, "answer").status_code == 200
    assert commands(cp)[0]["value"] is False


# ------------------------------------------------------------- ask gate: select
def test_select_ask_gate_is_a_dropdown_only_when_the_event_carries_options(cp):
    ready(cp)
    post_event(cp, "E06", JOB, field_key=VISA, question="Visa?", options=["Citizen", "Work visa"])
    (form,), html = forms(cp, "answer")
    assert form["selects"][0]["name"] == "value" and form["selects"][0]["options"] == ["Citizen", "Work visa"]
    assert not form["inputs"] and "|select|" not in visible(html)
    assert submit(cp, form, "answer", value="Work visa").status_code == 200
    assert commands(cp)[0]["value"] == "Work visa"


def test_select_without_options_and_other_types_fall_back_to_a_text_box(cp):
    for key in (VISA, WHY, "python"):
        ready(cp)
        post_event(cp, "E06", JOB, field_key=key, question="?")
        (form,), html = forms(cp, "answer")
        assert form["inputs"][0]["type"] == "text" and not form["selects"], key
        assert "Your answer for" in html


# ----------------------------------------------------------- the rules still hold
def test_typed_answers_keep_token_origin_and_single_use_rules(cp):
    ready(cp, RADIO_SNAPSHOT)
    post_event(cp, "E06", JOB, field_key=YES, question=QUESTION)
    (form,), _ = forms(cp, "answer")
    data = {**form["hidden"], "value": "true"}
    evil = {"Origin": "https://evil.example"}
    assert cp.client.post("/api/answer", data=data, headers=evil).status_code == 403
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert cp.client.post("/api/answer", data=data, headers=cross).status_code == 403
    other = {**data, "field_key": NO}
    assert cp.client.post("/api/answer", data=other, headers=ORIGIN).status_code == 403  # token is bound to YES
    assert used_tokens(cp) == 0
    assert cp.client.post("/api/answer", data=data, headers=ORIGIN).status_code == 200
    assert cp.client.post("/api/answer", data=data, headers=ORIGIN).status_code == 409  # single use
    assert used_tokens(cp) == 1 and len(commands(cp)) == 1


def test_plain_text_answers_and_edits_are_unchanged(cp):
    ready(cp)
    post_event(cp, "E06", JOB, field_key="notice_period", question="Notice period?")
    gate = cp.store.get_job(RUN, JOB)
    body = {"token": act(cp, "answer", gate["gate_hash"], field_key="notice_period"), "run_id": RUN,
            "job_id": JOB, "field_key": "notice_period", "value": "30 days"}
    assert cp.client.post("/api/answer", json=body, headers=ORIGIN).status_code == 200
    assert commands(cp)[0]["value"] == "30 days"
