from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .backup import export_backup, import_backup
from .storage import Storage


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
