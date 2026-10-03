"""Live Telegram smoke test. Skipped unless RUN_LIVE_TELEGRAM=1. Never prints secrets.

    $env:RUN_LIVE_TELEGRAM = "1"
    python -m pytest tests/channels/manual_telegram_smoke.py -q -p no:cacheprovider

Sends ONE short E01 message with a view-token button to the chat in TELEGRAM_CHAT_ID (first
entry), using the .env named by ENV_FILE. Needs CP_SIGNING_KEY, CP_WORKER_TOKEN and a public
HTTPS origin in CP_BASE_URL / CP_PUBLIC_URL. The named file is not collected by default
(it does not match test_*.py) and skips itself without the env flag.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from control_plane.config import ConfigError, load_config
from control_plane.tokens import TokenService
from src.operator.channels import BotApi, InMemoryTelegramState, TelegramChannel

from .support import make_event

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TELEGRAM") != "1", reason="set RUN_LIVE_TELEGRAM=1 to send one real message"
)


def test_live_e01_message_is_delivered():
    try:
        config = load_config()
    except ConfigError as exc:  # names the variable only, never a value
        pytest.skip(f"configuration incomplete: {exc}")
    if not config.telegram_enabled:
        pytest.skip("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured")
    chat = sorted(config.telegram_chat_ids)[0]

    async def send() -> None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            api = BotApi(config.telegram_bot_token or "", client=client)
            channel = TelegramChannel(
                api, chat_ids=frozenset({chat}), public_url=config.base_url,
                tokens=TokenService(config.signing_key, config.signing_key_previous),
                state=InMemoryTelegramState(),
            )
            await channel.emit(make_event("E01", job=None, run_id="smoke_run",
                                          goal="Channel smoke test (safe to ignore)"))

    asyncio.run(send())  # raises ChannelError (code only, no secrets) when delivery fails
