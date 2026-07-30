from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import (
    Snippet,
    SnippetKind,
    TriggerMode,
    normalize_applications,
    normalize_priority,
    normalize_search_terms,
    validate_category,
    validate_description,
    validate_snippet_trigger,
)

SCHEMA_VERSION = 6


class DuplicateAbbreviationError(ValueError):
    pass


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    abbreviation TEXT NOT NULL UNIQUE COLLATE BINARY,
                    expansion TEXT NOT NULL,
                    trigger_mode TEXT NOT NULL CHECK(trigger_mode IN ('immediate', 'delimiter')),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    category TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0, 1)),
                    applications TEXT NOT NULL DEFAULT '[]',
                    kind TEXT NOT NULL DEFAULT 'literal'
                        CHECK(kind IN ('literal', 'regex')),
                    description TEXT NOT NULL DEFAULT '',
                    search_terms TEXT NOT NULL DEFAULT '[]',
                    priority INTEGER NOT NULL DEFAULT 0
                        CHECK(priority BETWEEN -1000 AND 1000)
                );

                CREATE INDEX IF NOT EXISTS idx_snippets_enabled
                    ON snippets(enabled);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(snippets)").fetchall()
            }
            if "usage_count" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_used_at" not in columns:
                connection.execute("ALTER TABLE snippets ADD COLUMN last_used_at TEXT")
            if "category" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN category TEXT NOT NULL DEFAULT ''"
                )
            if "favorite" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
                )
            if "applications" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN applications TEXT NOT NULL DEFAULT '[]'"
                )
            if "kind" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN kind TEXT NOT NULL DEFAULT 'literal'"
                )
            if "description" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN description TEXT NOT NULL DEFAULT ''"
                )
            if "search_terms" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN search_terms TEXT NOT NULL DEFAULT '[]'"
                )
            if "priority" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_snippets_category "
                "ON snippets(category COLLATE NOCASE)"
            )
            connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_snippets(self) -> list[Snippet]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                       usage_count, last_used_at, category, favorite, applications,
                       kind, description, search_terms, priority
                FROM snippets
                ORDER BY abbreviation COLLATE NOCASE, id
                """
            ).fetchall()
        return [self._row_to_snippet(row) for row in rows]

    def check_integrity(self) -> tuple[bool, str]:
        with self._connection() as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
        messages = [str(row[0]) for row in rows]
        if messages == ["ok"]:
            return True, "ok"
        return False, "; ".join(messages) if messages else "No result"

    def get_snippet(self, snippet_id: int) -> Snippet | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                       usage_count, last_used_at, category, favorite, applications,
                       kind, description, search_terms, priority
                FROM snippets WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
        return self._row_to_snippet(row) if row else None

    def list_categories(self) -> list[tuple[str, int]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT category, COUNT(*) AS snippet_count
                FROM snippets
                WHERE category <> ''
                GROUP BY category
                ORDER BY category COLLATE NOCASE, category
                """
            ).fetchall()
        return [
            (str(row["category"]), int(row["snippet_count"]))
            for row in rows
        ]

    def rename_category(self, current: str, replacement: str) -> int:
        source = current.strip()
        target = replacement.strip()
        if not source:
            raise ValueError("The source category cannot be empty.")
        if not target:
            raise ValueError("The replacement category cannot be empty.")
        issues = validate_category(target)
        if issues:
            raise ValueError(issues[0].message)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE snippets
                SET category = ?, updated_at = ?
                WHERE category = ?
                """,
                (target, timestamp, source),
            )
        return cursor.rowcount

    def clear_category(self, category: str) -> int:
        source = category.strip()
        if not source:
            return 0
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE snippets
                SET category = '', updated_at = ?
                WHERE category = ?
                """,
                (timestamp, source),
            )
        return cursor.rowcount

    def save_snippet(self, snippet: Snippet) -> Snippet:
        issues = validate_snippet_trigger(snippet.abbreviation, snippet.kind)
        if issues:
            raise ValueError(issues[0].message)
        category = snippet.category.strip()
        category_issues = validate_category(category)
        if category_issues:
            raise ValueError(category_issues[0].message)
        applications = normalize_applications(snippet.applications)
        description = snippet.description.strip()
        description_issues = validate_description(description)
        if description_issues:
            raise ValueError(description_issues[0].message)
        search_terms = normalize_search_terms(snippet.search_terms)
        priority = normalize_priority(snippet.priority)
        timestamp = datetime.now().isoformat(timespec="seconds")

        try:
            with self._connection() as connection:
                if snippet.id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO snippets(
                            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                            usage_count, last_used_at, category, favorite, applications,
                            kind, description, search_terms, priority
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snippet.abbreviation,
                            snippet.expansion,
                            snippet.trigger_mode.value,
                            int(snippet.enabled),
                            timestamp,
                            timestamp,
                            max(0, snippet.usage_count),
                            snippet.last_used_at.isoformat(timespec="seconds")
                            if snippet.last_used_at
                            else None,
                            category,
                            int(snippet.favorite),
                            json.dumps(applications, ensure_ascii=False),
                            snippet.kind.value,
                            description,
                            json.dumps(search_terms, ensure_ascii=False),
                            priority,
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError(
                            "Inserted snippet did not receive an id"
                        )
                    snippet_id = cursor.lastrowid
                else:
                    cursor = connection.execute(
                        """
                        UPDATE snippets
                        SET abbreviation = ?, expansion = ?, trigger_mode = ?,
                            enabled = ?, updated_at = ?, category = ?, favorite = ?,
                            applications = ?, kind = ?, description = ?,
                            search_terms = ?, priority = ?
                        WHERE id = ?
                        """,
                        (
                            snippet.abbreviation,
                            snippet.expansion,
                            snippet.trigger_mode.value,
                            int(snippet.enabled),
                            timestamp,
                            category,
                            int(snippet.favorite),
                            json.dumps(applications, ensure_ascii=False),
                            snippet.kind.value,
                            description,
                            json.dumps(search_terms, ensure_ascii=False),
                            priority,
                            snippet.id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise KeyError(f"Snippet {snippet.id} does not exist")
                    snippet_id = int(snippet.id)
        except sqlite3.IntegrityError as error:
            if "UNIQUE" in str(error).upper():
                raise DuplicateAbbreviationError(snippet.abbreviation) from error
            raise

        saved = self.get_snippet(snippet_id)
        if saved is None:
            raise RuntimeError("Saved snippet could not be read back")
        return saved

    def delete_snippet(self, snippet_id: int) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))

    def update_snippets(
        self,
        snippet_ids: list[int] | tuple[int, ...],
        *,
        enabled: bool | None = None,
        favorite: bool | None = None,
        category: str | None = None,
    ) -> int:
        identifiers = tuple(dict.fromkeys(snippet_ids))
        if not identifiers:
            return 0
        assignments: list[str] = []
        values: list[object] = []
        if enabled is not None:
            assignments.append("enabled = ?")
            values.append(int(enabled))
        if favorite is not None:
            assignments.append("favorite = ?")
            values.append(int(favorite))
        if category is not None:
            normalized_category = category.strip()
            issues = validate_category(normalized_category)
            if issues:
                raise ValueError(issues[0].message)
            assignments.append("category = ?")
            values.append(normalized_category)
        if not assignments:
            return 0
        assignments.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        placeholders = ", ".join("?" for _ in identifiers)
        values.extend(identifiers)
        with self._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE snippets
                SET {", ".join(assignments)}
                WHERE id IN ({placeholders})
                """,
                tuple(values),
            )
        return cursor.rowcount

    def delete_snippets(
        self,
        snippet_ids: list[int] | tuple[int, ...],
    ) -> int:
        identifiers = tuple(dict.fromkeys(snippet_ids))
        if not identifiers:
            return 0
        placeholders = ", ".join("?" for _ in identifiers)
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM snippets WHERE id IN ({placeholders})",
                identifiers,
            )
        return cursor.rowcount

    def record_expansion(self, snippet_id: int) -> Snippet | None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE snippets
                SET usage_count = usage_count + 1, last_used_at = ?
                WHERE id = ?
                """,
                (timestamp, snippet_id),
            )
        return self.get_snippet(snippet_id)

    def reset_usage(self, snippet_id: int | None = None) -> int:
        with self._connection() as connection:
            if snippet_id is None:
                cursor = connection.execute(
                    """
                    UPDATE snippets
                    SET usage_count = 0, last_used_at = NULL
                    WHERE usage_count <> 0 OR last_used_at IS NOT NULL
                    """
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE snippets
                    SET usage_count = 0, last_used_at = NULL
                    WHERE id = ?
                      AND (usage_count <> 0 OR last_used_at IS NOT NULL)
                    """,
                    (snippet_id,),
                )
        return cursor.rowcount

    def import_snippets(self, snippets: list[Snippet], *, replace: bool) -> tuple[int, int]:
        added, _updated, skipped = self._write_import(
            snippets,
            replace=replace,
            overwrite_conflicts=False,
        )
        return added, skipped

    def update_import_snippets(
        self,
        snippets: list[Snippet],
    ) -> tuple[int, int]:
        added, updated, _skipped = self._write_import(
            snippets,
            replace=False,
            overwrite_conflicts=True,
        )
        return added, updated

    def _write_import(
        self,
        snippets: list[Snippet],
        *,
        replace: bool,
        overwrite_conflicts: bool,
    ) -> tuple[int, int, int]:
        for snippet in snippets:
            issues = validate_snippet_trigger(snippet.abbreviation, snippet.kind)
            if issues:
                raise ValueError(issues[0].message)
            category_issues = validate_category(snippet.category.strip())
            if category_issues:
                raise ValueError(category_issues[0].message)
            normalize_applications(snippet.applications)
            if validate_description(snippet.description.strip()):
                raise ValueError("Invalid snippet description.")
            normalize_search_terms(snippet.search_terms)
            normalize_priority(snippet.priority)

        abbreviations = [snippet.abbreviation for snippet in snippets]
        if len(abbreviations) != len(set(abbreviations)):
            raise DuplicateAbbreviationError("Duplicate abbreviation in backup")

        now = datetime.now().isoformat(timespec="seconds")
        added = 0
        updated = 0
        skipped = 0
        with self._connection() as connection:
            if replace:
                connection.execute("DELETE FROM snippets")
                existing: set[str] = set()
            else:
                existing = {
                    str(row["abbreviation"])
                    for row in connection.execute("SELECT abbreviation FROM snippets").fetchall()
                }

            for snippet in snippets:
                if snippet.abbreviation in existing:
                    if overwrite_conflicts:
                        values = self._import_values(snippet, now)
                        connection.execute(
                            """
                            UPDATE snippets
                            SET expansion = ?, trigger_mode = ?, enabled = ?,
                                created_at = ?, updated_at = ?, usage_count = ?,
                                last_used_at = ?, category = ?, favorite = ?,
                                applications = ?, kind = ?, description = ?,
                                search_terms = ?, priority = ?
                            WHERE abbreviation = ?
                            """,
                            values[1:] + (snippet.abbreviation,),
                        )
                        updated += 1
                    else:
                        skipped += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO snippets(
                        abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                        usage_count, last_used_at, category, favorite, applications,
                        kind, description, search_terms, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._import_values(snippet, now),
                )
                existing.add(snippet.abbreviation)
                added += 1
        return added, updated, skipped

    @staticmethod
    def _import_values(
        snippet: Snippet,
        now: str,
    ) -> tuple[object, ...]:
        created_at = (
            snippet.created_at.isoformat(timespec="seconds")
            if snippet.created_at
            else now
        )
        updated_at = (
            snippet.updated_at.isoformat(timespec="seconds")
            if snippet.updated_at
            else created_at
        )
        return (
            snippet.abbreviation,
            snippet.expansion,
            snippet.trigger_mode.value,
            int(snippet.enabled),
            created_at,
            updated_at,
            max(0, snippet.usage_count),
            snippet.last_used_at.isoformat(timespec="seconds")
            if snippet.last_used_at
            else None,
            snippet.category.strip(),
            int(snippet.favorite),
            json.dumps(
                normalize_applications(snippet.applications),
                ensure_ascii=False,
            ),
            snippet.kind.value,
            snippet.description.strip(),
            json.dumps(
                normalize_search_terms(snippet.search_terms),
                ensure_ascii=False,
            ),
            normalize_priority(snippet.priority),
        )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    @staticmethod
    def _row_to_snippet(row: sqlite3.Row) -> Snippet:
        return Snippet(
            id=int(row["id"]),
            abbreviation=str(row["abbreviation"]),
            expansion=str(row["expansion"]),
            trigger_mode=TriggerMode(str(row["trigger_mode"])),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            usage_count=int(row["usage_count"]),
            last_used_at=(
                datetime.fromisoformat(str(row["last_used_at"]))
                if row["last_used_at"]
                else None
            ),
            category=str(row["category"]),
            favorite=bool(row["favorite"]),
            applications=Storage._decode_applications(str(row["applications"])),
            kind=SnippetKind(str(row["kind"])),
            description=str(row["description"]),
            search_terms=Storage._decode_search_terms(str(row["search_terms"])),
            priority=int(row["priority"]),
        )

    @staticmethod
    def _decode_applications(value: str) -> tuple[str, ...]:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) for item in decoded
        ):
            return ()
        try:
            return normalize_applications(decoded)
        except ValueError:
            return ()

    @staticmethod
    def _decode_search_terms(value: str) -> tuple[str, ...]:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) for item in decoded
        ):
            return ()
        try:
            return normalize_search_terms(decoded)
        except ValueError:
            return ()
