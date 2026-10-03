"""Google Drive public data source adapter via zero-credential export URLs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
import requests

from src.operator.data.local import LocalFolderDataSource
from src.operator.data.protocol import DataSourcePort
from src.operator.data.schema import DataSnapshot

logger = logging.getLogger(__name__)


def download_url(url: str, dest_path: Path, timeout: float = 25.0) -> bool:
    """Download content from URL and write to file."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(resp.content)
            return True
        logger.warning("Download failed for %s: HTTP %s", url, resp.status_code)
        return False
    except Exception as e:
        logger.error("Download exception for %s: %s", url, e)
        return False


def scrape_drive_folder_items(folder_id: str) -> dict[str, str]:
    """Scrape public Drive folder HTML for embedded file names and IDs."""
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return {}
        html = resp.text
        # Drive embeds file info in arrays: ["FILE_ID", ["FILENAME", ...]]
        matches = re.findall(r'\["([a-zA-Z0-9_-]{25,})",\s*\["([^"]+)"', html)
        items: dict[str, str] = {}
        for fid, fname in matches:
            items[fname] = fid
        return items
    except Exception as e:
        logger.error("Error scraping Drive folder %s: %s", folder_id, e)
        return {}


class DrivePublicDataSource(DataSourcePort):
    """Downloads candidate files from a public Google Drive folder using export URLs."""

    def __init__(
        self,
        folder_id: str | None = None,
        file_ids: dict[str, str] | None = None,
        cache_dir: Path | str = "data_cache",
        fallback_dir: Path | str | None = "sample_data",
    ) -> None:
        self.folder_id = (folder_id or os.environ.get("DRIVE_FOLDER_ID", "")).strip()
        self.file_ids = file_ids or {}
        self.cache_dir = Path(cache_dir).resolve()
        self.fallback_dir = Path(fallback_dir).resolve() if fallback_dir else None

    def _sync_drive_files(self, target_dir: Path) -> bool:
        """Download all files from Drive to local cache directory."""
        if not self.folder_id and not self.file_ids:
            return False

        file_map = dict(self.file_ids)
        if self.folder_id and not file_map:
            scraped = scrape_drive_folder_items(self.folder_id)
            if scraped:
                file_map.update(scraped)

        if not file_map:
            logger.warning("No file IDs discovered in Drive folder %s", self.folder_id)
            return False

        target_dir.mkdir(parents=True, exist_ok=True)
        success_count = 0

        for fname, fid in file_map.items():
            low_name = fname.lower()
            if "sheet" in low_name or low_name.endswith(".csv") or fname in ("answers", "job_queue"):
                # Google Sheet -> export as CSV
                ext = ".csv" if not low_name.endswith(".csv") else ""
                dest = target_dir / f"{fname}{ext}"
                url = f"https://docs.google.com/spreadsheets/d/{fid}/export?format=csv"
            elif "doc" in low_name or low_name.endswith(".md") or fname in ("rules", "profile"):
                # Google Doc -> export as txt
                ext = ".md" if not (low_name.endswith(".md") or low_name.endswith(".json")) else ""
                dest = target_dir / f"{fname}{ext}"
                url = f"https://docs.google.com/document/d/{fid}/export?format=txt"
            else:
                # PDF or other binary file -> direct download
                ext = ".pdf" if not low_name.endswith(".pdf") else ""
                dest = target_dir / f"{fname}{ext}"
                url = f"https://drive.google.com/uc?export=download&id={fid}"

            if download_url(url, dest):
                success_count += 1

        return success_count > 0

    def load_sync(self, run_id: str) -> DataSnapshot:
        """Load data from Drive cache or fallback local directory."""
        drive_cache_folder = self.cache_dir / (self.folder_id or "default")
        synced = False
        if self.folder_id or self.file_ids:
            synced = self._sync_drive_files(drive_cache_folder)

        if synced and (drive_cache_folder / "profile.json").exists():
            return LocalFolderDataSource(drive_cache_folder).load_sync(run_id)

        if self.fallback_dir and self.fallback_dir.is_dir():
            logger.info("Using local fallback directory: %s", self.fallback_dir)
            return LocalFolderDataSource(self.fallback_dir).load_sync(run_id)

        raise FileNotFoundError(
            f"Could not load data from Drive (folder_id={self.folder_id}) "
            f"and local fallback {self.fallback_dir} not available."
        )

    async def load(self, run_id: str) -> DataSnapshot:
        return self.load_sync(run_id)
