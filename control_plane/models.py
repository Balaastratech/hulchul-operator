"""Shared data shapes used by the control plane: Command, Event, ReviewSnapshot and friends.

These are the frozen contracts from ``src.operator.contracts`` (contracts v0.1). This
module is the single place in ``control_plane/`` that names them, so the rest of the
package keeps importing ``from .models import Command, Event, ReviewSnapshot``.

Earlier revisions of this file carried hand-built copies derived from the exported JSON
Schemas; they were replaced by these imports once the contracts were merged to main.
The constants below are derived from the contract models, so they cannot drift.
"""
from __future__ import annotations

from typing import get_args

from src.operator.contracts import (
    Command,
    Contract,
    Event,
    FieldResult,
    ReviewSnapshot,
    RunStatus,
    Upload,
)

HEX64_PATTERN = r"^[0-9a-f]{64}$"

# Derived from the contract's Literal types (E01..E15, approve..handoff_done).
EVENT_IDS: tuple[str, ...] = get_args(Event.model_fields["event_id"].annotation)
COMMAND_ACTIONS: tuple[str, ...] = get_args(Command.model_fields["action"].annotation)

__all__ = [
    "COMMAND_ACTIONS",
    "EVENT_IDS",
    "HEX64_PATTERN",
    "Command",
    "Contract",
    "Event",
    "FieldResult",
    "ReviewSnapshot",
    "RunStatus",
    "Upload",
]
