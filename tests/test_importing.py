from __future__ import annotations

import json
from pathlib import Path

import pytest

from quicktype.backup import BackupFormatError, export_backup, import_backup
from quicktype.importing import ImportMode, analyze_import, apply_import
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage


def _snippet(abbreviation: str, expansion: str) -> Snippet:
    return Snippet(
        id=None,
        abbreviation=abbreviation,
        expansion=expansion,
        trigger_mode=TriggerMode.IMMEDIATE,
    )


def _storage(path: Path) -> Storage:
    storage = Storage(path)
    storage.initialize()
    return storage


def test_analyze_import_reports_new_snippets_and_exact_conflicts(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path / "quicktype.sqlite3")
    storage.save_snippet(_snippet("keep", "Current"))
    storage.save_snippet(_snippet("Case", "Different case remains distinct"))
    source = tmp_path / "incoming.json"
    export_backup(
        source,
        [
            _snippet("new", "New"),
            _snippet("keep", "Incoming conflict"),
            _snippet("case", "New because matching is case-sensitive"),
        ],
    )

    analysis = analyze_import(storage, source)

    assert analysis.source == source
    assert analysis.incoming_count == 3
    assert analysis.new_count == 2
    assert tuple(
        conflict.abbreviation for conflict in analysis.conflicts
    ) == ("keep",)
    assert analysis.conflicts[0].current.expansion == "Current"
    assert analysis.conflicts[0].incoming.expansion == "Incoming conflict"


def test_merge_import_creates_safety_copy_and_skips_conflicts(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.save_snippet(_snippet("keep", "Current"))
    source = tmp_path / "incoming.json"
    export_backup(
        source,
        [_snippet("keep", "Incoming conflict"), _snippet("new", "New")],
    )
    analysis = analyze_import(storage, source)

    result = apply_import(storage, analysis, mode=ImportMode.MERGE)

    assert (result.added, result.updated, result.skipped) == (1, 0, 1)
    assert result.safety_copy.exists()
    assert result.safety_copy.parent.name == "Backups"
    assert result.safety_copy.name.startswith("QuickType-before-import-")
    safety_snippets = import_backup(result.safety_copy)
    assert [(item.abbreviation, item.expansion) for item in safety_snippets] == [
        ("keep", "Current")
    ]
    current = {
        item.abbreviation: item.expansion for item in storage.list_snippets()
    }
    assert current == {"keep": "Current", "new": "New"}


def test_replace_import_creates_safety_copy_of_previous_library(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.save_snippet(_snippet("old", "Previous library"))
    source = tmp_path / "incoming.json"
    export_backup(
        source,
        [_snippet("first", "One"), _snippet("second", "Two")],
    )

    result = apply_import(
        storage,
        analyze_import(storage, source),
        mode=ImportMode.REPLACE,
    )

    assert (result.added, result.updated, result.skipped) == (2, 0, 0)
    assert [item.abbreviation for item in import_backup(result.safety_copy)] == [
        "old"
    ]
    assert {item.abbreviation for item in storage.list_snippets()} == {
        "first",
        "second",
    }


def test_update_import_overwrites_conflicts_and_preserves_other_snippets(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.save_snippet(_snippet("keep", "Current"))
    storage.save_snippet(_snippet("untouched", "Local only"))
    source = tmp_path / "incoming.json"
    export_backup(
        source,
        [_snippet("keep", "Updated"), _snippet("new", "New")],
    )

    result = apply_import(
        storage,
        analyze_import(storage, source),
        mode=ImportMode.UPDATE,
    )

    assert (result.added, result.updated, result.skipped) == (1, 1, 0)
    current = {
        item.abbreviation: item.expansion for item in storage.list_snippets()
    }
    assert current == {
        "keep": "Updated",
        "new": "New",
        "untouched": "Local only",
    }
    safety = {
        item.abbreviation: item.expansion
        for item in import_backup(result.safety_copy)
    }
    assert safety == {"keep": "Current", "untouched": "Local only"}


def test_invalid_import_is_rejected_before_creating_safety_copy(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.save_snippet(_snippet("keep", "Current"))
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({"format": "wrong"}), encoding="utf-8")

    with pytest.raises(BackupFormatError):
        analyze_import(storage, source)

    assert not (storage.path.parent / "Backups").exists()
    assert [item.abbreviation for item in storage.list_snippets()] == ["keep"]
