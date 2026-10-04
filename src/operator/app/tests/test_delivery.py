"""change items 3, 4 and 5 in the real composition: no duplicate messages, hand-off reason, run facts."""

import asyncio
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.operator.app.data import RealData
from src.operator.app.delivery import DeliveryLog, current_task_id, delivery_key
from src.operator.contracts import Event, FieldSpec, PageState
from src.operator.policy.allowlist import DomainAllowlist

# NOTE: src.operator.app.factory and src.operator.channels import the real `control_plane`
# package. They are imported inside the functions below, never at module level: `src` is
# collected before `tests/control_plane` (a test package of the same name that must be imported
# first), so a module-level import here would break the collection of that whole directory.


def render(evt, url):
    from src.operator.channels.context import ContextResolver
    from src.operator.channels.messages import render_event

    return render_event(evt, url, ContextResolver(env={}).facts(evt)).html


def event(kind="E04", job="job-1", **payload):
    return Event(
        event_id=kind,
        run_id="run-1",
        job_id=job,
        message="Complete the required action in the visible browser; press done",
        payload=payload,
        created_at=datetime.now(UTC),  # differs on every call, like a replayed emit
    )


def bare_channel(tmp_path, **attrs):
    """A RealChannel without browser/web/Telegram wiring; `_deliver` just records."""
    from src.operator.app.factory import RealChannel

    channel = object.__new__(RealChannel)
    channel.delivered = DeliveryLog(tmp_path / "delivered.json")
    channel.goal = None
    channel.run_context = None
    channel.sent = []

    async def deliver(evt):
        channel.sent.append(evt)

    channel._deliver = deliver
    for name, value in attrs.items():
        setattr(channel, name, value)
    return channel


class S(TypedDict):
    n: int


def build_graph(channel, emits):
    """A node shaped like human_handoff/ask_user: emit, THEN interrupt. It loops once more."""
    loop = asyncio.new_event_loop()

    def handoff(state: S) -> S:
        emits.append(1)
        loop.run_until_complete(channel.emit(event("E04")))
        interrupt({"kind": "handoff"})
        return {"n": state["n"] + 1}

    graph = StateGraph(S)
    graph.add_node("handoff", handoff)
    graph.add_edge(START, "handoff")
    graph.add_conditional_edges("handoff", lambda s: "handoff" if s["n"] < 2 else END)
    return graph.compile(checkpointer=InMemorySaver()), loop


def test_resuming_a_node_that_emits_before_interrupt_does_not_message_twice(tmp_path):
    channel, emits = bare_channel(tmp_path), []
    graph, loop = build_graph(channel, emits)
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"n": 0}, config)  # first visit: emit, then the interrupt
    assert len(channel.sent) == 1
    graph.invoke(Command(resume="done"), config)  # replay of the SAME task, then a NEW visit
    graph.invoke(Command(resume="done"), config)  # replay of the second visit, then the end
    loop.close()
    assert len(emits) == 4  # the node body really ran four times ...
    assert len(channel.sent) == 2  # ... but each hand-off was delivered exactly once


def test_outside_a_graph_task_nothing_is_suppressed(tmp_path):
    assert current_task_id() is None
    channel = bare_channel(tmp_path)
    asyncio.run(channel.emit(event()))
    asyncio.run(channel.emit(event()))
    assert len(channel.sent) == 2  # a duplicate is noise, a swallowed hand-off would strand the user


def test_delivery_key_ignores_created_at_but_not_content_or_task():
    a, b = event(), event()
    assert a.created_at != b.created_at
    assert delivery_key(a, "t1") == delivery_key(b, "t1")
    assert delivery_key(a, "t1") != delivery_key(a, "t2")
    assert delivery_key(a, "t1") != delivery_key(event(reason="legal"), "t1")


def test_delivered_keys_survive_a_restart(tmp_path):
    first = DeliveryLog(tmp_path / "delivered.json")
    first.mark("k1")
    second = DeliveryLog(tmp_path / "delivered.json")
    assert second.seen("k1") and not second.seen("k2")
    (tmp_path / "delivered.json").write_text("not json", encoding="utf-8")
    assert not DeliveryLog(tmp_path / "delivered.json").seen("k1")  # corrupt file: start empty


def test_failed_delivery_is_not_remembered_so_it_can_be_retried(tmp_path, monkeypatch):
    channel = bare_channel(tmp_path)
    calls = []

    async def deliver(evt):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("channel down")

    channel._deliver = deliver
    monkeypatch.setattr("src.operator.app.factory.current_task_id", lambda: "task-1")
    try:
        asyncio.run(channel.emit(event()))
    except RuntimeError:
        pass
    asyncio.run(channel.emit(event()))
    asyncio.run(channel.emit(event()))
    assert len(calls) == 2  # retried once, then recognised as delivered


# ------------------------------------------------------------ item 4: reason
class FakeBrowser:
    def __init__(self, state, fields=()):
        self.state, self.fields = state, {f.key: f for f in fields}

    async def classify_page(self):
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


def compose(tmp_path, evt, browser):
    channel = bare_channel(tmp_path, browser=browser)
    return asyncio.run(channel._compose(evt))


def legal_field():
    return FieldSpec(id="c", key="Terms|checkbox||0", label="I agree to the privacy terms", type="checkbox")


def test_handoff_reason_is_read_from_the_page_not_assumed_to_be_a_login(tmp_path):
    cases = [
        (PageState.LOGIN, (), "login"),
        (PageState.CAPTCHA, (), "human_check"),
        (PageState.FORM, [legal_field()], "legal"),
    ]
    for state, fields, expected in cases:
        assert compose(tmp_path, event("E04"), FakeBrowser(state, fields))["reason"] == expected
    assert "reason" not in compose(tmp_path, event("E04"), FakeBrowser(PageState.FORM))
    assert "reason" not in compose(tmp_path, event("E04"), FakeBrowser(RuntimeError("page gone")))
    assert compose(tmp_path, event("E05"), FakeBrowser(PageState.LOGIN))["reason"] == "human_check"
    assert compose(tmp_path, event("E04", reason="legal"), FakeBrowser(PageState.LOGIN))["reason"] == "legal"


def test_legal_consent_handoff_reaches_the_user_without_the_word_login(tmp_path):
    payload = compose(tmp_path, event("E04"), FakeBrowser(PageState.FORM, [legal_field()]))
    evt = event("E04", **payload)
    text = render(evt, "https://cp.example.test/s/abcdefghijklm")
    assert "Action needed in the browser" in text and "legal confirmation" in text
    assert "log in" not in text.lower() and "Login needed" not in text


# --------------------------------------------------------- item 5: run start
def test_run_started_event_carries_goal_source_and_update_times(tmp_path):
    (tmp_path / "profile.md").write_text("p", encoding="utf-8")
    (tmp_path / "rules.md").write_text("r", encoding="utf-8")
    data = RealData(object(), tmp_path, DomainAllowlist.from_urls(["http://127.0.0.1:8780"]))
    data.source_used, data.source_dir = "local_folder", tmp_path
    channel = bare_channel(tmp_path, goal="Apply to the 3 best-fit roles", run_context=data.run_context)
    evt = event("E01", job=None)
    payload = asyncio.run(channel._compose(evt))
    context = payload["context"]
    assert context["goal"] == "Apply to the 3 best-fit roles" and payload["goal"] == context["goal"]
    assert context["data_source"] == "local sample folder"
    assert context["profile_updated"] and context["rules_updated"]
    composed = evt.model_copy(update={"payload": payload})
    text = render(composed, None)
    assert "Goal: Apply to the 3 best-fit roles" in text and "not stated" not in text
    assert "Data from: local sample folder" in text and "Last updated: profile" in text


def test_drive_source_names_the_folder_and_invents_no_times(tmp_path):
    data = RealData(object(), tmp_path, DomainAllowlist.from_urls(["http://127.0.0.1:8780"]))
    data.source_used, data.source_dir = "drive_public", tmp_path
    assert data.run_context() == {"data_source": "Google Drive folder"}
    data.source_used = ""  # before the data is loaded: nothing to say
    assert data.run_context() == {}


def test_existing_context_wins_over_the_composition(tmp_path):
    channel = bare_channel(tmp_path, goal="from launcher", run_context=lambda: {"data_source": "x", "profile_updated": "y"})
    evt = event("E01", job=None, context={"goal": "from graph", "profile_updated": "kept"})
    context = asyncio.run(channel._compose(evt))["context"]
    assert (context["goal"], context["profile_updated"], context["data_source"]) == ("from graph", "kept", "x")
