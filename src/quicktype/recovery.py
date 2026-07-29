from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .backup import export_backup, import_backup
from .models import Snippet
from .storage import Storage


@dataclass(frozen=True, slots=True)
class RestoreAnalysis:
    source: Path
    incoming_count: int
    added: int
    updated: int
    removed: int
    unchanged: int


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
    shared_names = incoming_names & current_names
    unchanged = sum(
        _restorable_state(incoming_by_abbreviation[name])
        == _restorable_state(current_by_abbreviation[name])
        for name in shared_names
    )
    return RestoreAnalysis(
        source=path,
        incoming_count=len(incoming),
        added=len(incoming_names - current_names),
        updated=len(shared_names) - unchanged,
        removed=len(current_names - incoming_names),
        unchanged=unchanged,
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


def _restorable_state(snippet: Snippet) -> tuple[object, ...]:
    return (
        snippet.expansion,
        snippet.trigger_mode,
        snippet.enabled,
        snippet.usage_count,
        snippet.last_used_at,
        snippet.category,
        snippet.favorite,
        snippet.applications,
    )
