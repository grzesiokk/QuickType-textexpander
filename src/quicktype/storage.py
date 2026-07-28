from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import (
    Snippet,
    TriggerMode,
    normalize_applications,
    validate_abbreviation,
    validate_category,
)

SCHEMA_VERSION = 5


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
                    applications TEXT NOT NULL DEFAULT '[]'
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
                       usage_count, last_used_at, category, favorite, applications
                FROM snippets
                ORDER BY abbreviation COLLATE NOCASE, id
                """
            ).fetchall()
        return [self._row_to_snippet(row) for row in rows]

    def get_snippet(self, snippet_id: int) -> Snippet | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                       usage_count, last_used_at, category, favorite, applications
                FROM snippets WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
        return self._row_to_snippet(row) if row else None

    def save_snippet(self, snippet: Snippet) -> Snippet:
        issues = validate_abbreviation(snippet.abbreviation)
        if issues:
            raise ValueError(issues[0].message)
        category = snippet.category.strip()
        category_issues = validate_category(category)
        if category_issues:
            raise ValueError(category_issues[0].message)
        applications = normalize_applications(snippet.applications)
        timestamp = datetime.now().isoformat(timespec="seconds")

        try:
            with self._connection() as connection:
                if snippet.id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO snippets(
                            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                            usage_count, last_used_at, category, favorite, applications
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        ),
                    )
                    snippet_id = int(cursor.lastrowid)
                else:
                    cursor = connection.execute(
                        """
                        UPDATE snippets
                        SET abbreviation = ?, expansion = ?, trigger_mode = ?,
                            enabled = ?, updated_at = ?, category = ?, favorite = ?,
                            applications = ?
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
                            snippet.id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise KeyError(f"Snippet {snippet.id} does not exist")
                    snippet_id = snippet.id
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

    def import_snippets(self, snippets: list[Snippet], *, replace: bool) -> tuple[int, int]:
        for snippet in snippets:
            issues = validate_abbreviation(snippet.abbreviation)
            if issues:
                raise ValueError(issues[0].message)
            category_issues = validate_category(snippet.category.strip())
            if category_issues:
                raise ValueError(category_issues[0].message)
            normalize_applications(snippet.applications)

        abbreviations = [snippet.abbreviation for snippet in snippets]
        if len(abbreviations) != len(set(abbreviations)):
            raise DuplicateAbbreviationError("Duplicate abbreviation in backup")

        now = datetime.now().isoformat(timespec="seconds")
        added = 0
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
                    skipped += 1
                    continue
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
                connection.execute(
                    """
                    INSERT INTO snippets(
                        abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                        usage_count, last_used_at, category, favorite, applications
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
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
                    ),
                )
                existing.add(snippet.abbreviation)
                added += 1
        return added, skipped

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
