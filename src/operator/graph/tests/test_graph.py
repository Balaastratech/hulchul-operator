"""Native subgraph gates and durable state restoration with fake Ports."""

from langgraph.types import Command as Resume

from src.operator.contracts import RunState
from src.operator.graph import sqlite_graph
from src.operator.graph.tests.fakes import services_at


def start(graph):
    return graph.invoke(
        {"run": RunState(run_id="r", goal="Fill one fixture").model_dump(mode="json")},
        {"configurable": {"thread_id": "r"}, "recursion_limit": 150},
    )


def test_review_gate_survives_connection_restart_without_refills(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
        assert services.browser.submissions == 0
    services.llm.calls = 0
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = graph.invoke(
            Resume(
                resume={
                    "command_id": "reject-1",
                    "run_id": "r",
                    "job_id": "fixture",
                    "action": "reject",
                }
            ),
            config,
        )
        assert result["run"]["status"] == "COMPLETED"
        assert result["run"]["jobs"]["fixture"]["status"] == "REJECTED_BY_USER"
        assert services.browser.executions == ["name"]
        assert services.llm.calls == 0
    services.close()


def test_pause_gate_never_touches_browser_until_resume(tmp_path):
    services = services_at(tmp_path)
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        start(graph)
        result = graph.invoke(
            Resume(resume={"command_id": "pause", "run_id": "r", "action": "pause"}),
            config,
        )
        assert result["__interrupt__"][0].value["kind"] == "paused"
        assert services.browser.executions == ["name"]
        result = graph.invoke(
            Resume(resume={"command_id": "resume", "run_id": "r", "action": "resume"}),
            config,
        )
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
    services.close()


def test_captcha_handoff_has_no_executor_attempts(tmp_path):
    services = services_at(tmp_path)
    from src.operator.contracts import PageState

    services.browser.page_state = PageState.CAPTCHA
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "handoff"
        assert services.browser.executions == []
        services.browser.page_state = PageState.FORM
        result = graph.invoke(
            Resume(
                resume={"command_id": "done", "run_id": "r", "action": "handoff_done"}
            ),
            {"configurable": {"thread_id": "r"}, "recursion_limit": 150},
        )
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert services.browser.executions == ["name"]
    services.close()


def test_legal_handoff_on_early_step_resumes_navigation_before_review(tmp_path):
    from src.operator.contracts import FieldSpec, FillAction, Goal
    from src.operator.graph.runtime import AnswerPlan, ShortlistPlan

    services = services_at(tmp_path)
    browser = services.browser
    browser.step = 0
    browser.values["consent"] = False
    original_extract = browser.extract_fields

    async def extract():
        if browser.step == 0:
            return [
                FieldSpec(
                    id="consent",
                    key="consent",
                    label="I agree to terms",
                    type="checkbox",
                    required=True,
                    current_value=browser.values["consent"],
                )
            ]
        return await original_extract()

    async def next_step():
        if browser.step == 0:
            assert browser.values["consent"] is True
            browser.step = 1
            return True
        return False

    original_plan = services.llm.structured

    async def plan(prompt, response_model):
        if response_model not in {Goal, ShortlistPlan} and browser.step == 0:
            return AnswerPlan(
                actions=[
                    FillAction(
                        field_key="consent",
                        action="ask_user",
                        question="Accept terms manually",
                    )
                ]
            )
        return await original_plan(prompt, response_model)

    browser.extract_fields = extract
    browser.click_next = next_step
    services.llm.structured = plan
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "handoff"
        assert browser.executions == []
        browser.values["consent"] = True  # Simulated human browser action.
        result = graph.invoke(
            Resume(
                resume={"command_id": "done", "run_id": "r", "action": "handoff_done"}
            ),
            config,
        )
        assert result["__interrupt__"][0].value["kind"] == "review"
        assert browser.step == 1
        assert browser.executions == ["name"]
        assert browser.submissions == 0
    services.close()


def test_handoff_after_human_advances_extracts_current_step_and_keeps_unknown(tmp_path):
    """Done cannot verify stale actions or turn unrelated unanswered facts into skips."""
    from src.operator.contracts import FieldSpec, FillAction
    from src.operator.graph.runtime import AnswerPlan

    services = services_at(tmp_path)
    browser = services.browser
    browser.step = 0
    browser.values["consent"] = False
    extracted = []
    original_plan = services.llm.structured

    async def extract():
        extracted.append(browser.step)
        if browser.step == 0:
            return [
                FieldSpec(
                    id="consent",
                    key="consent",
                    label="I agree to terms",
                    type="checkbox",
                    required=True,
                    current_value=browser.values["consent"],
                )
            ]
        return [
            FieldSpec(
                id="name", key="name", label="Full name", type="text", required=True
            ),
            FieldSpec(
                id="unknown",
                key="unknown",
                label="Unknown required fact",
                type="text",
                required=True,
            ),
        ]

    async def plan(prompt, response_model):
        result = await original_plan(prompt, response_model)
        if response_model is AnswerPlan:
            result.actions.append(
                FillAction(
                    field_key="unknown",
                    action="ask_user",
                    question="Unknown required fact",
                )
            )
        return result

    browser.extract_fields = extract
    services.llm.structured = plan
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "handoff"
        browser.values["consent"] = True  # Human accepts and advances the fixture.
        browser.step = 1
        result = graph.invoke(
            Resume(
                resume={"command_id": "done", "run_id": "r", "action": "handoff_done"}
            ),
            config,
        )
        gate = result["__interrupt__"][0].value
        assert gate["kind"] == "answer"
        assert gate["field_key"] == "unknown"
        assert extracted == [0, 1]
        assert browser.executions == ["name"]
        assert browser.submissions == 0
    services.close()


def test_duplicate_ranked_postings_never_open_or_fill_second_application(tmp_path):
    from src.operator.contracts import Goal
    from src.operator.graph.runtime import ShortlistPlan
    from worker.main import _current_run

    services = services_at(tmp_path)
    original_load = services.data.load
    original_plan = services.llm.structured

    async def load(run_id):
        data = await original_load(run_id)
        data.rules.max_applications_per_run = 2
        duplicate = data.jobs[0].model_copy(deep=True)
        duplicate.job_id = "duplicate"
        duplicate.company = "  SYNTHETIC   COMPANY  "
        duplicate.url += "#another-listing"
        data.jobs.append(duplicate)
        return data

    async def plan(prompt, response_model):
        if response_model is Goal:
            return Goal(mode="normal", max_apply=2)
        if response_model is ShortlistPlan:
            return ShortlistPlan(
                jobs=[
                    {"job_id": "fixture", "score": 1, "reason": "Synthetic fit"},
                    {"job_id": "duplicate", "score": 0.9, "reason": "Same role again"},
                ]
            )
        return await original_plan(prompt, response_model)

    services.data.load = load
    services.llm.structured = plan
    config = {"configurable": {"thread_id": "r"}, "recursion_limit": 150}
    with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
        result = start(graph)
        assert result["__interrupt__"][0].value["kind"] == "review"
        run = _current_run(graph.get_state(config, subgraphs=True))
        assert run.jobs["duplicate"].status.value == "SKIPPED_DUPLICATE"
        assert services.browser.navigations == 1
        assert services.browser.executions == ["name"]
        assert services.browser.submissions == 0
        graph.invoke(None, config)
        assert services.browser.navigations == 1
        assert services.browser.executions == ["name"]
    services.close()
