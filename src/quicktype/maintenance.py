from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .backup import export_backup
from .storage import Storage


@dataclass(frozen=True, slots=True)
class DataSummary:
    database_bytes: int
    snippet_count: int
    backup_count: int


def collect_data_summary(storage: Storage) -> DataSummary:
    backup_directory = storage.path.parent / "Backups"
    backup_count = (
        sum(
            1
            for path in backup_directory.iterdir()
            if path.is_file() and path.suffix.casefold() == ".json"
        )
        if backup_directory.exists()
        else 0
    )
    return DataSummary(
        database_bytes=storage.path.stat().st_size if storage.path.exists() else 0,
        snippet_count=len(storage.list_snippets()),
        backup_count=backup_count,
    )


def create_manual_backup(storage: Storage) -> Path:
    destination = storage.path.parent / "Backups" / (
        f"QuickType-manual-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    )
    export_backup(destination, storage.list_snippets())
    return destination


def format_file_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
