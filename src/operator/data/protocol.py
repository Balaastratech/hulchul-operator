"""DataSourcePort protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from src.operator.data.schema import DataSnapshot


@runtime_checkable
class DataSourcePort(Protocol):
    """Port for loading candidate and queue data snapshot."""

    async def load(self, run_id: str) -> DataSnapshot:
        """Asynchronously load data snapshot for a run."""
        ...

    def load_sync(self, run_id: str) -> DataSnapshot:
        """Synchronously load data snapshot for a run."""
        ...
