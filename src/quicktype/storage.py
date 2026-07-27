from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import Snippet, TriggerMode, validate_abbreviation

SCHEMA_VERSION = 1


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
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_snippets_enabled
                    ON snippets(enabled);
                """
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
                SELECT id, abbreviation, expansion, trigger_mode, enabled, created_at, updated_at
                FROM snippets
                ORDER BY abbreviation COLLATE NOCASE, id
                """
            ).fetchall()
        return [self._row_to_snippet(row) for row in rows]

    def get_snippet(self, snippet_id: int) -> Snippet | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, abbreviation, expansion, trigger_mode, enabled, created_at, updated_at
                FROM snippets WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
        return self._row_to_snippet(row) if row else None

    def save_snippet(self, snippet: Snippet) -> Snippet:
        issues = validate_abbreviation(snippet.abbreviation)
        if issues:
            raise ValueError(issues[0].message)
        timestamp = datetime.now().isoformat(timespec="seconds")

        try:
            with self._connection() as connection:
                if snippet.id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO snippets(
                            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snippet.abbreviation,
                            snippet.expansion,
                            snippet.trigger_mode.value,
                            int(snippet.enabled),
                            timestamp,
                            timestamp,
                        ),
                    )
                    snippet_id = int(cursor.lastrowid)
                else:
                    cursor = connection.execute(
                        """
                        UPDATE snippets
                        SET abbreviation = ?, expansion = ?, trigger_mode = ?,
                            enabled = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            snippet.abbreviation,
                            snippet.expansion,
                            snippet.trigger_mode.value,
                            int(snippet.enabled),
                            timestamp,
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
        )
