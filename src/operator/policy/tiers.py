"""Deterministic authority vocabulary from POLICY_AND_SAFETY section 1."""

from enum import IntEnum


class Tier(IntEnum):
    """Model output cannot promote an action's authority."""

    READ = 0
    LOCAL = 1
    REVERSIBLE = 2
    EXTERNAL = 3
    FORBIDDEN = 4


def action_tier(action: str) -> Tier:
    """Unknown actions fail closed; all messages and consent are external."""
    if action in {"read", "screenshot", "navigate", "read_data"}:
        return Tier.READ
    if action in {"score", "draft", "ledger", "skip", "ask_user"}:
        return Tier.LOCAL
    if action in {"fill", "select", "check", "upload_resume", "click_next"}:
        return Tier.REVERSIBLE
    if action in {"submit", "apply", "send_message", "create_account", "consent", "legal"}:
        return Tier.EXTERNAL
    return Tier.FORBIDDEN
