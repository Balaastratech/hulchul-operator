"""Factory for instantiating configured DataSourcePort."""

from __future__ import annotations

import os
from pathlib import Path
from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.local import LocalFolderDataSource
from src.operator.data.protocol import DataSourcePort


def get_data_source(
    source_type: str | None = None,
    folder_or_path: str | Path | None = None,
    fallback_dir: str | Path | None = "sample_data",
) -> DataSourcePort:
    """Create DataSourcePort based on environment or explicit arguments."""
    chosen_source = (source_type or os.environ.get("DATA_SOURCE", "local_folder")).lower().strip()

    if chosen_source == "drive_public":
        folder_id = str(folder_or_path) if folder_or_path else os.environ.get("DRIVE_FOLDER_ID", "")
        return DrivePublicDataSource(
            folder_id=folder_id,
            fallback_dir=fallback_dir,
        )
    else:
        path = folder_or_path or fallback_dir or "sample_data"
        return LocalFolderDataSource(data_dir=path)
