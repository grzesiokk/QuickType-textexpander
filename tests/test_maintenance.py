from __future__ import annotations

from pathlib import Path

from quicktype.backup import import_backup
from quicktype.maintenance import (
    collect_data_summary,
    create_manual_backup,
    format_file_size,
)
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage


def test_manual_backup_and_data_summary(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )

    before = collect_data_summary(storage)
    path = create_manual_backup(storage)
    after = collect_data_summary(storage)

    assert before.snippet_count == 1
    assert before.backup_count == 0
    assert before.database_bytes > 0
    assert path.exists()
    assert path.parent == storage.path.parent / "Backups"
    assert path.name.startswith("QuickType-manual-")
    assert import_backup(path)[0].abbreviation == "sig"
    assert after.backup_count == 1


def test_file_size_formatting() -> None:
    assert format_file_size(0) == "0 B"
    assert format_file_size(1023) == "1023 B"
    assert format_file_size(1024) == "1.0 KB"
    assert format_file_size(1024 * 1024) == "1.0 MB"
