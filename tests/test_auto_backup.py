from __future__ import annotations

from pathlib import Path

from quicktype.auto_backup import (
    AUTO_BACKUP_PATTERN,
    AutomaticBackupManager,
    list_automatic_backups,
    normalize_backup_retention,
)
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage


def _save(storage: Storage, abbreviation: str, expansion: str) -> None:
    storage.save_snippet(
        Snippet(None, abbreviation, expansion, TriggerMode.IMMEDIATE)
    )


def test_automatic_backup_only_writes_when_snippets_change(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    _save(storage, "sig", "Regards")
    manager = AutomaticBackupManager(storage)

    first = manager.create_if_changed()
    unchanged = manager.create_if_changed()
    _save(storage, "mail", "hello@example.com")
    second = manager.create_if_changed()

    assert first is not None and first.exists()
    assert unchanged is None
    assert second is not None and second.exists()
    assert first != second
    assert first.parent == storage.path.parent / "Backups"


def test_automatic_backup_prunes_only_its_own_old_files(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    manager = AutomaticBackupManager(storage, retention=2)
    unrelated = manager.directory / "my-important-backup.json"
    manager.directory.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")

    for index in range(4):
        _save(storage, f"s{index}", str(index))
        manager.create_if_changed()

    automatic = [
        path
        for path in manager.directory.iterdir()
        if AUTO_BACKUP_PATTERN.fullmatch(path.name)
    ]
    assert len(automatic) == 2
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list_automatic_backups(manager.directory) == sorted(
        automatic,
        reverse=True,
    )


def test_retention_can_be_changed_and_is_bounded(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    manager = AutomaticBackupManager(storage, retention=4)
    for index in range(4):
        _save(storage, f"s{index}", str(index))
        manager.create_if_changed()

    manager.set_retention(2)

    assert len(list_automatic_backups(manager.directory)) == 2
    assert normalize_backup_retention("invalid") == 20
    assert normalize_backup_retention(0) == 1
    assert normalize_backup_retention(999) == 200
