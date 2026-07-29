from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .backup import export_backup, import_backup
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
    export_backup(safety_copy, current_snippets)
    added, _skipped = storage.import_snippets(restored_snippets, replace=True)
    return added, safety_copy


RESTORABLE_FIELDS = (
    "expansion",
    "trigger_mode",
    "enabled",
    "usage_count",
    "last_used_at",
    "category",
    "favorite",
    "applications",
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
