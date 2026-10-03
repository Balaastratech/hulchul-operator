"""Live integration smoke test for Vertex AI provider with gemini-2.5-flash."""

import pytest
from pydantic import BaseModel
from src.operator.llm.factory import get_llm_port


class LivePingResponse(BaseModel):
    message: str
    status: str


@pytest.mark.live
def test_live_vertex_generate_structured():
    """Verify live Vertex AI generate_structured using gemini-2.5-flash and ADC."""
    llm = get_llm_port(provider="vertex", model="gemini-2.5-flash")
    result, resp = llm.generate_structured(
        prompt="Respond with message 'hello from operator' and status 'active'",
        schema=LivePingResponse,
    )
    assert result.message.lower().strip() == "hello from operator"
    assert result.status.lower().strip() == "active"
    assert resp.usage.total_tokens > 0
    assert resp.usage.cost_usd > 0
    assert resp.usage.cost_inr > 0

    summary = llm.get_usage()
    assert summary.total_calls >= 1
    assert summary.total_tokens > 0
