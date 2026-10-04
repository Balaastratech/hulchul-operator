"""Offline, read-only run reports. No worker or control-plane mutations."""

from .viewer import write_report

__all__ = ["write_report"]
