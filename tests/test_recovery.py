from __future__ import annotations

from pathlib import Path

import pytest

from quicktype.backup import BackupFormatError, export_backup, import_backup
from quicktype.models import Snippet, TriggerMode
from quicktype.recovery import restore_backup
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
