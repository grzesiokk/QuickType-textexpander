import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from quicktype.models import Snippet, SnippetKind, TriggerMode
from quicktype.storage import DuplicateAbbreviationError, Storage


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    result = Storage(tmp_path / "data" / "quicktype.sqlite3")
    result.initialize()
    return result


def test_database_is_created_and_starts_empty(storage: Storage) -> None:
    assert storage.path.exists()
    assert storage.list_snippets() == []
    assert storage.get_setting("missing", "fallback") == "fallback"
    assert storage.check_integrity() == (True, "ok")


def test_snippet_crud_and_case_sensitive_uniqueness(storage: Storage) -> None:
    first = storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER, True)
    )
    second = storage.save_snippet(
        Snippet(None, "Sig", "Formal regards", TriggerMode.IMMEDIATE, False)
    )
    assert first.id is not None
    assert second.id is not None
    assert len(storage.list_snippets()) == 2

    updated = storage.save_snippet(
        Snippet(first.id, "sig", "Best regards", TriggerMode.IMMEDIATE, True)
    )
    assert updated.expansion == "Best regards"
    assert updated.trigger_mode == TriggerMode.IMMEDIATE

    storage.delete_snippet(second.id)
    assert [entry.abbreviation for entry in storage.list_snippets()] == ["sig"]


def test_duplicate_abbreviation_is_rejected(storage: Storage) -> None:
    storage.save_snippet(Snippet(None, "sig", "One", TriggerMode.DELIMITER))
    with pytest.raises(DuplicateAbbreviationError):
        storage.save_snippet(Snippet(None, "sig", "Two", TriggerMode.IMMEDIATE))


def test_settings_are_upserted(storage: Storage) -> None:
    storage.set_setting("language", "pl")
    storage.set_setting("language", "en")
    assert storage.get_setting("language") == "en"


def test_bulk_updates_and_deletion_are_transactional(storage: Storage) -> None:
    one = storage.save_snippet(
        Snippet(None, "one", "1", TriggerMode.IMMEDIATE)
    )
    two = storage.save_snippet(
        Snippet(None, "two", "2", TriggerMode.IMMEDIATE)
    )
    three = storage.save_snippet(
        Snippet(None, "three", "3", TriggerMode.IMMEDIATE)
    )
    identifiers = [int(one.id), int(two.id)]

    assert storage.update_snippets(
        identifiers,
        enabled=False,
        favorite=True,
        category="Bulk",
    ) == 2
    updated = {
        snippet.abbreviation: snippet for snippet in storage.list_snippets()
    }
    assert not updated["one"].enabled
    assert updated["one"].favorite
    assert updated["one"].category == "Bulk"
    assert updated["three"].enabled
    assert storage.delete_snippets(identifiers) == 2
    assert [snippet.id for snippet in storage.list_snippets()] == [three.id]


def test_usage_is_recorded(storage: Storage) -> None:
    saved = storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    updated = storage.record_expansion(saved.id)
    assert updated is not None
    assert updated.usage_count == 1
    assert updated.last_used_at is not None


def test_usage_can_be_reset_for_one_or_all_snippets(storage: Storage) -> None:
    first = storage.save_snippet(
        Snippet(
            None,
            "first",
            "One",
            TriggerMode.IMMEDIATE,
            usage_count=4,
            last_used_at=datetime(2026, 7, 29, 9, 0),
        )
    )
    second = storage.save_snippet(
        Snippet(
            None,
            "second",
            "Two",
            TriggerMode.IMMEDIATE,
            usage_count=2,
            last_used_at=datetime(2026, 7, 29, 10, 0),
        )
    )

    assert storage.reset_usage(first.id) == 1
    reset_first = storage.get_snippet(int(first.id))
    unchanged_second = storage.get_snippet(int(second.id))
    assert reset_first is not None
    assert reset_first.usage_count == 0
    assert reset_first.last_used_at is None
    assert unchanged_second is not None
    assert unchanged_second.usage_count == 2

    assert storage.reset_usage() == 1
    reset_second = storage.get_snippet(int(second.id))
    assert reset_second is not None
    assert reset_second.usage_count == 0
    assert reset_second.last_used_at is None
    assert storage.reset_usage() == 0


def test_category_is_normalized_and_persisted(storage: Storage) -> None:
    saved = storage.save_snippet(
        Snippet(
            None,
            "mail",
            "hello@example.com",
            TriggerMode.IMMEDIATE,
            category="  Kontakt  ",
            favorite=True,
            applications=("WINWORD.EXE", " Code.exe ", "code.exe"),
        )
    )
    assert saved.category == "Kontakt"
    assert saved.favorite
    assert saved.applications == ("Code.exe", "WINWORD.EXE")
    assert storage.list_snippets()[0].category == "Kontakt"


def test_categories_can_be_listed_renamed_and_cleared(storage: Storage) -> None:
    storage.save_snippet(
        Snippet(None, "one", "1", TriggerMode.IMMEDIATE, category="Work")
    )
    storage.save_snippet(
        Snippet(None, "two", "2", TriggerMode.IMMEDIATE, category="Work")
    )
    storage.save_snippet(
        Snippet(None, "three", "3", TriggerMode.IMMEDIATE, category="Home")
    )
    storage.save_snippet(
        Snippet(None, "none", "4", TriggerMode.IMMEDIATE)
    )

    assert storage.list_categories() == [("Home", 1), ("Work", 2)]
    assert storage.rename_category("Work", "Projects") == 2
    assert storage.list_categories() == [("Home", 1), ("Projects", 2)]
    assert storage.clear_category("Home") == 1
    assert storage.list_categories() == [("Projects", 2)]
    categories = {
        snippet.abbreviation: snippet.category
        for snippet in storage.list_snippets()
    }
    assert categories == {
        "none": "",
        "one": "Projects",
        "three": "",
        "two": "Projects",
    }


def test_category_rename_validates_target(storage: Storage) -> None:
    storage.save_snippet(
        Snippet(None, "one", "1", TriggerMode.IMMEDIATE, category="Work")
    )
    with pytest.raises(ValueError):
        storage.rename_category("Work", "")
    with pytest.raises(ValueError):
        storage.rename_category("Work", "x" * 65)
    assert storage.list_categories() == [("Work", 1)]


def test_import_can_merge_or_replace(storage: Storage) -> None:
    storage.save_snippet(Snippet(None, "keep", "Old", TriggerMode.DELIMITER))
    incoming = [
        Snippet(None, "keep", "Backup", TriggerMode.IMMEDIATE),
        Snippet(
            None,
            "new",
            "New",
            TriggerMode.IMMEDIATE,
            usage_count=3,
            last_used_at=datetime(2026, 7, 28, 12, 0),
            category="Praca",
            favorite=True,
        ),
    ]
    assert storage.import_snippets(incoming, replace=False) == (1, 1)
    assert {item.abbreviation for item in storage.list_snippets()} == {"keep", "new"}

    assert storage.import_snippets(incoming, replace=True) == (2, 0)
    replaced = {item.abbreviation: item for item in storage.list_snippets()}
    assert replaced["keep"].expansion == "Backup"
    assert replaced["new"].usage_count == 3
    assert replaced["new"].category == "Praca"
    assert replaced["new"].favorite


def test_import_can_update_conflicts_without_removing_other_snippets(
    storage: Storage,
) -> None:
    original = storage.save_snippet(
        Snippet(
            None,
            "keep",
            "Old",
            TriggerMode.DELIMITER,
            usage_count=2,
            category="Local",
        )
    )
    storage.save_snippet(
        Snippet(None, "untouched", "Local only", TriggerMode.IMMEDIATE)
    )
    incoming = [
        Snippet(
            None,
            "keep",
            "From backup",
            TriggerMode.IMMEDIATE,
            enabled=False,
            usage_count=7,
            category="Imported",
            favorite=True,
            applications=("Code.exe",),
        ),
        Snippet(None, "new", "New", TriggerMode.DELIMITER),
    ]

    assert storage.update_import_snippets(incoming) == (1, 1)

    current = {
        snippet.abbreviation: snippet for snippet in storage.list_snippets()
    }
    assert set(current) == {"keep", "new", "untouched"}
    assert current["keep"].id == original.id
    assert current["keep"].expansion == "From backup"
    assert current["keep"].trigger_mode == TriggerMode.IMMEDIATE
    assert not current["keep"].enabled
    assert current["keep"].usage_count == 7
    assert current["keep"].category == "Imported"
    assert current["keep"].favorite
    assert current["keep"].applications == ("Code.exe",)
    assert current["untouched"].expansion == "Local only"


def test_v1_database_is_migrated_without_losing_snippets(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abbreviation TEXT NOT NULL UNIQUE COLLATE BINARY,
            expansion TEXT NOT NULL,
            trigger_mode TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO metadata VALUES('schema_version', '1');
        INSERT INTO snippets(
            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at
        ) VALUES('sig', 'Regards', 'delimiter', 1, '2026-01-01T10:00:00', '2026-01-01T10:00:00');
        """
    )
    connection.commit()
    connection.close()

    migrated = Storage(path)
    migrated.initialize()
    snippet = migrated.list_snippets()[0]
    assert snippet.abbreviation == "sig"
    assert snippet.usage_count == 0
    assert snippet.last_used_at is None
    assert snippet.category == ""
    assert not snippet.favorite
    assert snippet.applications == ()
    assert migrated.get_setting("missing") is None

    connection = sqlite3.connect(path)
    schema_version = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()[0]
    connection.close()
    assert schema_version == "6"


def test_advanced_snippet_fields_are_persisted(storage: Storage) -> None:
    saved = storage.save_snippet(
        Snippet(
            None,
            r"order-(?P<number>\d+)",
            "Order {{match:number}}",
            TriggerMode.DELIMITER,
            kind=SnippetKind.REGEX,
            description="Order helper",
            search_terms=("invoice", "order"),
            priority=25,
        )
    )

    assert saved.kind == SnippetKind.REGEX
    assert saved.description == "Order helper"
    assert saved.search_terms == ("invoice", "order")
    assert saved.priority == 25
