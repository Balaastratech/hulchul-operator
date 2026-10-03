"""Non-checkpointed services and pure serializable graph helpers."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TypedDict, TypeVar

from pydantic import Field

from src.operator.contracts import (
    BrowserPort, ChannelPort, Command, Contract, DataSourcePort, Event, FillAction,
    JobState, LLMPort, RunState,
)
from src.operator.ledger import SQLiteLedger
from src.operator.policy.allowlist import DomainAllowlist

T = TypeVar("T")


class GraphState(TypedDict, total=False):
    """Only JSON-compatible data is checkpointed."""

    run: dict[str, Any]
    data: dict[str, Any]
    route: str
    queue: list[str]
    command: dict[str, Any]
    step: int


class AnswerPlan(Contract):
    """Internal structured proposal; policy inspects every action."""

    actions: list[FillAction] = Field(default_factory=list)


class RankedJob(Contract):
    """A proposal to rank an already eligible posting, never to add a URL."""

    job_id: str
    score: float = Field(ge=0, le=1)
    reason: str


class ShortlistPlan(Contract):
    """Internal fit proposal validated against eligible candidate IDs."""

    jobs: list[RankedJob] = Field(default_factory=list)


@dataclass
class Services:
    """Ports remain outside state; a persistent loop preserves browser bindings."""

    browser: BrowserPort
    llm: LLMPort
    data: DataSourcePort
    channel: ChannelPort
    ledger: SQLiteLedger
    allowlist: DomainAllowlist
    injection_scan: Callable[[str], bool] | None = None
    submit_handler: Callable[[GraphState, "Services"], dict[str, Any]] | None = None
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.new_event_loop)

    def call(self, awaitable: Awaitable[T]) -> T:
        """Run Ports on one worker-owned event loop; synchronous graph API only."""
        return self.loop.run_until_complete(awaitable)

    def emit(self, event_id: str, run: RunState, message: str, *, payload: dict | None = None) -> None:
        """Persist a redacted milestone and deliver; errors keep the gate closed."""
        self.ledger.append_event(run.run_id, run.active_job_id, event_id, message)
        self.call(self.channel.emit(Event(event_id=event_id, run_id=run.run_id,
                                         job_id=run.active_job_id, message=message,
                                         payload=payload or {}, created_at=datetime.now(timezone.utc))))

    def close(self) -> None:
        """Release the local event loop after adapters have detached."""
        self.loop.close()


def read_run(state: GraphState) -> RunState:
    """Validate state on entry to each deterministic node."""
    return RunState.model_validate(state["run"])


def active_job(run: RunState) -> JobState:
    """Resolve exactly one active job; absent identities fail closed."""
    if run.active_job_id is None:
        raise ValueError("no active job")
    return run.jobs[run.active_job_id]


def update(run: RunState, **extra: Any) -> dict[str, Any]:
    """Serialize Pydantic state before it reaches the checkpointer."""
    return {"run": run.model_dump(mode="json"), **extra}


def validate_command(value: Any, run: RunState) -> Command:
    """Reject a resume bound to another run or active job."""
    command = Command.model_validate(value)
    if command.run_id != run.run_id or command.job_id not in {None, run.active_job_id}:
        raise PermissionError("command binding mismatch")
    return command
