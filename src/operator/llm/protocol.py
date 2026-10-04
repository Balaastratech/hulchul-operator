"""Protocol and data structures for LLM provider adapters."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class UsageMetadata(BaseModel):
    """Metadata for a single LLM call."""
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    cost_inr: float = 0.0
    duration_sec: float = 0.0


class UsageSummary(BaseModel):
    """Cumulative usage across all LLM calls."""
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_candidates_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_cost_inr: float = 0.0
    calls_by_model: dict[str, int] = Field(default_factory=dict)
    tokens_by_model: dict[str, int] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Raw response from an LLM call."""
    content: str
    usage: UsageMetadata
    raw_response: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class LLMPort(Protocol):
    """Port interface for LLM operations.

    Compatible with canonical contracts-v0.1a LLMPort specification.
    """

    async def structured(
        self,
        prompt: str,
        response_model: type[T],
    ) -> T:
        """canonical contracts-v0.1a compatible async structured generation."""
        ...

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Generate unstructured text from a prompt."""
        ...

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> tuple[T, LLMResponse]:
        """Generate structured data adhering to a Pydantic schema."""
        ...

    def get_usage(self) -> UsageSummary:
        """Return cumulative usage summary."""
        ...

    def reset_usage(self) -> None:
        """Reset cumulative usage counters."""
        ...
