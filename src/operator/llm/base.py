"""Base abstract LLM adapter with retry, fallback, structured parsing, and cost accounting."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.operator.llm.cost import UsageTracker, calculate_cost
from src.operator.llm.protocol import LLMResponse, UsageMetadata, UsageSummary

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

FORBIDDEN_DEFAULT_MODEL = "gemini-3-flash-preview"
SAFE_DEFAULT_MODEL = "gemini-2.5-flash"


class LLMError(Exception):
    """Base error for LLM calls."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class LLMValidationError(LLMError):
    """Failed to validate structured LLM response against Pydantic schema."""


def clean_json_text(text: str) -> str:
    """Strip markdown code fences and extraneous whitespace from JSON responses."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class BaseLLMAdapter(ABC):
    """Base class for Gemini LLM adapters (Vertex AI and Gemini API)."""

    def __init__(
        self,
        default_model: str = SAFE_DEFAULT_MODEL,
        fallback_model: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if default_model == FORBIDDEN_DEFAULT_MODEL:
            logger.warning(
                "Model '%s' is forbidden as default due to repeated timeouts in spike S1. "
                "Switching default to '%s'.",
                FORBIDDEN_DEFAULT_MODEL,
                SAFE_DEFAULT_MODEL,
            )
            default_model = SAFE_DEFAULT_MODEL

        self.default_model = default_model
        self.fallback_model = fallback_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.usage_tracker = UsageTracker()

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'vertex' or 'gemini_api')."""
        ...

    @abstractmethod
    def _call_api(
        self,
        model: str,
        prompt: str,
        system_instruction: str | None,
        temperature: float,
        response_mime_type: str | None,
        timeout: float,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Internal call to the underlying API.

        Returns:
            (raw_text, usage_metadata_dict, full_response_dict)
        """
        ...

    def _execute_with_retry_and_fallback(
        self,
        prompt: str,
        system_instruction: str | None,
        model: str | None,
        temperature: float,
        response_mime_type: str | None,
    ) -> tuple[str, UsageMetadata, dict[str, Any]]:
        target_model = model or self.default_model
        if target_model == FORBIDDEN_DEFAULT_MODEL and not model:
            target_model = SAFE_DEFAULT_MODEL

        models_to_try = [target_model]
        if self.fallback_model and self.fallback_model != target_model:
            models_to_try.append(self.fallback_model)

        last_error: Exception | None = None

        for cur_model in models_to_try:
            for attempt in range(self.max_retries + 1):
                t0 = time.time()
                try:
                    logger.debug(
                        "LLM call to %s [%s] attempt %d/%d",
                        cur_model,
                        self.provider_name,
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    raw_text, usage_dict, full_resp = self._call_api(
                        model=cur_model,
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        response_mime_type=response_mime_type,
                        timeout=self.timeout_seconds,
                    )
                    duration = time.time() - t0

                    prompt_tokens = usage_dict.get("promptTokenCount", 0)
                    candidates_tokens = usage_dict.get("candidatesTokenCount", 0)
                    thoughts_tokens = usage_dict.get("thoughtsTokenCount", 0)
                    total_tokens = usage_dict.get(
                        "totalTokenCount", prompt_tokens + candidates_tokens + thoughts_tokens
                    )
                    billed_output_tokens = candidates_tokens + thoughts_tokens

                    cost_usd, cost_inr = calculate_cost(
                        cur_model, prompt_tokens, billed_output_tokens
                    )
                    meta = UsageMetadata(
                        prompt_tokens=prompt_tokens,
                        candidates_tokens=candidates_tokens,
                        total_tokens=total_tokens,
                        model=cur_model,
                        provider=self.provider_name,
                        cost_usd=cost_usd,
                        cost_inr=cost_inr,
                        duration_sec=round(duration, 3),
                    )
                    self.usage_tracker.record(meta)
                    return raw_text, meta, full_resp

                except Exception as e:
                    last_error = e
                    duration = time.time() - t0
                    logger.warning(
                        "LLM error on %s (attempt %d, %.1fs): %s",
                        cur_model,
                        attempt + 1,
                        duration,
                        e,
                    )
                    if attempt < self.max_retries:
                        time.sleep(1.5 * (attempt + 1))

            logger.warning(
                "Primary model %s failed all %d retries. Trying fallback if available.",
                cur_model,
                self.max_retries + 1,
            )

        raise LLMError(f"All LLM attempts failed. Last error: {last_error}") from last_error

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Generate unstructured text from a prompt."""
        raw_text, meta, full_resp = self._execute_with_retry_and_fallback(
            prompt=prompt,
            system_instruction=system_instruction,
            model=model,
            temperature=temperature,
            response_mime_type=None,
        )
        return LLMResponse(content=raw_text, usage=meta, raw_response=full_resp)

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> tuple[T, LLMResponse]:
        """Generate structured JSON adhering to the given Pydantic schema."""
        # Append schema instructions if not already embedded
        json_schema_str = json.dumps(schema.model_json_schema())
        schema_instruction = (
            f"\n\nYou must return valid JSON matching this schema exactly:\n{json_schema_str}"
        )
        effective_prompt = prompt + schema_instruction

        raw_text, meta, full_resp = self._execute_with_retry_and_fallback(
            prompt=effective_prompt,
            system_instruction=system_instruction,
            model=model,
            temperature=temperature,
            response_mime_type="application/json",
        )

        cleaned_text = clean_json_text(raw_text)
        try:
            parsed_instance = schema.model_validate_json(cleaned_text)
            response = LLMResponse(
                content=cleaned_text, usage=meta, raw_response=full_resp
            )
            return parsed_instance, response
        except ValidationError as ve:
            err_details = [
                f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('type')}"
                for err in ve.errors()
            ]
            logger.error("Structured output validation failed against %s: %s", schema.__name__, err_details)
            raise LLMValidationError(
                f"Failed to validate model response against {schema.__name__}: {err_details}"
            ) from None
        except json.JSONDecodeError as je:
            logger.error("JSON decode error for %s: %s", schema.__name__, type(je).__name__)
            raise LLMValidationError(
                f"Failed to decode JSON from model response against {schema.__name__}: {type(je).__name__}"
            ) from None

    async def structured(
        self,
        prompt: str,
        response_model: type[T],
    ) -> T:
        """canonical contracts-v0.1a compatible async structured generation."""
        instance, _ = self.generate_structured(prompt=prompt, schema=response_model)
        return instance

    def get_usage(self) -> UsageSummary:
        """Return cumulative usage summary."""
        return self.usage_tracker.get_summary()


    def reset_usage(self) -> None:
        """Reset cumulative usage counters."""
        self.usage_tracker.reset()
