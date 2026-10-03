"""Evidence capture manager for screenshots, DOM snapshots, and diff logs."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from playwright.sync_api import Page as SyncPage
from playwright.async_api import Page as AsyncPage

logger = logging.getLogger(__name__)


class EvidenceManager:
    """Manages writing artifacts and evidence to disk for auditability."""

    def __init__(self, run_id: str, base_dir: Path | str = "runs") -> None:
        self.run_id = run_id
        self.evidence_dir = Path(base_dir).resolve() / run_id / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_name(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)[:50]

    def capture_screenshot_sync(
        self,
        page: SyncPage,
        action_name: str,
        full_page: bool = True,
    ) -> str:
        """Capture page screenshot synchronously."""
        ts = int(time.time() * 1000)
        safe_name = self._sanitize_name(action_name)
        filename = f"{ts}_{safe_name}.png"
        target_path = self.evidence_dir / filename
        try:
            page.screenshot(path=str(target_path), full_page=full_page)
            return str(target_path)
        except Exception as e:
            logger.warning("Failed to capture screenshot for %s: %s", action_name, e)
            return ""

    async def capture_screenshot_async(
        self,
        page: AsyncPage,
        action_name: str,
        full_page: bool = True,
    ) -> str:
        """Capture page screenshot asynchronously."""
        ts = int(time.time() * 1000)
        safe_name = self._sanitize_name(action_name)
        filename = f"{ts}_{safe_name}.png"
        target_path = self.evidence_dir / filename
        try:
            await page.screenshot(path=str(target_path), full_page=full_page)
            return str(target_path)
        except Exception as e:
            logger.warning("Failed to capture async screenshot for %s: %s", action_name, e)
            return ""

    def capture_dom_snapshot_sync(self, page: SyncPage, action_name: str) -> str:
        """Capture DOM HTML snapshot synchronously."""
        ts = int(time.time() * 1000)
        safe_name = self._sanitize_name(action_name)
        filename = f"{ts}_{safe_name}.html"
        target_path = self.evidence_dir / filename
        try:
            html = page.content()
            target_path.write_text(html, encoding="utf-8")
            return str(target_path)
        except Exception as e:
            logger.warning("Failed to capture DOM snapshot for %s: %s", action_name, e)
            return ""

    def record_diff(
        self,
        field_key: str,
        intended: Any,
        actual: Any,
        matched: bool,
    ) -> str:
        """Record JSON log entry for readback diff."""
        ts = int(time.time() * 1000)
        diff_data = {
            "timestamp": ts,
            "field_key": field_key,
            "intended": intended,
            "actual": actual,
            "matched": matched,
        }
        filename = f"{ts}_diff_{self._sanitize_name(field_key)}.json"
        target_path = self.evidence_dir / filename
        try:
            target_path.write_text(json.dumps(diff_data, indent=2), encoding="utf-8")
            return str(target_path)
        except Exception as e:
            logger.warning("Failed to record diff for %s: %s", field_key, e)
            return ""
