from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from quicktype.backup import (
    BackupFormatError,
    export_backup,
    import_backup,
    import_library_state,
)
from quicktype.models import Snippet, SnippetKind, TriggerMode


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
        category="Praca",
        favorite=True,
        applications=("Code.exe", "WINWORD.EXE"),
        kind=SnippetKind.REGEX,
        description="Signature rule",
        search_terms=("email", "formal"),
        priority=10,
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
    assert imported[0].category == "Praca"
    assert imported[0].favorite
    assert imported[0].applications == ("Code.exe", "WINWORD.EXE")
    assert imported[0].kind == SnippetKind.REGEX
    assert imported[0].description == "Signature rule"
    assert imported[0].search_terms == ("email", "formal")
    assert imported[0].priority == 10


def test_old_backup_without_category_remains_compatible(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "format": "quicktype-backup",
                "version": 1,
                "snippets": [
                    {
                        "abbreviation": "sig",
                        "expansion": "Regards",
                        "trigger_mode": "delimiter",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert import_backup(path)[0].category == ""
    assert not import_backup(path)[0].favorite
    assert import_backup(path)[0].applications == ()
    assert import_backup(path)[0].kind == SnippetKind.LITERAL
    assert import_backup(path)[0].description == ""
    assert import_backup(path)[0].search_terms == ()
    assert import_backup(path)[0].priority == 0


def test_backup_is_utf8_and_human_readable(tmp_path: Path) -> None:
    path = tmp_path / "backup.json"
    export_backup(
        path,
        [Snippet(None, "pl", "Zażółć gęślą", TriggerMode.IMMEDIATE)],
    )
    text = path.read_text(encoding="utf-8")
    assert "Zażółć gęślą" in text
    assert json.loads(text)["format"] == "quicktype-backup"


def test_backup_v2_can_include_builtin_library_state(tmp_path: Path) -> None:
    path = tmp_path / "full-backup.json"
    state = {
        "settings": [
            {
                "library_id": "emoji",
                "enabled": True,
                "profile": "full",
                "prefix": ":",
            }
        ],
        "disabled_items": [],
        "usage": [],
    }

    export_backup(path, [], library_state=state)

    assert import_library_state(path) == state


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
        {
            "format": "quicktype-backup",
            "version": 1,
            "snippets": [
                {
                    "abbreviation": "valid",
                    "expansion": "x",
                    "trigger_mode": "delimiter",
                    "enabled": True,
                    "favorite": "yes",
                }
            ],
        },
        {
            "format": "quicktype-backup",
            "version": 1,
            "snippets": [
                {
                    "abbreviation": "valid",
                    "expansion": "x",
                    "trigger_mode": "delimiter",
                    "enabled": True,
                    "applications": [r"C:\bad.exe"],
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
