from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .backup import export_backup, import_backup_bundles, import_library_state
from .backup_catalog import list_backup_entries
from .models import Snippet, SnippetBundle
from .storage import Storage


class RestoreChangeKind(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class RestoreChange:
    abbreviation: str
    kind: RestoreChangeKind
    current: Snippet | None
    incoming: Snippet | None
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoreAnalysis:
    source: Path
    incoming_count: int
    added: int
    updated: int
    removed: int
    unchanged: int
    changes: tuple[RestoreChange, ...]


@dataclass(frozen=True, slots=True)
class DatabaseRecoveryResult:
    restored_count: int
    source: Path | None
    quarantined_database: Path | None


def analyze_restore(storage: Storage, source: Path) -> RestoreAnalysis:
    path = Path(source)
    incoming = import_backup_bundles(path)
    current = storage.list_snippet_bundles()
    incoming_by_abbreviation = {
        bundle.snippet.abbreviation: bundle for bundle in incoming
    }
    current_by_abbreviation = {
        bundle.snippet.abbreviation: bundle for bundle in current
    }
    incoming_names = set(incoming_by_abbreviation)
    current_names = set(current_by_abbreviation)
    changes: list[RestoreChange] = []
    for name in sorted(incoming_names | current_names, key=str.casefold):
        current_bundle = current_by_abbreviation.get(name)
        incoming_bundle = incoming_by_abbreviation.get(name)
        current_snippet = current_bundle.snippet if current_bundle else None
        incoming_snippet = incoming_bundle.snippet if incoming_bundle else None
        if current_bundle is None:
            kind = RestoreChangeKind.ADDED
            changed_fields: tuple[str, ...] = ()
        elif incoming_bundle is None:
            kind = RestoreChangeKind.REMOVED
            changed_fields = ()
        else:
            changed_fields = _changed_fields(
                current_bundle,
                incoming_bundle,
            )
            kind = (
                RestoreChangeKind.CHANGED
                if changed_fields
                else RestoreChangeKind.UNCHANGED
            )
        changes.append(
            RestoreChange(
                abbreviation=name,
                kind=kind,
                current=current_snippet,
                incoming=incoming_snippet,
                changed_fields=changed_fields,
            )
        )
    counts = {
        kind: sum(change.kind == kind for change in changes)
        for kind in RestoreChangeKind
    }
    return RestoreAnalysis(
        source=path,
        incoming_count=len(incoming),
        added=counts[RestoreChangeKind.ADDED],
        updated=counts[RestoreChangeKind.CHANGED],
        removed=counts[RestoreChangeKind.REMOVED],
        unchanged=counts[RestoreChangeKind.UNCHANGED],
        changes=tuple(changes),
    )


def restore_backup(storage: Storage, source: Path) -> tuple[int, Path]:
    restored_bundles = import_backup_bundles(Path(source))
    current_bundles = storage.list_snippet_bundles()
    backup_directory = storage.path.parent / "Backups"
    safety_copy = backup_directory / (
        f"QuickType-before-restore-{datetime.now():%Y%m%d-%H%M%S-%f}.qtbackup"
    )
    export_backup(
        safety_copy,
        current_bundles,
        library_state=storage.export_library_state(),
    )
    added, _skipped = storage.import_snippet_bundles(restored_bundles, replace=True)
    library_state = import_library_state(Path(source))
    if library_state is not None:
        storage.restore_library_state(library_state)
    return added, safety_copy


def latest_recovery_backup(database: Path) -> Path | None:
    entries = list_backup_entries(Path(database).parent / "Backups")
    return entries[0].path if entries else None


def recover_database(
    database: Path,
    source: Path | None,
) -> DatabaseRecoveryResult:
    path = Path(database)
    bundles = import_backup_bundles(source) if source is not None else []
    library_state = import_library_state(source) if source is not None else None
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    quarantine = path.with_name(
        f"{path.stem}-corrupt-{timestamp}{path.suffix}"
    )
    moved: list[tuple[Path, Path]] = []
    for suffix in ("", "-wal", "-shm"):
        original = Path(f"{path}{suffix}")
        if not original.exists():
            continue
        preserved = Path(f"{quarantine}{suffix}")
        original.replace(preserved)
        moved.append((original, preserved))
    try:
        storage = Storage(path)
        storage.initialize()
        if bundles:
            storage.import_snippet_bundles(bundles, replace=True)
        if library_state is not None:
            storage.restore_library_state(library_state)
    except Exception:
        for suffix in ("", "-wal", "-shm"):
            created = Path(f"{path}{suffix}")
            if created.exists():
                created.unlink()
        for original, preserved in moved:
            preserved.replace(original)
        raise
    return DatabaseRecoveryResult(
        restored_count=len(bundles),
        source=Path(source) if source is not None else None,
        quarantined_database=quarantine if moved else None,
    )


RESTORABLE_FIELDS = (
    "expansion",
    "trigger_mode",
    "enabled",
    "usage_count",
    "last_used_at",
    "category",
    "favorite",
    "applications",
    "kind",
    "description",
    "search_terms",
    "priority",
    "content_format",
    "rich_html",
    "assets",
)


def _changed_fields(
    current: SnippetBundle,
    incoming: SnippetBundle,
) -> tuple[str, ...]:
    fields = [
        field
        for field in RESTORABLE_FIELDS
        if field != "assets"
        and getattr(current.snippet, field) != getattr(incoming.snippet, field)
    ]
    current_assets = tuple(
        (asset.asset_id, asset.sha256, asset.original_name, asset.width, asset.height)
        for asset in current.assets
    )
    incoming_assets = tuple(
        (asset.asset_id, asset.sha256, asset.original_name, asset.width, asset.height)
        for asset in incoming.assets
    )
    if current_assets != incoming_assets:
        fields.append("assets")
    return tuple(fields)
