import hashlib
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from quicktype.models import (
    Snippet,
    SnippetAsset,
    SnippetBundle,
    SnippetContentFormat,
    SnippetKind,
    TriggerMode,
)
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


def test_rich_snippet_bundle_round_trip_and_asset_cascade(storage: Storage) -> None:
    data = b"\x89PNG\r\n\x1a\nrich-image"
    asset_id = str(uuid.uuid4())
    asset = SnippetAsset(
        asset_id=asset_id,
        mime_type="image/png",
        data=data,
        original_name="logo.png",
        width=32,
        height=16,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    snippet = Snippet(
        None,
        ";rich",
        "Hello [Image: logo]",
        TriggerMode.DELIMITER,
        content_format=SnippetContentFormat.RICH,
        rich_html=f'<p><b>Hello</b><img src="quicktype-asset://{asset_id}"></p>',
    )

    saved = storage.save_snippet_bundle(SnippetBundle(snippet, (asset,)))
    assert saved.snippet.content_format == SnippetContentFormat.RICH
    assert saved.assets == (asset,)
    assert storage.list_snippets()[0].rich_html == snippet.rich_html

    storage.delete_snippet(int(saved.snippet.id))
    with sqlite3.connect(storage.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snippet_assets").fetchone()[0] == 0


def test_rich_bundle_rejects_missing_or_unreferenced_assets(storage: Storage) -> None:
    snippet = Snippet(
        None,
        ";rich",
        "Hello",
        TriggerMode.DELIMITER,
        content_format=SnippetContentFormat.RICH,
        rich_html='<p><img src="quicktype-asset://11111111-1111-1111-1111-111111111111"></p>',
    )
    with pytest.raises(ValueError, match="inconsistent"):
        storage.save_snippet_bundle(SnippetBundle(snippet))


def test_rich_asset_update_rolls_back_as_one_transaction(storage: Storage) -> None:
    first_data = b"\x89PNG\r\n\x1a\nfirst"
    shared_id = str(uuid.uuid4())
    first_asset = SnippetAsset(
        shared_id,
        "image/png",
        first_data,
        "first.png",
        2,
        2,
        hashlib.sha256(first_data).hexdigest(),
    )
    storage.save_snippet_bundle(
        SnippetBundle(
            Snippet(
                None,
                "first",
                "[Image: first]",
                TriggerMode.IMMEDIATE,
                content_format=SnippetContentFormat.RICH,
                rich_html=f'<img src="quicktype-asset://{shared_id}">',
            ),
            (first_asset,),
        )
    )
    second_data = b"\x89PNG\r\n\x1a\nsecond"
    second_id = str(uuid.uuid4())
    second_asset = SnippetAsset(
        second_id,
        "image/png",
        second_data,
        "second.png",
        3,
        3,
        hashlib.sha256(second_data).hexdigest(),
    )
    original = storage.save_snippet_bundle(
        SnippetBundle(
            Snippet(
                None,
                "second",
                "[Image: second]",
                TriggerMode.IMMEDIATE,
                content_format=SnippetContentFormat.RICH,
                rich_html=f'<img src="quicktype-asset://{second_id}">',
            ),
            (second_asset,),
        )
    )
    conflicting_asset = SnippetAsset(
        shared_id,
        "image/png",
        second_data,
        "replacement.png",
        3,
        3,
        hashlib.sha256(second_data).hexdigest(),
    )

    with pytest.raises(sqlite3.IntegrityError):
        storage.save_snippet_bundle(
            SnippetBundle(
                replace(
                    original.snippet,
                    expansion="Changed",
                    rich_html=f'<img src="quicktype-asset://{shared_id}">',
                ),
                (conflicting_asset,),
            )
        )

    restored = storage.get_snippet_bundle(int(original.snippet.id))
    assert restored == original


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
    assert schema_version == "8"
    assert snippet.content_format.value == "plain"
    migration_copies = list(
        (path.parent / "Backups").glob(
            "QuickType-before-v3-migration-*.sqlite3"
        )
    )
    assert len(migration_copies) == 1


def test_schema_7_migrates_to_8_as_plain_with_pre_migration_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schema-7.sqlite3"
    with sqlite3.connect(path) as connection:
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
                updated_at TEXT NOT NULL,
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT,
                category TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,
                applications TEXT NOT NULL DEFAULT '[]',
                kind TEXT NOT NULL DEFAULT 'literal',
                description TEXT NOT NULL DEFAULT '',
                search_terms TEXT NOT NULL DEFAULT '[]',
                priority INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO metadata VALUES('schema_version', '7');
            INSERT INTO snippets(
                abbreviation, expansion, trigger_mode, enabled,
                created_at, updated_at
            ) VALUES(
                'legacy', 'Unchanged {{date}}', 'delimiter', 1,
                '2026-01-01T10:00:00', '2026-01-01T10:00:00'
            );
            """
        )

    storage = Storage(path)
    storage.initialize()
    storage.initialize()

    snippet = storage.list_snippets()[0]
    assert snippet.expansion == "Unchanged {{date}}"
    assert snippet.content_format == SnippetContentFormat.PLAIN
    assert snippet.rich_html == ""
    copies = list(
        (path.parent / "Backups").glob(
            "QuickType-before-v3-migration-*.sqlite3"
        )
    )
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as backup:
        assert backup.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone() == ("7",)
        columns = {
            row[1] for row in backup.execute("PRAGMA table_info(snippets)")
        }
    assert "content_format" not in columns


def test_v5_database_migrates_to_advanced_schema_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-v5.sqlite3"
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
            updated_at TEXT NOT NULL,
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            category TEXT NOT NULL DEFAULT '',
            favorite INTEGER NOT NULL DEFAULT 0,
            applications TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO metadata VALUES('schema_version', '5');
        INSERT INTO snippets(
            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
            usage_count, last_used_at, category, favorite, applications
        ) VALUES(
            ';firma', 'Zażółć gęślą', 'delimiter', 1,
            '2026-01-01T10:00:00', '2026-01-02T10:00:00',
            12, '2026-01-03T10:00:00', 'Praca', 1, '["WINWORD.EXE"]'
        );
        """
    )
    connection.commit()
    connection.close()

    storage = Storage(path)
    storage.initialize()
    snippet = storage.list_snippets()[0]

    assert snippet.abbreviation == ";firma"
    assert snippet.expansion == "Zażółć gęślą"
    assert snippet.usage_count == 12
    assert snippet.category == "Praca"
    assert snippet.favorite
    assert snippet.applications == ("WINWORD.EXE",)
    assert snippet.kind == SnippetKind.LITERAL
    assert snippet.description == ""
    assert snippet.search_terms == ()
    assert snippet.priority == 0
    assert storage.get_builtin_library_settings("emoji") is None

    connection = sqlite3.connect(path)
    schema_version = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()[0]
    builtin_table = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'builtin_library_settings'
        """
    ).fetchone()
    connection.close()
    assert schema_version == "8"
    assert builtin_table == ("builtin_library_settings",)


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


def test_builtin_library_state_round_trip(storage: Storage) -> None:
    storage.set_builtin_library_settings(
        "emoji",
        enabled=True,
        profile="full",
        prefix=":",
    )
    storage.set_builtin_item_enabled(
        "emoji",
        "emoji-smile",
        enabled=False,
    )
    storage.record_builtin_expansion("emoji", "emoji-wave")
    state = storage.export_library_state()

    replacement = Storage(storage.path.parent / "replacement.sqlite3")
    replacement.initialize()
    replacement.restore_library_state(state)

    assert replacement.get_builtin_library_settings("emoji") == (
        True,
        "full",
        ":",
    )
    assert replacement.list_disabled_builtin_items("emoji") == {
        "emoji-smile"
    }
    assert replacement.list_builtin_usage()[("emoji", "emoji-wave")][0] == 1
