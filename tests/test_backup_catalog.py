from __future__ import annotations

import os
from pathlib import Path

from quicktype.backup import export_backup
from quicktype.backup_catalog import (
    BackupKind,
    classify_backup,
    list_backup_entries,
)
from quicktype.models import Snippet, TriggerMode


def _export(path: Path, abbreviation: str) -> None:
    export_backup(
        path,
        [
            Snippet(
                None,
                abbreviation,
                abbreviation,
                TriggerMode.IMMEDIATE,
            )
        ],
    )


def test_backup_kinds_are_classified_from_generated_names() -> None:
    assert classify_backup(
        Path("QuickType-auto-20260729-120000-000001.json")
    ) == BackupKind.AUTOMATIC
    assert classify_backup(
        Path("QuickType-manual-20260729-120000-000001.json")
    ) == BackupKind.MANUAL
    assert classify_backup(
        Path("QuickType-before-import-20260729-120000-000001.json")
    ) == BackupKind.BEFORE_IMPORT
    assert classify_backup(
        Path("QuickType-before-restore-20260729-120000-000001.json")
    ) == BackupKind.BEFORE_RESTORE
    assert classify_backup(Path("my-export.json")) == BackupKind.OTHER


def test_catalog_lists_valid_backups_newest_first_and_skips_invalid(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "Backups"
    older = directory / "QuickType-manual-20260729-120000-000001.json"
    newer = directory / "QuickType-before-import-20260729-120100-000001.json"
    other = directory / "custom.json"
    _export(older, "old")
    _export(newer, "new")
    _export(other, "other")
    os.utime(older, (1000, 1000))
    os.utime(other, (1500, 1500))
    os.utime(newer, (2000, 2000))
    invalid = directory / "QuickType-auto-20260729-120200-000001.json"
    invalid.write_text("not json", encoding="utf-8")

    entries = list_backup_entries(directory)

    assert [entry.path for entry in entries] == [newer, other, older]
    assert [entry.kind for entry in entries] == [
        BackupKind.BEFORE_IMPORT,
        BackupKind.OTHER,
        BackupKind.MANUAL,
    ]
    assert all(entry.snippet_count == 1 for entry in entries)
