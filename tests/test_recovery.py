from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quicktype.backup import BackupFormatError, export_backup, import_backup
from quicktype.models import Snippet, TriggerMode
from quicktype.recovery import (
    RestoreChangeKind,
    analyze_restore,
    restore_backup,
)
from quicktype.storage import Storage


def test_restore_replaces_library_and_saves_previous_state(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "current", "Current text", TriggerMode.IMMEDIATE)
    )
    source = tmp_path / "restore.json"
    export_backup(
        source,
        [
            Snippet(None, "old1", "First", TriggerMode.DELIMITER),
            Snippet(None, "old2", "Second", TriggerMode.IMMEDIATE),
        ],
    )

    count, safety_copy = restore_backup(storage, source)

    assert count == 2
    assert [snippet.abbreviation for snippet in storage.list_snippets()] == [
        "old1",
        "old2",
    ]
    assert safety_copy.exists()
    assert safety_copy.name.startswith("QuickType-before-restore-")
    assert import_backup(safety_copy)[0].abbreviation == "current"


def test_restore_analysis_counts_library_changes_without_timestamp_noise(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            "same",
            "Same text",
            TriggerMode.DELIMITER,
            usage_count=4,
            category="Work",
            favorite=True,
            applications=("notepad.exe",),
        )
    )
    storage.save_snippet(
        Snippet(None, "changed", "Old text", TriggerMode.IMMEDIATE)
    )
    storage.save_snippet(
        Snippet(None, "removed", "Local only", TriggerMode.IMMEDIATE)
    )
    current = {
        snippet.abbreviation: snippet for snippet in storage.list_snippets()
    }
    different_timestamp = datetime.now(timezone.utc) - timedelta(days=365)
    source = tmp_path / "restore.json"
    export_backup(
        source,
        [
            Snippet(
                None,
                "same",
                "Same text",
                TriggerMode.DELIMITER,
                created_at=different_timestamp,
                updated_at=different_timestamp,
                usage_count=4,
                category="Work",
                favorite=True,
                applications=("notepad.exe",),
            ),
            Snippet(
                None,
                "changed",
                "New text",
                current["changed"].trigger_mode,
                enabled=False,
                usage_count=2,
                category="Updated",
                favorite=True,
                applications=("Code.exe",),
            ),
            Snippet(None, "added", "Backup only", TriggerMode.IMMEDIATE),
        ],
    )

    analysis = analyze_restore(storage, source)

    assert analysis.source == source
    assert analysis.incoming_count == 3
    assert analysis.added == 1
    assert analysis.updated == 1
    assert analysis.removed == 1
    assert analysis.unchanged == 1
    changes = {change.abbreviation: change for change in analysis.changes}
    assert changes["added"].kind == RestoreChangeKind.ADDED
    assert changes["changed"].kind == RestoreChangeKind.CHANGED
    assert changes["changed"].changed_fields == (
        "expansion",
        "enabled",
        "usage_count",
        "category",
        "favorite",
        "applications",
    )
    assert changes["removed"].kind == RestoreChangeKind.REMOVED
    assert changes["same"].kind == RestoreChangeKind.UNCHANGED
    assert changes["same"].changed_fields == ()


def test_invalid_restore_does_not_change_data_or_write_safety_copy(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "current", "Current text", TriggerMode.IMMEDIATE)
    )
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")

    with pytest.raises(BackupFormatError):
        restore_backup(storage, invalid)

    assert storage.list_snippets()[0].abbreviation == "current"
    backup_directory = storage.path.parent / "Backups"
    assert not backup_directory.exists()
