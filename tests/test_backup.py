from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from quicktype.backup import BackupFormatError, export_backup, import_backup
from quicktype.models import Snippet, TriggerMode


def test_backup_round_trip_preserves_snippet_data(tmp_path: Path) -> None:
    source = Snippet(
        id=7,
        abbreviation=";sig",
        expansion="Regards {{date}}",
        trigger_mode=TriggerMode.DELIMITER,
        enabled=True,
        created_at=datetime(2026, 7, 28, 10, 0),
        updated_at=datetime(2026, 7, 28, 11, 0),
        usage_count=4,
        last_used_at=datetime(2026, 7, 28, 12, 0),
    )
    path = tmp_path / "backup.json"
    export_backup(path, [source])
    imported = import_backup(path)

    assert len(imported) == 1
    assert imported[0].id is None
    assert imported[0].abbreviation == ";sig"
    assert imported[0].trigger_mode == TriggerMode.DELIMITER
    assert imported[0].usage_count == 4
    assert imported[0].last_used_at == datetime(2026, 7, 28, 12, 0)


def test_backup_is_utf8_and_human_readable(tmp_path: Path) -> None:
    path = tmp_path / "backup.json"
    export_backup(
        path,
        [Snippet(None, "pl", "Zażółć gęślą", TriggerMode.IMMEDIATE)],
    )
    text = path.read_text(encoding="utf-8")
    assert "Zażółć gęślą" in text
    assert json.loads(text)["format"] == "quicktype-backup"


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"format": "quicktype-backup", "version": 99, "snippets": []},
        {"format": "quicktype-backup", "version": 1, "snippets": "invalid"},
        {
            "format": "quicktype-backup",
            "version": 1,
            "snippets": [
                {
                    "abbreviation": "bad shortcut",
                    "expansion": "x",
                    "trigger_mode": "delimiter",
                    "enabled": True,
                }
            ],
        },
    ],
)
def test_invalid_backup_is_rejected(tmp_path: Path, document: object) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BackupFormatError):
        import_backup(path)
