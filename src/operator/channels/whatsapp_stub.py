"""WhatsApp channel stub (D-007; CONTROL_PLANE_API.md section 7.4).

WhatsApp is deliberately not built in v1. This stub only reserves the seam: it implements
`ChannelPort`, adds no routes and always raises `NotConfigured` (a `ChannelError` that is
also a `NotImplementedError`), so a misconfigured deployment fails loudly instead of
silently dropping a message.

A future adapter must reuse the same services as Telegram: outbound through the same
message renderer and view-link minter, inbound through the same reply-to-question rules
(answers only; never approve, edit, reject or hand off). Nothing in the worker, the routes
or the tokens changes when it is added.
"""
from __future__ import annotations

from src.operator.contracts import Event

from .base import NotConfigured


class WhatsAppStub:
    """ChannelPort placeholder."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    async def emit(self, event: Event) -> None:
        if not self.enabled:
            raise NotConfigured("whatsapp_not_configured", "WhatsApp channel is not enabled")
        raise NotConfigured("whatsapp_not_implemented", "no WhatsApp adapter exists in v1 (D-007)")
