"""File helpers for state shared between the launcher and the worker process .

On Windows `os.replace` raises PermissionError (WinError 5 or 32) while another process holds the
destination open, for example the launcher reading `state.json` at the same moment. The reader
closes it within milliseconds, so a short retry loop is the right fix. Any other error, and a
PermissionError that does not go away, is raised unchanged.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

REPLACE_TRIES = 40
REPLACE_DELAY_S = 0.05


def replace_with_retry(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    tries: int = REPLACE_TRIES,
    delay: float = REPLACE_DELAY_S,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], object] = os.replace,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    """`os.replace` that retries on PermissionError (about 40 tries, 50 ms apart = 2 s)."""
    attempts = max(1, tries)
    for attempt in range(1, attempts + 1):
        try:
            replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            sleep(delay)


def write_json_atomic(path: Path, text: str) -> None:
    """Write `text` next to `path`, then replace it (readers never see a half-written file)."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    replace_with_retry(temp, path)
