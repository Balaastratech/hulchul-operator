"""Factory for creating LLMPort instances based on configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

from src.operator.llm.base import (
    FORBIDDEN_DEFAULT_MODEL,
    SAFE_DEFAULT_MODEL,
    BaseLLMAdapter,
    LLMError,
)
from src.operator.llm.gemini_api import GeminiAPIAdapter
from src.operator.llm.protocol import LLMPort
from src.operator.llm.vertex import VertexLLMAdapter

logger = logging.getLogger(__name__)

ENV_FILE_PATH = os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env")


def load_config() -> None:
    """Load environment variables from canonical .env file if it exists."""
    p = Path(ENV_FILE_PATH)
    if p.is_file():
        load_dotenv(dotenv_path=p, override=False)


def get_llm_port(
    provider: str | None = None,
    model: str | None = None,
    fallback_model: str | None = None,
    timeout_seconds: float | None = None,
) -> LLMPort:
    """Instantiate and configure the LLMPort adapter.
    
    Defaults:
        provider: env LLM_PROVIDER or 'vertex'
        model: env LLM_MODEL or 'gemini-2.5-flash'
        fallback_model: env LLM_FALLBACK_MODEL or None
        timeout_seconds: env LLM_TIMEOUT_SECONDS or 60.0
    """
    load_config()

    chosen_provider = (
        provider or os.environ.get("LLM_PROVIDER", "vertex")
    ).lower().strip()

    chosen_model = (
        model or os.environ.get("LLM_MODEL", SAFE_DEFAULT_MODEL)
    ).strip()

    # Safety check: NEVER default to gemini-3-flash-preview
    if chosen_model == FORBIDDEN_DEFAULT_MODEL:
        logger.warning(
            "Configured model '%s' is forbidden as default due to repeated timeouts in spike S1. "
            "Falling back to '%s'.",
            FORBIDDEN_DEFAULT_MODEL,
            SAFE_DEFAULT_MODEL,
        )
        chosen_model = SAFE_DEFAULT_MODEL

    chosen_fallback = fallback_model or os.environ.get("LLM_FALLBACK_MODEL")
    if chosen_fallback:
        chosen_fallback = chosen_fallback.strip()

    timeout_val = timeout_seconds
    if timeout_val is None:
        try:
            timeout_val = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60.0"))
        except ValueError:
            timeout_val = 60.0

    if chosen_provider == "vertex":
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "ai-negotiation-copilot")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        return VertexLLMAdapter(
            project_id=project_id,
            location=location,
            default_model=chosen_model,
            fallback_model=chosen_fallback,
            timeout_seconds=timeout_val,
        )

    elif chosen_provider == "gemini_api":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise LLMError("GEMINI_API_KEY environment variable is missing for gemini_api provider.")
        return GeminiAPIAdapter(
            api_key=api_key,
            default_model=chosen_model,
            fallback_model=chosen_fallback,
            timeout_seconds=timeout_val,
        )

    else:
        raise LLMError(f"Unsupported LLM provider: '{chosen_provider}'. Expected 'vertex' or 'gemini_api'.")
