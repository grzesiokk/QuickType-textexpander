from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .backup import export_backup, import_backup, import_library_state
from .backup_catalog import list_backup_entries
from .models import Snippet
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
    incoming = import_backup(path)
    current = storage.list_snippets()
    incoming_by_abbreviation = {
        snippet.abbreviation: snippet for snippet in incoming
    }
    current_by_abbreviation = {
        snippet.abbreviation: snippet for snippet in current
    }
    incoming_names = set(incoming_by_abbreviation)
    current_names = set(current_by_abbreviation)
    changes: list[RestoreChange] = []
    for name in sorted(incoming_names | current_names, key=str.casefold):
        current_snippet = current_by_abbreviation.get(name)
        incoming_snippet = incoming_by_abbreviation.get(name)
        if current_snippet is None:
            kind = RestoreChangeKind.ADDED
            changed_fields: tuple[str, ...] = ()
        elif incoming_snippet is None:
            kind = RestoreChangeKind.REMOVED
            changed_fields = ()
        else:
            changed_fields = _changed_fields(
                current_snippet,
                incoming_snippet,
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
    restored_snippets = import_backup(Path(source))
    current_snippets = storage.list_snippets()
    backup_directory = storage.path.parent / "Backups"
    safety_copy = backup_directory / (
        f"QuickType-before-restore-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    )
    export_backup(
        safety_copy,
        current_snippets,
        library_state=storage.export_library_state(),
    )
    added, _skipped = storage.import_snippets(restored_snippets, replace=True)
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
    snippets = import_backup(source) if source is not None else []
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
        if snippets:
            storage.import_snippets(snippets, replace=True)
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
        restored_count=len(snippets),
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
)


def _changed_fields(
    current: Snippet,
    incoming: Snippet,
) -> tuple[str, ...]:
    return tuple(
        field
        for field in RESTORABLE_FIELDS
        if getattr(current, field) != getattr(incoming, field)
    )
