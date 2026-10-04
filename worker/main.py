"""Local worker: resume checkpoints, poll outbound commands and heartbeat."""

import argparse
import importlib
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command as Resume

from src.operator.contracts import Command, RunState
from src.operator.graph import Services, sqlite_graph
from src.operator.graph.runtime import ControlUnavailable

from .transport import HttpTransport, WorkerTransport


def _processed_in_snapshot(snapshot, command_id: str) -> bool:
    command = snapshot.values.get("command", {})
    if command.get("command_id") == command_id:
        return True
    return any(
        task.state is not None
        and hasattr(task.state, "values")
        and _processed_in_snapshot(task.state, command_id)
        for task in snapshot.tasks
    )


def _current_run(snapshot) -> RunState:
    """Prefer the deepest application checkpoint over the parent's initial state."""
    for task in snapshot.tasks:
        if (
            task.state is not None
            and hasattr(task.state, "values")
            and task.state.values.get("run")
        ):
            return _current_run(task.state)
    return RunState.model_validate(snapshot.values["run"])


def _pending_interrupts(snapshot) -> tuple:
    """Find pending human gates even when they live in a nested application."""
    for task in snapshot.tasks:
        if task.state is not None and hasattr(task.state, "values"):
            # Parent task interrupts can still describe the gate already released
            # inside a running child (e.g. crash in pre_submit_check). The child's
            # latest checkpoint is authoritative, including an empty interrupt set.
            return _pending_interrupts(task.state)
    return snapshot.interrupts


class Worker:
    """One run per local worker; command replay is persisted before network ack."""

    def __init__(
        self,
        graph,
        services: Services,
        transport: WorkerTransport,
        run_id: str,
        command_db: str | Path,
        *,
        poll_seconds: float = 2,
        graph_node_budget: int = 5000,
    ) -> None:
        """Retain a compiled graph backed by an open SqliteSaver connection."""
        if poll_seconds < 0.1:
            raise ValueError("poll interval must be at least 0.1 seconds")
        if (
            isinstance(graph_node_budget, bool)
            or not isinstance(graph_node_budget, int)
            or graph_node_budget < 1
        ):
            raise ValueError("graph node budget must be a positive integer")
        self.graph, self.services, self.transport, self.run_id = (
            graph,
            services,
            transport,
            run_id,
        )
        self.config = {
            "configurable": {"thread_id": run_id},
            "recursion_limit": graph_node_budget,
        }
        self.command_db = Path(command_db)
        self.command_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.command_db) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY, "
                "run_id TEXT NOT NULL, status TEXT NOT NULL)"
            )
        self.poll_seconds = poll_seconds
        self.running = True
        self.services.control_command = self._control_command

    def _control_command(self) -> Command | None:
        """Read pause/cancel commands between atomic fill actions."""
        for attempt in range(3):
            try:
                commands = self.transport.poll(self.run_id)
                break
            except OSError as error:
                if attempt == 2:
                    raise ControlUnavailable("control poll unavailable") from error
                time.sleep(self.poll_seconds)
        for command in commands:
            if command.action not in {"pause", "cancel"}:
                continue
            with sqlite3.connect(self.command_db) as connection:
                row = connection.execute(
                    "SELECT status FROM commands WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()
                if row and row[0] == "DONE":
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO commands VALUES (?, ?, 'PENDING')",
                    (command.command_id, self.run_id),
                )
            return command
        return None

    def start_or_resume(
        self, goal: str | None = None, cdp_endpoint: str | None = None
    ) -> dict:
        """Existing state always wins over new CLI input; reattach before resuming."""
        snapshot = self.graph.get_state(self.config, subgraphs=True)
        if snapshot.values:
            if not snapshot.next:
                # Completed runs need no browser. A retained review tab must not
                # make terminal replay ambiguous after active_job_id is cleared.
                return snapshot.values
            run = _current_run(snapshot)
            if run.cdp_endpoint:
                target = run.jobs.get(run.active_job_id) if run.active_job_id else None
                self.services.call(
                    self.services.browser.attach(
                        run.cdp_endpoint, target.browser_target_id if target else None
                    )
                )
                if target and self.services.restore_browser:
                    self.services.call(self.services.restore_browser(target))
            pending = _pending_interrupts(snapshot)
            if pending:
                # invoke(None) may replay the last resume value into the next gate.
                # Only a fresh CP command may release a persisted human interrupt.
                return {"run": run.model_dump(mode="json"), "__interrupt__": pending}
            return self.graph.invoke(None, self.config)
        if goal is None:
            raise ValueError("a new run requires a goal")
        run = RunState(run_id=self.run_id, goal=goal, cdp_endpoint=cdp_endpoint)
        return self.graph.invoke({"run": run.model_dump(mode="json")}, self.config)

    def handle(self, command: Command) -> dict | None:
        """Checkpoint graph work before acknowledging; reject cross-run commands."""
        if command.run_id != self.run_id:
            raise PermissionError("command run mismatch")
        with sqlite3.connect(self.command_db) as connection:
            row = connection.execute(
                "SELECT run_id,status FROM commands WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if row and row[0] != self.run_id:
                raise PermissionError("command ID reused across runs")
            already_done = bool(row and row[1] == "DONE")
            connection.execute(
                "INSERT OR IGNORE INTO commands VALUES (?, ?, 'PENDING')",
                (command.command_id, self.run_id),
            )
        result = None
        if not already_done:
            if command.action == "approve" and hasattr(
                self.transport, "approval_expiry"
            ):
                expiry = self.transport.approval_expiry(command)
                if expiry is None:
                    raise PermissionError(
                        "remote approval requires verified expiry metadata"
                    )
                if not self.services.ledger.approval_registered(
                    command.run_id,
                    command.job_id,
                    command.token_hash,
                    command.snapshot_hash,
                    expiry,
                ):
                    self.services.ledger.record_approval(
                        command.run_id,
                        command.job_id,
                        command.token_hash,
                        command.snapshot_hash,
                        expiry,
                    )
            snapshot = self.graph.get_state(self.config, subgraphs=True)
            if not _processed_in_snapshot(snapshot, command.command_id):
                pending = _pending_interrupts(snapshot)
                if len(pending) != 1:
                    raise PermissionError("command requires exactly one pending gate")
                result = self.graph.invoke(
                    Resume(resume={pending[0].id: command.model_dump(mode="json")}),
                    self.config,
                )
            with sqlite3.connect(self.command_db) as connection:
                connection.execute(
                    "UPDATE commands SET status='DONE' WHERE command_id=?",
                    (command.command_id,),
                )
        self.transport.acknowledge(command.command_id)
        return result

    def tick(self) -> None:
        """Emit liveness and process this run's commands serially."""
        snapshot = self.graph.get_state(self.config, subgraphs=True)
        if snapshot.next and not _pending_interrupts(snapshot):
            self.start_or_resume()
            snapshot = self.graph.get_state(self.config, subgraphs=True)
        status = snapshot.values.get("run", {}).get("status", "QUEUED")
        self.transport.heartbeat(self.run_id, status)
        for command in self.transport.poll(self.run_id):
            self.handle(command)

    def run_forever(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        """Bounded poll/backoff; transport failure never releases a human gate."""
        backoff = self.poll_seconds
        while self.running:
            try:
                self.tick()
                backoff = self.poll_seconds
            except (OSError, ValueError, PermissionError):
                backoff = min(30, backoff * 2)
            sleep(backoff)


def main() -> None:
    """Load the single env file and user-selected adapter factory; never echo secrets."""
    load_dotenv(os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--factory", required=True, help="trusted module:function returning Services"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--graph-node-budget", type=int, default=5000)
    parser.add_argument("--goal")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--state-dir", type=Path, default=Path("runs/worker"))
    parser.add_argument("--commands-url", required=True)
    parser.add_argument("--acknowledgement-url", required=True)
    parser.add_argument("--heartbeat-url", required=True)
    args = parser.parse_args()
    module, function = args.factory.split(":", 1)
    services = getattr(importlib.import_module(module), function)()
    transport = HttpTransport(
        args.commands_url,
        args.acknowledgement_url,
        args.heartbeat_url,
        authorization=os.environ.get("WORKER_AUTHORIZATION"),
    )
    try:
        with sqlite_graph(services, args.state_dir / "checkpoints.sqlite") as graph:
            worker = Worker(
                graph,
                services,
                transport,
                args.run_id,
                args.state_dir / "commands.sqlite",
                graph_node_budget=args.graph_node_budget,
            )
            try:
                worker.start_or_resume(args.goal, args.cdp_endpoint)
            except ControlUnavailable:
                # The checkpoint still owns the pending action boundary. The
                # normal bounded polling loop retries it before any mutation.
                pass
            worker.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        services.close()


if __name__ == "__main__":
    main()
