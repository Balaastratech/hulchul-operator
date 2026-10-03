"""Subprocess probe of core + public peer browser components on a local fixture."""

import argparse
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.types import Command as Resume

from src.operator.contracts import FillAction, Goal
from src.operator.graph import Services, sqlite_graph
from src.operator.graph.adapters import BrowserBridge
from src.operator.graph.runtime import AnswerPlan, ShortlistPlan
from src.operator.graph.tests.fakes import FakeChannel, FakeData
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist
from worker.main import Worker, _current_run


class NoNetworkTransport:
    def poll(self, run_id):
        return []

    def acknowledge(self, command_id):
        pass

    def heartbeat(self, run_id, status):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--mode",
        choices=[
            "start",
            "crash_fill",
            "approve",
            "resume",
            "crash_before",
            "crash_approval",
            "crash_after",
        ],
        required=True,
    )
    args = parser.parse_args()
    importlib.import_module("src.operator").__path__.append(
        str(args.source_root / "src/operator")
    )
    from src.operator.browser.cdp import CDPBrowserManager

    allowed = DomainAllowlist.from_urls([args.url], fixture_urls=[args.url])
    browser = BrowserBridge(CDPBrowserManager(), allowed, args.state_dir / "evidence")

    class LLM:
        def __init__(self):
            self.calls = 0

        async def structured(self, prompt, response_model):
            self.calls += 1
            if response_model is Goal:
                return Goal(mode="normal")
            if response_model is ShortlistPlan:
                return ShortlistPlan(
                    jobs=[
                        {"job_id": "fixture", "score": 1, "reason": "Synthetic fixture"}
                    ]
                )
            payload = json.loads(
                prompt.split("<untrusted_data>")[1].split("</untrusted_data>")[0]
            )
            return AnswerPlan(
                actions=[
                    FillAction(
                        field_key=payload["fields"][0]["key"],
                        action="fill",
                        value="Synthetic",
                        source="profile.name",
                    )
                ]
            )

    class Data(FakeData):
        async def load(self, run_id):
            data = await super().load(run_id)
            data.jobs[0].url = args.url
            return data

    services = Services(
        browser=browser,
        llm=LLM(),
        data=Data(),
        channel=FakeChannel(),
        ledger=SQLiteLedger(args.state_dir / "ledger.sqlite"),
        allowlist=allowed,
        submission_urls=browser.submission_urls,
        restore_browser=browser.restore,
        target_id=browser.target_id,
    )
    original_execute = browser.execute
    original_submit = browser.submit
    if args.mode == "crash_approval":

        def crash_approval(state, services):
            os._exit(74)

        services.submit_handler = crash_approval
    if args.mode == "crash_fill":

        async def crash_fill(action):
            await original_execute(action)
            os._exit(71)

        browser.execute = crash_fill
    if args.mode in {"crash_before", "crash_after"}:

        async def crash_submit():
            if args.mode == "crash_after":
                await original_submit()
            os._exit(72 if args.mode == "crash_before" else 73)

        browser.submit = crash_submit
    with sqlite_graph(services, args.state_dir / "checkpoints.sqlite") as graph:
        worker = Worker(
            graph,
            services,
            NoNetworkTransport(),
            "r",
            args.state_dir / "commands.sqlite",
        )
        result = worker.start_or_resume("Fill one local fixture", args.endpoint)
        if args.mode in {"approve", "crash_before", "crash_after", "crash_approval"}:
            run = _current_run(graph.get_state(worker.config, subgraphs=True))
            digest = run.jobs["fixture"].review_snapshot_hash
            services.ledger.record_approval(
                "r",
                "fixture",
                "a" * 64,
                digest,
                datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            result = graph.invoke(
                Resume(
                    resume={
                        "command_id": "approval",
                        "run_id": "r",
                        "job_id": "fixture",
                        "action": "approve",
                        "token_hash": "a" * 64,
                        "snapshot_hash": digest,
                    }
                ),
                worker.config,
            )
        snapshot = graph.get_state(worker.config, subgraphs=True)
        run = _current_run(snapshot)
        count = services.call(
            browser._call(
                lambda: browser.page.evaluate(
                    "Number(sessionStorage.getItem('fills') || 0)"
                )
            )
        )
        print(
            json.dumps(
                {
                    "status": run.jobs["fixture"].status.value,
                    "fills": count,
                    "llm_calls_this_process": services.llm.calls,
                    "interrupt": result.get("__interrupt__", [None])[0].value["kind"]
                    if result.get("__interrupt__")
                    else None,
                }
            )
        )
    services.call(browser.disconnect())
    services.close()


if __name__ == "__main__":
    main()
