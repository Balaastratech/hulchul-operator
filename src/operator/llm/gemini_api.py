"""Gemini Developer API adapter using API key authentication."""

from __future__ import annotations

import logging
from typing import Any
import requests

from src.operator.llm.base import BaseLLMAdapter, LLMError, LLMTimeoutError

logger = logging.getLogger(__name__)


class GeminiAPIAdapter(BaseLLMAdapter):
    """Adapter for Google AI Studio / Gemini Developer API via API key."""

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-2.5-flash",
        fallback_model: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            default_model=default_model,
            fallback_model=fallback_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        if not api_key:
            raise LLMError("GEMINI_API_KEY is required for gemini_api provider.")
        self.api_key = api_key
        self._session = requests.Session()

    @property
    def provider_name(self) -> str:
        return "gemini_api"

    def _call_api(
        self,
        model: str,
        prompt: str,
        system_instruction: str | None,
        temperature: float,
        response_mime_type: str | None,
        timeout: float,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        if response_mime_type:
            body["generationConfig"]["responseMimeType"] = response_mime_type

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        try:
            resp = self._session.post(url, json=body, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout as e:
            raise LLMTimeoutError(f"Gemini API request timed out after {timeout}s: {e}") from e
        except requests.exceptions.RequestException as e:
            raise LLMError(f"Gemini API network request failed: {e}") from e

        if resp.status_code != 200:
            err_text = resp.text[:500]
            raise LLMError(f"Gemini API returned HTTP {resp.status_code}: {err_text}")

        data = resp.json()
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise LLMError(f"Gemini API returned no candidates: {data}")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise LLMError(f"Candidate contains no text parts: {candidates[0]}")
            raw_text = parts[0].get("text", "")
            usage_dict = data.get("usageMetadata", {})
            return raw_text, usage_dict, data
        except (KeyError, IndexError) as e:
            raise LLMError(f"Failed to parse Gemini API response format: {e}, data: {data}") from e
