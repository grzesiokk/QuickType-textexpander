from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import (
    Snippet,
    SnippetAsset,
    SnippetBundle,
    SnippetContentFormat,
    SnippetKind,
    TriggerMode,
    normalize_applications,
    normalize_priority,
    normalize_search_terms,
    validate_category,
    validate_description,
    validate_snippet_trigger,
)

SCHEMA_VERSION = 8
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_SNIPPET_ASSET_BYTES = 25 * 1024 * 1024
MAX_LIBRARY_ASSET_BYTES = 250 * 1024 * 1024
ASSET_URL_RE = re.compile(r"quicktype-asset://([0-9a-fA-F-]{36})")


class DuplicateAbbreviationError(ValueError):
    pass


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._needs_v3_migration():
            self._create_v3_migration_backup()
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
                        CHECK(priority BETWEEN -1000 AND 1000),
                    content_format TEXT NOT NULL DEFAULT 'plain'
                        CHECK(content_format IN ('plain', 'rich')),
                    rich_html TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_snippets_enabled
                    ON snippets(enabled);

                CREATE TABLE IF NOT EXISTS builtin_library_settings (
                    library_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                    profile TEXT NOT NULL DEFAULT '',
                    prefix TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS builtin_item_overrides (
                    library_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 1 CHECK(disabled IN (0, 1)),
                    PRIMARY KEY(library_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS builtin_usage (
                    library_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    PRIMARY KEY(library_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS snippet_assets (
                    asset_id TEXT PRIMARY KEY,
                    snippet_id INTEGER NOT NULL
                        REFERENCES snippets(id) ON DELETE CASCADE,
                    mime_type TEXT NOT NULL
                        CHECK(mime_type IN ('image/png', 'image/jpeg')),
                    data BLOB NOT NULL,
                    original_name TEXT NOT NULL DEFAULT '',
                    width INTEGER NOT NULL CHECK(width > 0),
                    height INTEGER NOT NULL CHECK(height > 0),
                    sha256 TEXT NOT NULL,
                    UNIQUE(snippet_id, sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_snippet_assets_snippet
                    ON snippet_assets(snippet_id);
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
            if "content_format" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN content_format "
                    "TEXT NOT NULL DEFAULT 'plain'"
                )
            if "rich_html" not in columns:
                connection.execute(
                    "ALTER TABLE snippets ADD COLUMN rich_html TEXT NOT NULL DEFAULT ''"
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

    def _needs_v3_migration(self) -> bool:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "snippets" not in tables:
                return False
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(snippets)").fetchall()
            }
        except sqlite3.DatabaseError:
            return False
        finally:
            if connection is not None:
                connection.close()
        return "content_format" not in columns or "rich_html" not in columns

    def _create_v3_migration_backup(self) -> None:
        backup_directory = self.path.parent / "Backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        destination = backup_directory / (
            f"QuickType-before-v3-migration-"
            f"{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
        )
        source = sqlite3.connect(self.path, timeout=5)
        target = sqlite3.connect(destination, timeout=5)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

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
                       kind, description, search_terms, priority, content_format, rich_html
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
                       kind, description, search_terms, priority, content_format, rich_html
                FROM snippets WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
        return self._row_to_snippet(row) if row else None

    def get_snippet_bundle(self, snippet_id: int) -> SnippetBundle | None:
        snippet = self.get_snippet(snippet_id)
        if snippet is None:
            return None
        with self._connection() as connection:
            assets = self._assets_for_snippet(connection, snippet_id)
        return SnippetBundle(snippet=snippet, assets=assets)

    def list_snippet_bundles(self) -> list[SnippetBundle]:
        snippets = self.list_snippets()
        if not snippets:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT asset_id, snippet_id, mime_type, data, original_name,
                       width, height, sha256
                FROM snippet_assets
                ORDER BY snippet_id, asset_id
                """
            ).fetchall()
        assets_by_snippet: dict[int, list[SnippetAsset]] = {}
        for row in rows:
            assets_by_snippet.setdefault(int(row["snippet_id"]), []).append(
                self._row_to_asset(row)
            )
        return [
            SnippetBundle(
                snippet=snippet,
                assets=tuple(assets_by_snippet.get(int(snippet.id or 0), ())),
            )
            for snippet in snippets
        ]

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
        existing_assets: tuple[SnippetAsset, ...] = ()
        if snippet.id is not None and snippet.content_format == SnippetContentFormat.RICH:
            current = self.get_snippet_bundle(snippet.id)
            if current is not None:
                existing_assets = current.assets
        return self.save_snippet_bundle(
            SnippetBundle(snippet=snippet, assets=existing_assets)
        ).snippet

    def save_snippet_bundle(self, bundle: SnippetBundle) -> SnippetBundle:
        snippet = bundle.snippet
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
        assets = self._validated_assets(snippet, bundle.assets)
        timestamp = datetime.now().isoformat(timespec="seconds")

        try:
            with self._connection() as connection:
                self._validate_library_asset_limit(
                    connection,
                    assets,
                    excluding_snippet_id=snippet.id,
                )
                if snippet.id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO snippets(
                            abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                            usage_count, last_used_at, category, favorite, applications,
                            kind, description, search_terms, priority, content_format, rich_html
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            snippet.content_format.value,
                            snippet.rich_html,
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
                            search_terms = ?, priority = ?, content_format = ?,
                            rich_html = ?
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
                            snippet.content_format.value,
                            snippet.rich_html,
                            snippet.id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise KeyError(f"Snippet {snippet.id} does not exist")
                    snippet_id = int(snippet.id)
                self._replace_assets(connection, int(snippet_id), assets)
        except sqlite3.IntegrityError as error:
            if "snippets.abbreviation" in str(error):
                raise DuplicateAbbreviationError(snippet.abbreviation) from error
            raise

        saved = self.get_snippet_bundle(int(snippet_id))
        if saved is None:
            raise RuntimeError("Saved snippet could not be read back")
        return saved

    @staticmethod
    def _validated_assets(
        snippet: Snippet,
        assets: tuple[SnippetAsset, ...],
    ) -> tuple[SnippetAsset, ...]:
        if snippet.content_format == SnippetContentFormat.PLAIN:
            if snippet.rich_html or assets:
                raise ValueError("Plain snippets cannot contain rich content or images.")
            return ()
        identifiers: set[str] = set()
        hashes: set[str] = set()
        total_bytes = 0
        for asset in assets:
            try:
                identifier = str(uuid.UUID(asset.asset_id))
            except (ValueError, AttributeError) as error:
                raise ValueError("Image asset identifier is invalid.") from error
            if identifier != asset.asset_id:
                raise ValueError("Image asset identifier must use canonical UUID form.")
            if identifier in identifiers:
                raise ValueError("Image asset identifiers must be unique.")
            identifiers.add(identifier)
            if asset.mime_type not in {"image/png", "image/jpeg"}:
                raise ValueError("Images must be stored as PNG or JPEG.")
            if not asset.data or len(asset.data) > MAX_ASSET_BYTES:
                raise ValueError("An image exceeds the 10 MiB limit.")
            digest = hashlib.sha256(asset.data).hexdigest()
            if asset.sha256 != digest:
                raise ValueError("Image checksum does not match its data.")
            if digest in hashes:
                raise ValueError("Duplicate images must reuse the same asset.")
            hashes.add(digest)
            if not (0 < asset.width <= 16384 and 0 < asset.height <= 16384):
                raise ValueError("Image dimensions are invalid.")
            if (
                len(asset.original_name) > 255
                or Path(asset.original_name).name != asset.original_name
                or any(ord(character) < 32 for character in asset.original_name)
            ):
                raise ValueError("Image file name is invalid.")
            total_bytes += len(asset.data)
        if total_bytes > MAX_SNIPPET_ASSET_BYTES:
            raise ValueError("A snippet cannot contain more than 25 MiB of images.")
        referenced = set(ASSET_URL_RE.findall(snippet.rich_html))
        if referenced != identifiers:
            raise ValueError("Rich HTML and embedded image assets are inconsistent.")
        return tuple(assets)

    @staticmethod
    def _validate_library_asset_limit(
        connection: sqlite3.Connection,
        assets: tuple[SnippetAsset, ...],
        *,
        excluding_snippet_id: int | None,
    ) -> None:
        if excluding_snippet_id is None:
            row = connection.execute(
                "SELECT COALESCE(SUM(length(data)), 0) FROM snippet_assets"
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(length(data)), 0)
                FROM snippet_assets
                WHERE snippet_id <> ?
                """,
                (excluding_snippet_id,),
            ).fetchone()
        existing_bytes = int(row[0]) if row else 0
        if existing_bytes + sum(len(asset.data) for asset in assets) > MAX_LIBRARY_ASSET_BYTES:
            raise ValueError("The image library cannot exceed 250 MiB.")

    @staticmethod
    def _replace_assets(
        connection: sqlite3.Connection,
        snippet_id: int,
        assets: tuple[SnippetAsset, ...],
    ) -> None:
        connection.execute(
            "DELETE FROM snippet_assets WHERE snippet_id = ?",
            (snippet_id,),
        )
        connection.executemany(
            """
            INSERT INTO snippet_assets(
                asset_id, snippet_id, mime_type, data, original_name,
                width, height, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    asset.asset_id,
                    snippet_id,
                    asset.mime_type,
                    asset.data,
                    asset.original_name,
                    asset.width,
                    asset.height,
                    asset.sha256,
                )
                for asset in assets
            ),
        )

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
        return self.import_snippet_bundles(
            [SnippetBundle(snippet) for snippet in snippets],
            replace=replace,
        )

    def import_snippet_bundles(
        self,
        bundles: list[SnippetBundle],
        *,
        replace: bool,
    ) -> tuple[int, int]:
        added, _updated, skipped = self._write_import(
            bundles,
            replace=replace,
            overwrite_conflicts=False,
        )
        return added, skipped

    def update_import_snippets(
        self,
        snippets: list[Snippet],
    ) -> tuple[int, int]:
        return self.update_import_bundles(
            [SnippetBundle(snippet) for snippet in snippets]
        )

    def update_import_bundles(
        self,
        bundles: list[SnippetBundle],
    ) -> tuple[int, int]:
        added, updated, _skipped = self._write_import(
            bundles,
            replace=False,
            overwrite_conflicts=True,
        )
        return added, updated

    def _write_import(
        self,
        bundles: list[SnippetBundle],
        *,
        replace: bool,
        overwrite_conflicts: bool,
    ) -> tuple[int, int, int]:
        for bundle in bundles:
            snippet = bundle.snippet
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
            self._validated_assets(snippet, bundle.assets)

        abbreviations = [bundle.snippet.abbreviation for bundle in bundles]
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

            for bundle in bundles:
                snippet = bundle.snippet
                assets = self._validated_assets(snippet, bundle.assets)
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
                                search_terms = ?, priority = ?, content_format = ?,
                                rich_html = ?
                            WHERE abbreviation = ?
                            """,
                            values[1:] + (snippet.abbreviation,),
                        )
                        row = connection.execute(
                            "SELECT id FROM snippets WHERE abbreviation = ?",
                            (snippet.abbreviation,),
                        ).fetchone()
                        if row is None:
                            raise RuntimeError("Updated snippet could not be found.")
                        self._replace_assets(connection, int(row["id"]), assets)
                        updated += 1
                    else:
                        skipped += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO snippets(
                        abbreviation, expansion, trigger_mode, enabled, created_at, updated_at,
                        usage_count, last_used_at, category, favorite, applications,
                        kind, description, search_terms, priority, content_format, rich_html
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._import_values(snippet, now),
                )
                row = connection.execute(
                    "SELECT id FROM snippets WHERE abbreviation = ?",
                    (snippet.abbreviation,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Imported snippet could not be found.")
                self._replace_assets(connection, int(row["id"]), assets)
                existing.add(snippet.abbreviation)
                added += 1
            row = connection.execute(
                "SELECT COALESCE(SUM(length(data)), 0) FROM snippet_assets"
            ).fetchone()
            if row and int(row[0]) > MAX_LIBRARY_ASSET_BYTES:
                raise ValueError("The image library cannot exceed 250 MiB.")
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
            snippet.content_format.value,
            snippet.rich_html,
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

    def get_builtin_library_settings(
        self,
        library_id: str,
    ) -> tuple[bool, str, str] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT enabled, profile, prefix
                FROM builtin_library_settings
                WHERE library_id = ?
                """,
                (library_id,),
            ).fetchone()
        if row is None:
            return None
        return bool(row["enabled"]), str(row["profile"]), str(row["prefix"])

    def set_builtin_library_settings(
        self,
        library_id: str,
        *,
        enabled: bool,
        profile: str,
        prefix: str,
    ) -> None:
        _validate_builtin_identifier(library_id, "library")
        if len(profile) > 40 or any(character.isspace() for character in profile):
            raise ValueError("Invalid built-in library profile.")
        if (
            len(prefix) > 32
            or any(character.isspace() for character in prefix)
            or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
        ):
            raise ValueError("Library prefix must have at most 32 non-whitespace characters.")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO builtin_library_settings(
                    library_id, enabled, profile, prefix, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(library_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    profile = excluded.profile,
                    prefix = excluded.prefix,
                    updated_at = excluded.updated_at
                """,
                (
                    library_id,
                    int(enabled),
                    profile,
                    prefix,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def list_disabled_builtin_items(self, library_id: str) -> set[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT item_id
                FROM builtin_item_overrides
                WHERE library_id = ? AND disabled = 1
                """,
                (library_id,),
            ).fetchall()
        return {str(row["item_id"]) for row in rows}

    def set_builtin_item_enabled(
        self,
        library_id: str,
        item_id: str,
        *,
        enabled: bool,
    ) -> None:
        _validate_builtin_identifier(library_id, "library")
        _validate_builtin_identifier(item_id, "item")
        with self._connection() as connection:
            if enabled:
                connection.execute(
                    """
                    DELETE FROM builtin_item_overrides
                    WHERE library_id = ? AND item_id = ?
                    """,
                    (library_id, item_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO builtin_item_overrides(library_id, item_id, disabled)
                    VALUES (?, ?, 1)
                    ON CONFLICT(library_id, item_id) DO UPDATE SET disabled = 1
                    """,
                    (library_id, item_id),
                )

    def record_builtin_expansion(
        self,
        library_id: str,
        item_id: str,
    ) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO builtin_usage(
                    library_id, item_id, usage_count, last_used_at
                ) VALUES (?, ?, 1, ?)
                ON CONFLICT(library_id, item_id) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used_at = excluded.last_used_at
                """,
                (library_id, item_id, timestamp),
            )

    def list_builtin_usage(
        self,
    ) -> dict[tuple[str, str], tuple[int, datetime | None]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT library_id, item_id, usage_count, last_used_at
                FROM builtin_usage
                """
            ).fetchall()
        return {
            (str(row["library_id"]), str(row["item_id"])): (
                int(row["usage_count"]),
                datetime.fromisoformat(str(row["last_used_at"]))
                if row["last_used_at"]
                else None,
            )
            for row in rows
        }

    def export_library_state(self) -> dict[str, object]:
        with self._connection() as connection:
            settings = [
                {
                    "library_id": str(row["library_id"]),
                    "enabled": bool(row["enabled"]),
                    "profile": str(row["profile"]),
                    "prefix": str(row["prefix"]),
                }
                for row in connection.execute(
                    """
                    SELECT library_id, enabled, profile, prefix
                    FROM builtin_library_settings
                    ORDER BY library_id
                    """
                ).fetchall()
            ]
            disabled_items = [
                {
                    "library_id": str(row["library_id"]),
                    "item_id": str(row["item_id"]),
                }
                for row in connection.execute(
                    """
                    SELECT library_id, item_id
                    FROM builtin_item_overrides
                    WHERE disabled = 1
                    ORDER BY library_id, item_id
                    """
                ).fetchall()
            ]
            usage = [
                {
                    "library_id": str(row["library_id"]),
                    "item_id": str(row["item_id"]),
                    "usage_count": int(row["usage_count"]),
                    "last_used_at": str(row["last_used_at"])
                    if row["last_used_at"]
                    else None,
                }
                for row in connection.execute(
                    """
                    SELECT library_id, item_id, usage_count, last_used_at
                    FROM builtin_usage
                    ORDER BY library_id, item_id
                    """
                ).fetchall()
            ]
        return {
            "settings": settings,
            "disabled_items": disabled_items,
            "usage": usage,
        }

    def restore_library_state(self, state: dict[str, object]) -> None:
        settings = state.get("settings", [])
        disabled_items = state.get("disabled_items", [])
        usage = state.get("usage", [])
        if (
            not isinstance(settings, list)
            or not isinstance(disabled_items, list)
            or not isinstance(usage, list)
        ):
            raise ValueError("Invalid built-in library state.")
        with self._connection() as connection:
            connection.execute("DELETE FROM builtin_library_settings")
            connection.execute("DELETE FROM builtin_item_overrides")
            connection.execute("DELETE FROM builtin_usage")
            for raw in settings:
                if not isinstance(raw, dict):
                    raise ValueError("Invalid built-in library settings.")
                library_id = str(raw.get("library_id", ""))
                profile = str(raw.get("profile", ""))
                prefix = str(raw.get("prefix", ""))
                enabled = raw.get("enabled")
                _validate_builtin_identifier(library_id, "library")
                if not isinstance(enabled, bool):
                    raise ValueError("Invalid built-in library enabled state.")
                connection.execute(
                    """
                    INSERT INTO builtin_library_settings(
                        library_id, enabled, profile, prefix, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        library_id,
                        int(enabled),
                        profile,
                        prefix,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
            for raw in disabled_items:
                if not isinstance(raw, dict):
                    raise ValueError("Invalid built-in item override.")
                library_id = str(raw.get("library_id", ""))
                item_id = str(raw.get("item_id", ""))
                _validate_builtin_identifier(library_id, "library")
                _validate_builtin_identifier(item_id, "item")
                connection.execute(
                    """
                    INSERT INTO builtin_item_overrides(library_id, item_id, disabled)
                    VALUES (?, ?, 1)
                    """,
                    (library_id, item_id),
                )
            for raw in usage:
                if not isinstance(raw, dict):
                    raise ValueError("Invalid built-in usage entry.")
                library_id = str(raw.get("library_id", ""))
                item_id = str(raw.get("item_id", ""))
                usage_count = raw.get("usage_count")
                last_used_at = raw.get("last_used_at")
                _validate_builtin_identifier(library_id, "library")
                _validate_builtin_identifier(item_id, "item")
                if (
                    not isinstance(usage_count, int)
                    or isinstance(usage_count, bool)
                    or usage_count < 0
                ):
                    raise ValueError("Invalid built-in usage count.")
                if last_used_at is not None:
                    if not isinstance(last_used_at, str):
                        raise ValueError("Invalid built-in usage timestamp.")
                    datetime.fromisoformat(last_used_at)
                connection.execute(
                    """
                    INSERT INTO builtin_usage(
                        library_id, item_id, usage_count, last_used_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (library_id, item_id, usage_count, last_used_at),
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
            content_format=SnippetContentFormat(str(row["content_format"])),
            rich_html=str(row["rich_html"]),
        )

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> SnippetAsset:
        return SnippetAsset(
            asset_id=str(row["asset_id"]),
            mime_type=str(row["mime_type"]),
            data=bytes(row["data"]),
            original_name=str(row["original_name"]),
            width=int(row["width"]),
            height=int(row["height"]),
            sha256=str(row["sha256"]),
        )

    @classmethod
    def _assets_for_snippet(
        cls,
        connection: sqlite3.Connection,
        snippet_id: int,
    ) -> tuple[SnippetAsset, ...]:
        rows = connection.execute(
            """
            SELECT asset_id, mime_type, data, original_name, width, height, sha256
            FROM snippet_assets
            WHERE snippet_id = ?
            ORDER BY asset_id
            """,
            (snippet_id,),
        ).fetchall()
        return tuple(cls._row_to_asset(row) for row in rows)

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


def _validate_builtin_identifier(value: str, label: str) -> None:
    if (
        not value
        or len(value) > 160
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError(f"Invalid built-in {label} identifier.")
