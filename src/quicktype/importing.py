from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .backup import export_backup, import_backup
from .models import Snippet
from .storage import Storage


@dataclass(frozen=True, slots=True)
class ImportAnalysis:
    source: Path
    snippets: tuple[Snippet, ...]
    conflicts: tuple[str, ...]

    @property
    def incoming_count(self) -> int:
        return len(self.snippets)

    @property
    def new_count(self) -> int:
        return self.incoming_count - len(self.conflicts)


@dataclass(frozen=True, slots=True)
class ImportResult:
    added: int
    skipped: int
    safety_copy: Path


def analyze_import(storage: Storage, source: Path) -> ImportAnalysis:
    snippets = tuple(import_backup(Path(source)))
    existing = {
        snippet.abbreviation for snippet in storage.list_snippets()
    }
    conflicts = tuple(
        sorted(
            (
                snippet.abbreviation
                for snippet in snippets
                if snippet.abbreviation in existing
            ),
            key=lambda value: (value.casefold(), value),
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
    replace: bool,
) -> ImportResult:
    safety_copy = storage.path.parent / "Backups" / (
        f"QuickType-before-import-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    )
    export_backup(safety_copy, storage.list_snippets())
    added, skipped = storage.import_snippets(
        list(analysis.snippets),
        replace=replace,
    )
    return ImportResult(
        added=added,
        skipped=skipped,
        safety_copy=safety_copy,
    )
