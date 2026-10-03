"""Delivery channels implementing ChannelPort (T-020): Telegram, web, WhatsApp stub."""
from .base import (
    CHANNEL_UNREACHABLE,
    ChannelError,
    ChannelPort,
    FallbackChannel,
    NotConfigured,
)
from .telegram import (
    BotApi,
    ChatGate,
    InMemoryTelegramState,
    TelegramChannel,
    TelegramInbound,
    TelegramState,
    make_telegram,
)
from .web import EventSink, HttpSink, StoreSink, WebChannel
from .whatsapp_stub import WhatsAppStub

__all__ = [
    "CHANNEL_UNREACHABLE",
    "BotApi",
    "ChannelError",
    "ChannelPort",
    "ChatGate",
    "EventSink",
    "FallbackChannel",
    "HttpSink",
    "InMemoryTelegramState",
    "NotConfigured",
    "StoreSink",
    "TelegramChannel",
    "TelegramInbound",
    "TelegramState",
    "WebChannel",
    "WhatsAppStub",
    "make_telegram",
]
