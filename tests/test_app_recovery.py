from pathlib import Path

from PySide6.QtWidgets import QMessageBox

import quicktype.app as app_module
from quicktype.backup import export_backup
from quicktype.i18n import Translator
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage


def test_startup_recovery_restores_latest_valid_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "QuickTypeData" / "quicktype.sqlite3"
    backup = (
        database.parent
        / "Backups"
        / "QuickType-manual-20260729-120000-000001.json"
    )
    export_backup(
        backup,
        [Snippet(None, "recovered", "Text", TriggerMode.IMMEDIATE)],
    )
    database.write_bytes(b"corrupt")
    notices: list[str] = []
    monkeypatch.setattr(app_module, "database_path", lambda: database)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text: notices.append(text),
    )

    assert app_module._prepare_database(Translator("en"))

    storage = Storage(database)
    storage.initialize()
    assert storage.list_snippets()[0].abbreviation == "recovered"
    assert "Restored 1 snippets" in notices[0]


def test_declined_startup_recovery_preserves_corrupt_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "QuickTypeData" / "quicktype.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"corrupt")
    monkeypatch.setattr(app_module, "database_path", lambda: database)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    assert not app_module._prepare_database(Translator("en"))
    assert database.read_bytes() == b"corrupt"
