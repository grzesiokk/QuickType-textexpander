from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .auto_backup import AUTO_BACKUP_PATTERN
from .backup import BackupFormatError, import_backup


class BackupKind(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    BEFORE_IMPORT = "before_import"
    BEFORE_RESTORE = "before_restore"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class BackupEntry:
    path: Path
    kind: BackupKind
    modified_at: datetime
    snippet_count: int


TIMESTAMP_SUFFIX = r"\d{8}-\d{6}-\d{6}\.json"
BACKUP_PATTERNS = {
    BackupKind.MANUAL: re.compile(
        rf"QuickType-manual-{TIMESTAMP_SUFFIX}"
    ),
    BackupKind.BEFORE_IMPORT: re.compile(
        rf"QuickType-before-import-{TIMESTAMP_SUFFIX}"
    ),
    BackupKind.BEFORE_RESTORE: re.compile(
        rf"QuickType-before-restore-{TIMESTAMP_SUFFIX}"
    ),
}


def classify_backup(path: Path) -> BackupKind:
    name = Path(path).name
    if AUTO_BACKUP_PATTERN.fullmatch(name):
        return BackupKind.AUTOMATIC
    for kind, pattern in BACKUP_PATTERNS.items():
        if pattern.fullmatch(name):
            return kind
    return BackupKind.OTHER


def list_backup_entries(directory: Path) -> list[BackupEntry]:
    location = Path(directory)
    if not location.exists():
        return []
    entries: list[BackupEntry] = []
    for path in location.iterdir():
        if not path.is_file() or path.suffix.casefold() != ".json":
            continue
        try:
            snippets = import_backup(path)
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)
        except (OSError, BackupFormatError):
            continue
        entries.append(
            BackupEntry(
                path=path,
                kind=classify_backup(path),
                modified_at=modified_at,
                snippet_count=len(snippets),
            )
        )
    return sorted(
        entries,
        key=lambda entry: (
            entry.modified_at,
            entry.path.name.casefold(),
        ),
        reverse=True,
    )


def delete_backup_file(directory: Path, path: Path) -> None:
    location = Path(directory).resolve()
    target = Path(path).resolve()
    if target.parent != location or target.suffix.casefold() != ".json":
        raise ValueError("The selected file is outside the backup directory.")
    target.unlink()
