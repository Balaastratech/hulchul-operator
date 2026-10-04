"""Token cost estimation and cumulative usage tracking."""

from __future__ import annotations

import os
import threading
from typing import Any
from src.operator.llm.protocol import UsageMetadata, UsageSummary

# Default pricing per million tokens in USD
# Rates: standard input / output (<=128k context)
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Gemini 2.5 Flash (published standard Developer API text rate)
    "gemini-2.5-flash": {"input_per_m": 0.30, "output_per_m": 2.50},
    # Gemini 2.5 Pro
    "gemini-2.5-pro": {"input_per_m": 1.25, "output_per_m": 5.00},
    # Gemini 1.5 Flash
    "gemini-1.5-flash": {"input_per_m": 0.075, "output_per_m": 0.30},
    # Gemini 1.5 Pro
    "gemini-1.5-pro": {"input_per_m": 1.25, "output_per_m": 5.00},
    # Gemini 3.1 Pro Preview
    "gemini-3.1-pro-preview": {"input_per_m": 1.25, "output_per_m": 5.00},
    # Gemini 3 Flash Preview (fallback price record)
    "gemini-3-flash-preview": {"input_per_m": 0.075, "output_per_m": 0.30},
}

DEFAULT_USD_INR = 84.0


def get_usd_inr_rate() -> float:
    """Retrieve USD to INR exchange rate from environment or fallback default."""
    try:
        return float(os.environ.get("USD_TO_INR_RATE", str(DEFAULT_USD_INR)))
    except (ValueError, TypeError):
        return DEFAULT_USD_INR


def calculate_cost(
    model: str,
    prompt_tokens: int,
    candidates_tokens: int,
) -> tuple[float, float]:
    """Calculate (cost_usd, cost_inr) based on model pricing."""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        # Match base prefix if exact match not found (e.g. gemini-2.5-flash-001)
        for known_model, rates in MODEL_PRICING.items():
            if model.startswith(known_model):
                pricing = rates
                break
        if not pricing:
            pricing = MODEL_PRICING["gemini-2.5-flash"]

    cost_usd = (prompt_tokens / 1_000_000.0) * pricing["input_per_m"] + (
        candidates_tokens / 1_000_000.0
    ) * pricing["output_per_m"]
    cost_inr = cost_usd * get_usd_inr_rate()
    return round(cost_usd, 6), round(cost_inr, 4)


class UsageTracker:
    """Thread-safe cumulative usage and cost tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._summary = UsageSummary()

    def record(self, meta: UsageMetadata) -> None:
        """Record an individual call's usage metadata."""
        with self._lock:
            self._summary.total_calls += 1
            self._summary.total_prompt_tokens += meta.prompt_tokens
            self._summary.total_candidates_tokens += meta.candidates_tokens
            self._summary.total_tokens += meta.total_tokens
            self._summary.total_cost_usd += meta.cost_usd
            self._summary.total_cost_inr += meta.cost_inr

            m = meta.model or "unknown"
            self._summary.calls_by_model[m] = self._summary.calls_by_model.get(m, 0) + 1
            self._summary.tokens_by_model[m] = (
                self._summary.tokens_by_model.get(m, 0) + meta.total_tokens
            )

    def get_summary(self) -> UsageSummary:
        """Return a copy of the current usage summary."""
        with self._lock:
            return self._summary.model_copy(deep=True)

    def reset(self) -> None:
        """Reset counters to zero."""
        with self._lock:
            self._summary = UsageSummary()
