"""Unit and contract tests for src/operator/llm."""

import pytest
from pydantic import BaseModel, Field
from unittest.mock import MagicMock, patch

from src.operator.llm.base import (
    FORBIDDEN_DEFAULT_MODEL,
    SAFE_DEFAULT_MODEL,
    BaseLLMAdapter,
    LLMError,
    clean_json_text,
)
from src.operator.llm.cost import UsageTracker, calculate_cost
from src.operator.llm.factory import get_llm_port
from src.operator.llm.judge import SemanticJudge
from src.operator.llm.protocol import LLMPort, LLMResponse, UsageMetadata


class MockUserResponse(BaseModel):
    name: str
    age: int
    skills: list[str] = Field(default_factory=list)


class FakeLLMAdapter(BaseLLMAdapter):
    """Fake adapter for testing protocol and base behavior without network."""

    def __init__(
        self,
        mock_text: str = '{"name": "Aarav", "age": 28, "skills": ["Python"]}',
        mock_usage: dict | None = None,
        should_fail_times: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.mock_text = mock_text
        self.mock_usage = mock_usage or {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30}
        self.failures_remaining = should_fail_times
        self.calls_made: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    def _call_api(
        self,
        model: str,
        prompt: str,
        system_instruction: str | None,
        temperature: float,
        response_mime_type: str | None,
        timeout: float,
    ):
        self.calls_made.append({"model": model, "prompt": prompt})
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("Simulated API failure")
        return self.mock_text, self.mock_usage, {"mock": True}


def test_protocol_conformance():
    """Verify FakeLLMAdapter satisfies LLMPort protocol."""
    adapter = FakeLLMAdapter()
    assert isinstance(adapter, LLMPort)


def test_forbidden_default_model_guard():
    """Verify gemini-3-flash-preview is barred as default model."""
    adapter = FakeLLMAdapter(default_model=FORBIDDEN_DEFAULT_MODEL)
    assert adapter.default_model == SAFE_DEFAULT_MODEL


def test_clean_json_text():
    """Verify markdown fences are stripped from LLM JSON output."""
    raw = "```json\n{\n  \"key\": \"value\"\n}\n```"
    assert clean_json_text(raw) == '{\n  "key": "value"\n}'

    raw2 = "```\n{\"key\": 123}\n```"
    assert clean_json_text(raw2) == '{"key": 123}'

    raw3 = '  {"plain": true}  '
    assert clean_json_text(raw3) == '{"plain": true}'


def test_cost_calculation():
    """Test cost calculation in USD and INR for known models."""
    usd, inr = calculate_cost("gemini-2.5-flash", prompt_tokens=1_000_000, candidates_tokens=1_000_000)
    assert usd == pytest.approx(0.375, rel=1e-3)
    assert inr == pytest.approx(0.375 * 84.0, rel=1e-2)

    usd_pro, _ = calculate_cost("gemini-2.5-pro", prompt_tokens=1_000_000, candidates_tokens=1_000_000)
    assert usd_pro == pytest.approx(6.25, rel=1e-3)


def test_usage_tracker():
    """Test cumulative usage summary tracking."""
    tracker = UsageTracker()
    meta1 = UsageMetadata(
        prompt_tokens=100, candidates_tokens=50, total_tokens=150,
        model="gemini-2.5-flash", cost_usd=0.01, cost_inr=0.84, duration_sec=1.2
    )
    tracker.record(meta1)
    tracker.record(meta1)

    summary = tracker.get_summary()
    assert summary.total_calls == 2
    assert summary.total_tokens == 300
    assert summary.total_cost_usd == pytest.approx(0.02)
    assert summary.total_cost_inr == pytest.approx(1.68)
    assert summary.calls_by_model["gemini-2.5-flash"] == 2


def test_generate_text_fake():
    """Test text generation with FakeLLMAdapter."""
    adapter = FakeLLMAdapter(mock_text="Hello world")
    resp = adapter.generate_text("Hi")
    assert resp.content == "Hello world"
    assert resp.usage.total_tokens == 30
    assert adapter.get_usage().total_calls == 1


def test_generate_structured_fake():
    """Test structured JSON generation validated by Pydantic."""
    adapter = FakeLLMAdapter(mock_text='{"name": "Aarav Mehta", "age": 28, "skills": ["React", "Python"]}')
    result, resp = adapter.generate_structured("Extract profile", schema=MockUserResponse)
    assert result.name == "Aarav Mehta"
    assert result.age == 28
    assert "React" in result.skills
    assert resp.usage.total_tokens == 30


@pytest.mark.anyio
async def test_async_structured_conformance():
    """Test async structured method matching Codex contracts-v0.1a."""
    adapter = FakeLLMAdapter(mock_text='{"name": "Priya", "age": 25, "skills": ["Go"]}')
    result = await adapter.structured("Extract candidate", response_model=MockUserResponse)
    assert result.name == "Priya"
    assert result.age == 25
    assert result.skills == ["Go"]


def test_fallback_model_on_failure():

    """Test fallback model is attempted when primary model fails."""
    adapter = FakeLLMAdapter(
        default_model="gemini-2.5-flash",
        fallback_model="gemini-2.5-pro",
        max_retries=1,
        should_fail_times=2,  # Will exhaust retries on primary, succeed on fallback
    )
    resp = adapter.generate_text("Test query")
    assert len(adapter.calls_made) == 3
    assert adapter.calls_made[0]["model"] == "gemini-2.5-flash"
    assert adapter.calls_made[1]["model"] == "gemini-2.5-flash"
    assert adapter.calls_made[2]["model"] == "gemini-2.5-pro"


def test_semantic_judge_deterministic():
    """Test SemanticJudge fast deterministic paths without invoking LLM."""
    adapter = FakeLLMAdapter()
    judge = SemanticJudge(adapter)

    # Exact match
    r1 = judge.is_semantic_match("Ahmedabad", "Ahmedabad")
    assert r1.match is True
    assert r1.confidence == 1.0
    assert len(adapter.calls_made) == 0

    # Substring match
    r2 = judge.is_semantic_match("Ahmedabad", "Ahmedabad, Gujarat")
    assert r2.match is True
    assert r2.confidence == 0.95
    assert len(adapter.calls_made) == 0

    # Deterministic submission confirmation
    s1 = judge.verify_submission("Thank you for applying! Your application has been received.", "https://ats.example.com")
    assert s1.confirmed is True
    assert s1.confidence == 0.99
    assert len(adapter.calls_made) == 0
