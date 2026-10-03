"""Vertex AI adapter for Gemini models using Application Default Credentials."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
import requests

try:
    import google.auth
    import google.auth.transport.requests
    _HAS_GOOGLE_AUTH = True
except ImportError:
    _HAS_GOOGLE_AUTH = False

from src.operator.llm.base import BaseLLMAdapter, LLMError, LLMTimeoutError

logger = logging.getLogger(__name__)


class VertexLLMAdapter(BaseLLMAdapter):
    """Adapter for Google Cloud Vertex AI generateContent REST API."""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
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
        self.project_id = project_id
        self.location = location
        self._session = requests.Session()
        self._credentials = None
        self._auth_request = None
        self._init_credentials()

    def _init_credentials(self) -> None:
        if not _HAS_GOOGLE_AUTH:
            raise LLMError("google-auth is not installed. Required for Vertex AI provider.")
        try:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._auth_request = google.auth.transport.requests.Request()
            self._credentials.refresh(self._auth_request)
        except Exception as e:
            logger.error("Failed to acquire Google Cloud Application Default Credentials: %s", e)
            raise LLMError(f"ADC authentication failed for Vertex AI: {e}") from e

    def _get_access_token(self) -> str:
        if not self._credentials or not self._credentials.valid:
            if self._credentials and self._auth_request:
                self._credentials.refresh(self._auth_request)
            else:
                self._init_credentials()
        return self._credentials.token

    @property
    def provider_name(self) -> str:
        return "vertex"

    def _call_api(
        self,
        model: str,
        prompt: str,
        system_instruction: str | None,
        temperature: float,
        response_mime_type: str | None,
        timeout: float,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        token = self._get_access_token()
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project_id}"
            f"/locations/{self.location}/publishers/google/models/{model}:generateContent"
        )

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
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            resp = self._session.post(url, json=body, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout as e:
            raise LLMTimeoutError(f"Vertex AI request timed out after {timeout}s: {e}") from e
        except requests.exceptions.RequestException as e:
            raise LLMError(f"Vertex AI network request failed: {e}") from e

        if resp.status_code != 200:
            err_text = resp.text[:500]
            raise LLMError(f"Vertex AI returned HTTP {resp.status_code}: {err_text}")

        data = resp.json()
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise LLMError(f"Vertex AI returned no candidates: {data}")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise LLMError(f"Candidate contains no text parts: {candidates[0]}")
            raw_text = parts[0].get("text", "")
            usage_dict = data.get("usageMetadata", {})
            return raw_text, usage_dict, data
        except (KeyError, IndexError) as e:
            raise LLMError(f"Failed to parse Vertex AI response format: {e}, data: {data}") from e
