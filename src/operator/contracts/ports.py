"""Adapter protocols. Safety decisions belong to deterministic core code."""

from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from .primitives import ActionResult, FieldSpec, FillAction, FillReport, PageState, SubmissionResult

from .state import DataSnapshot, Event, ReviewSnapshot

ModelT = TypeVar("ModelT", bound=BaseModel)


@runtime_checkable
class LedgerPort(Protocol):
    """Synchronous atomic persistence; store hashes, never raw capabilities."""

    def claim_action(self, run_id: str, job_id: str, action: str, field_key: str) -> bool:
        """Claim once; an existing claim or success returns false."""
        ...

    def mark_success(self, run_id: str, job_id: str, action: str, field_key: str) -> None:
        """Complete an already-claimed action."""
        ...

    def is_done(self, run_id: str, job_id: str, action: str, field_key: str) -> bool:
        """Return whether the action has succeeded."""
        ...

    def record_approval(self, run_id: str, job_id: str, token_hash: str,
                        snapshot_hash: str, expires_at: datetime) -> None:
        """Record the immutable verified capability bindings."""
        ...

    def consume_approval(self, run_id: str, job_id: str, token_hash: str,
                         snapshot_hash: str) -> bool:
        """Consume once and set the persisted job to APPROVED atomically."""
        ...


@runtime_checkable
class LLMPort(Protocol):
    """Structured model proposals, with no authority to change safety state."""

    async def structured(self, prompt: str, response_model: type[ModelT]) -> ModelT:
        """Return validated structured output or raise an adapter error."""
        ...


@runtime_checkable
class DataSourcePort(Protocol):
    """Load and hash mutable candidate sources once per run."""

    async def load(self, run_id: str) -> "DataSnapshot":
        """Return validated data with resume location and file hashes."""
        ...


@runtime_checkable
class BrowserPort(Protocol):
    """Operate one browser target; caller enforces allowlist and approval."""

    async def attach(self, endpoint: str, target_id: str | None = None) -> None:
        """Reattach without launching another browser."""
        ...

    async def navigate(self, url: str) -> None:
        """Navigate with redirect checks supplied by the adapter."""
        ...

    async def classify_page(self) -> PageState:
        """Classify deterministic signals first; never interact with challenges."""
        ...

    async def extract_fields(self) -> list[FieldSpec]:
        """Extract stable field keys and read-back values."""
        ...

    async def execute(self, action: FillAction) -> ActionResult:
        """Execute one policy-approved reversible input."""
        ...

    async def verify(self, actions: list[FillAction]) -> FillReport:
        """Read back the intended fields and compare."""
        ...

    async def click_next(self) -> bool:
        """Click only safe next-step vocabulary; false means final step."""
        ...

    async def capture_evidence(self) -> list[str]:
        """Return local evidence paths."""
        ...

    async def read_review(self) -> "ReviewSnapshot":
        """Read current values and uploads for a canonical review hash."""
        ...

    async def submit(self) -> None:
        """Single click, only on fixtures after caller's durable submit claim."""
        ...

    async def verify_submission(self) -> SubmissionResult:
        """Observe confirmation without another click."""
        ...


@runtime_checkable
class ChannelPort(Protocol):
    """Delivery adapter; gate remains paused if delivery fails."""

    async def emit(self, event: "Event") -> None:
        """Deliver a typed event or raise so the caller can pause."""
        ...
