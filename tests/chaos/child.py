"""Test-only entry point: instrument node functions, never production files."""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from langgraph.graph import StateGraph

from scripts import demo_g3
from src.operator.contracts import ActionResult, FillAction
from src.operator.graph.runtime import AnswerPlan


def main() -> None:
    """Journal boundaries and stop until the parent kills this process."""
    directory = Path(sys.argv[1])
    target = os.environ.get("CHAOS_NODE", "")
    phase = os.environ.get("CHAOS_PHASE", "before")
    original = StateGraph.add_node

    def boundary(name, edge):
        with (directory / "boundaries.jsonl").open("a") as stream:
            stream.write(json.dumps([name, edge]) + "\n")
        if (
            name == target
            and edge == phase
            and not (directory / "fault-fired").exists()
        ):
            # Publish only a complete marker: the parent kills immediately on
            # existence, so create-then-write can lose its contents to SIGKILL.
            marker = directory / "fault-fired.tmp"
            marker.write_text(name + ":" + edge)
            marker.replace(directory / "fault-fired")
            while True:
                time.sleep(0.05)

    def add_node(graph, name, action=None, **kwargs):
        if callable(action):

            def wrapped(state, *args, **kw):
                if name == "open_application":
                    boundary("application", "before")
                    (directory / "application-entered").write_text("entered")
                elif (
                    name == "next_job" and (directory / "application-entered").exists()
                ):
                    boundary("application", "after")
                boundary(name, "before")
                result = action(state, *args, **kw)
                boundary(name, "after")
                return result

            return original(graph, name, wrapped, **kwargs)
        return original(graph, name, action, **kwargs)

    StateGraph.add_node = add_node
    # A failed initial input exercises the repair branch without a second successful input.
    execute = demo_g3.ObservedBrowser.execute

    async def observed(browser, action):
        marker = directory / "repair-initial-failure"
        if os.environ.get("CHAOS_VARIANT") == "repair" and not marker.exists():
            marker.write_text("failed before input")
            return ActionResult(
                field_key=action.field_key, success=False, reason="test fault"
            )
        with (directory / "inputs.jsonl").open("a") as stream:
            stream.write(json.dumps(action.field_key) + "\n")
        return await execute(browser, action)

    demo_g3.ObservedBrowser.execute = observed
    if os.environ.get("CHAOS_VARIANT") == "answer":
        structured = demo_g3.FixturePlanner.structured

        async def planner(planner, prompt, model):
            result = await structured(planner, prompt, model)
            if model is AnswerPlan:
                for index, action in enumerate(result.actions):
                    if action.field_key.startswith("Phone|tel|"):
                        result.actions[index] = FillAction(
                            field_key=action.field_key,
                            action="ask_user",
                            question="Synthetic fixture: explicitly provide a phone number?",
                        )
                        break
            return result

        demo_g3.FixturePlanner.structured = planner
    if os.environ.get("CHAOS_HTTP_RETRIES"):
        channel_init = demo_g3.RecordingChannel.__init__

        def channel(channel, *args, **kwargs):
            channel_init(channel, *args, **kwargs)
            channel.sink._attempts = (
                3  # production HttpSink default, not demo's one attempt
            )

        demo_g3.RecordingChannel.__init__ = channel
    for line in sys.stdin:
        request = json.loads(line)
        demo_g3.child_worker(directory, request["mode"], request.get("crash"))
        (directory / "worker-done").write_text(str(request["sequence"]))


if __name__ == "__main__":
    main()
