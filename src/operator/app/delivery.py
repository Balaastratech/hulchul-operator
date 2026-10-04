"""Idempotent event delivery for the real composition.

Why one event was sent twice: graph nodes such as `human_handoff` and `ask_user` call
`services.emit(...)` and THEN `interrupt(...)`. LangGraph re-runs the whole node from its first
line when the human's command resumes it, so the emit runs a second time, with a new
`created_at`, which every downstream dedup (Telegram, control plane) treats as a new event.

A re-run of a node is the same LangGraph *task*: the task id is derived from the checkpoint that
started it, so it is identical on the first run and on the resume, and different for a genuinely
new visit to the same node (a second hand-off on the same job is a new task). The delivery key is
therefore ``sha256(task id | event id | run | job | message | payload)``: same task plus same
content is a replay, anything else is delivered. Outside a graph task there is no task id and
nothing is suppressed (a duplicate message is noise; a swallowed hand-off would strand the user).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.operator.app.fsutil import write_json_atomic
from src.operator.contracts import Event

TASK_ID_KEY = "__pregel_task_id"  # langgraph's CONFIG_KEY_TASK_ID
MAX_REMEMBERED = 2000


def current_task_id() -> str | None:
    """The running LangGraph task's id, or None outside a graph node."""
    try:
        from langgraph.config import get_config

        value = (get_config().get("configurable") or {}).get(TASK_ID_KEY)
    except Exception:  # noqa: BLE001 - no runnable context (unit tests, scripts)
        return None
    return value if isinstance(value, str) and value else None


def delivery_key(event: Event, task_id: str) -> str:
    """Deterministic id of one emission: excludes `created_at` and `links` (both change on replay)."""
    body = json.dumps(
        [task_id, event.event_id, event.run_id, event.job_id, event.message, event.payload],
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DeliveryLog:
    """Keys of events already delivered, kept on disk so a worker restart cannot re-send them."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._keys: list[str] = []
        if path is not None and path.exists():
            try:
                loaded: Any = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = []
            self._keys = [k for k in loaded if isinstance(k, str)] if isinstance(loaded, list) else []
        self._set = set(self._keys)

    def seen(self, key: str) -> bool:
        return key in self._set

    def mark(self, key: str) -> None:
        if key in self._set:
            return
        self._keys.append(key)
        self._set.add(key)
        if len(self._keys) > MAX_REMEMBERED:
            drop, self._keys = self._keys[:-MAX_REMEMBERED], self._keys[-MAX_REMEMBERED:]
            self._set.difference_update(drop)
        if self.path is not None:
            write_json_atomic(self.path, json.dumps(self._keys))
