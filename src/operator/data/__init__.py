"""Data port package exporting schemas, adapters, and factory."""

from src.operator.data.drive import DrivePublicDataSource
from src.operator.data.factory import get_data_source
from src.operator.data.local import LocalFolderDataSource
from src.operator.data.protocol import DataSourcePort
from src.operator.data.schema import (
    Answer,
    AnswerLibrary,
    DataSnapshot,
    JobPosting,
    Profile,
    Rules,
)

__all__ = [
    "Answer",
    "AnswerLibrary",
    "DataSnapshot",
    "DataSourcePort",
    "DrivePublicDataSource",
    "JobPosting",
    "LocalFolderDataSource",
    "Profile",
    "Rules",
    "get_data_source",
]
