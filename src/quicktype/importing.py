from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .backup import export_backup, import_backup
from .models import Snippet
from .storage import Storage


class ImportMode(StrEnum):
    MERGE = "merge"
    UPDATE = "update"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class ImportConflict:
    current: Snippet
    incoming: Snippet

    @property
    def abbreviation(self) -> str:
        return self.incoming.abbreviation


@dataclass(frozen=True, slots=True)
class ImportAnalysis:
    source: Path
    snippets: tuple[Snippet, ...]
    conflicts: tuple[ImportConflict, ...]

    @property
    def incoming_count(self) -> int:
        return len(self.snippets)

    @property
    def new_count(self) -> int:
        return self.incoming_count - len(self.conflicts)


@dataclass(frozen=True, slots=True)
class ImportResult:
    added: int
    updated: int
    skipped: int
    safety_copy: Path


def analyze_import(storage: Storage, source: Path) -> ImportAnalysis:
    snippets = tuple(import_backup(Path(source)))
    existing = {
        snippet.abbreviation: snippet
        for snippet in storage.list_snippets()
    }
    conflicts = tuple(
        sorted(
            (
                ImportConflict(
                    current=existing[snippet.abbreviation],
                    incoming=snippet,
                )
                for snippet in snippets
                if snippet.abbreviation in existing
            ),
            key=lambda conflict: (
                conflict.abbreviation.casefold(),
                conflict.abbreviation,
            ),
        )
    )
    return ImportAnalysis(
        source=Path(source),
        snippets=snippets,
        conflicts=conflicts,
    )


def apply_import(
    storage: Storage,
    analysis: ImportAnalysis,
    *,
    mode: ImportMode,
) -> ImportResult:
    safety_copy = storage.path.parent / "Backups" / (
        f"QuickType-before-import-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    )
    export_backup(
        safety_copy,
        storage.list_snippets(),
        library_state=storage.export_library_state(),
    )
    if mode == ImportMode.UPDATE:
        added, updated = storage.update_import_snippets(
            list(analysis.snippets)
        )
        skipped = 0
    else:
        added, skipped = storage.import_snippets(
            list(analysis.snippets),
            replace=mode == ImportMode.REPLACE,
        )
        updated = 0
    return ImportResult(
        added=added,
        updated=updated,
        skipped=skipped,
        safety_copy=safety_copy,
    )
