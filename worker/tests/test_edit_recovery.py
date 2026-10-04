"""A recovered edit must bind fresh approval to restored intent and live values."""

from datetime import UTC, datetime, timedelta

import pytest

from src.operator.contracts import Command, FieldResult, ReviewSnapshot
from src.operator.graph import sqlite_graph
from src.operator.graph.nodes import apply_edit as edit_module
from src.operator.graph.tests.fakes import FakeBrowser, services_at
from worker.main import Worker
from worker.tests.test_worker import Transport


@pytest.mark.parametrize("phase", ["before", "after"])
def test_edit_crash_restores_intent_without_reapplying_input(
    tmp_path, monkeypatch, phase
):
    class Crash(BaseException):
        pass

    class Browser(FakeBrowser):
        intents = None

        async def execute(self, action):
            self.intents = {action.field_key: action.value}
            return await super().execute(action)

        async def restore(self, job):
            self.intents = {action.field_key: action.value for action in job.actions}

        async def read_review(self):
            return ReviewSnapshot(
                fields=[
                    FieldResult(
                        field_key=key,
                        actual=value,
                        intended=(self.intents or {}).get(key),
                        matched=True,
                    )
                    for key, value in self.values.items()
                ]
            )

    original = edit_module.apply_edit
    fired = False

    def crash_once(state, services):
        nonlocal fired
        if not fired:
            fired = True
            if phase == "after":
                original(state, services)
            raise Crash()
        return original(state, services)

    monkeypatch.setattr(edit_module, "apply_edit", crash_once)
    browser = Browser()
    services = services_at(tmp_path, browser)
    services.restore_browser = browser.restore
    transport = Transport()
    try:
        with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph, services, transport, "r", tmp_path / "commands.sqlite"
            )
            first = worker.start_or_resume("Fill fixture", "http://127.0.0.1:9222")[
                "__interrupt__"
            ][0].value
            with pytest.raises(Crash):
                worker.handle(
                    Command(
                        command_id="edit",
                        run_id="r",
                        job_id="fixture",
                        action="edit",
                        snapshot_hash=first["snapshot_hash"],
                        field_key="name",
                        value="Edited Synthetic",
                    )
                )
        browser.intents = {}  # Browser DOM survives, worker adapter cache does not.
        with sqlite_graph(services, tmp_path / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph, services, transport, "r", tmp_path / "commands.sqlite"
            )
            fresh = worker.start_or_resume()["__interrupt__"][0].value
            assert fresh["kind"] == "review"
            assert fresh["snapshot_hash"] != first["snapshot_hash"]
            assert fresh["review"]["fields"][0]["intended"] == "Edited Synthetic"
            services.ledger.record_approval(
                "r",
                "fixture",
                "a" * 64,
                fresh["snapshot_hash"],
                datetime.now(UTC) + timedelta(minutes=5),
            )
            result = worker.handle(
                Command(
                    command_id="approve",
                    run_id="r",
                    job_id="fixture",
                    action="approve",
                    snapshot_hash=fresh["snapshot_hash"],
                    token_hash="a" * 64,
                )
            )
            assert result["run"]["jobs"]["fixture"]["status"] == "SUBMITTED_VERIFIED"
            assert browser.executions == ["name", "name"]
            assert browser.submissions == 1
    finally:
        services.close()
