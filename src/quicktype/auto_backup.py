from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .backup import export_backup
from .models import Snippet
from .storage import Storage

AUTO_BACKUP_PATTERN = re.compile(
    r"QuickType-auto-\d{8}-\d{6}-\d{6}\.json"
)
DEFAULT_BACKUP_RETENTION = 20
MIN_BACKUP_RETENTION = 1
MAX_BACKUP_RETENTION = 200


def normalize_backup_retention(value: object) -> int:
    if (
        not isinstance(value, (str, int))
        or isinstance(value, bool)
    ):
        return DEFAULT_BACKUP_RETENTION
    try:
        retention = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKUP_RETENTION
    return min(
        MAX_BACKUP_RETENTION,
        max(MIN_BACKUP_RETENTION, retention),
    )


def list_automatic_backups(directory: Path) -> list[Path]:
    location = Path(directory)
    if not location.exists():
        return []
    return sorted(
        (
            path
            for path in location.iterdir()
            if path.is_file() and AUTO_BACKUP_PATTERN.fullmatch(path.name)
        ),
        reverse=True,
    )


class AutomaticBackupManager:
    def __init__(
        self,
        storage: Storage,
        *,
        retention: int = DEFAULT_BACKUP_RETENTION,
    ) -> None:
        self.storage = storage
        self.retention = normalize_backup_retention(retention)
        self.directory = storage.path.parent / "Backups"
        self._last_fingerprint: str | None = None

    def create_if_changed(self, snippets: list[Snippet] | None = None) -> Path | None:
        current = snippets if snippets is not None else self.storage.list_snippets()
        fingerprint = self._fingerprint(current)
        if fingerprint == self._last_fingerprint:
            return None

        now = datetime.now()
        destination = self.directory / (
            f"QuickType-auto-{now:%Y%m%d-%H%M%S-%f}.json"
        )
        export_backup(destination, current)
        self._last_fingerprint = fingerprint
        self._prune()
        return destination

    def set_retention(self, retention: int) -> None:
        self.retention = normalize_backup_retention(retention)
        self._prune()

    def _prune(self) -> None:
        if not self.directory.exists():
            return
        backups = list(reversed(list_automatic_backups(self.directory)))
        for path in backups[:-self.retention]:
            path.unlink()

    @staticmethod
    def _fingerprint(snippets: list[Snippet]) -> str:
        values = [
            {
                "abbreviation": snippet.abbreviation,
                "expansion": snippet.expansion,
                "trigger_mode": snippet.trigger_mode.value,
                "enabled": snippet.enabled,
                "category": snippet.category,
                "favorite": snippet.favorite,
                "applications": list(snippet.applications),
                "kind": snippet.kind.value,
                "description": snippet.description,
                "search_terms": list(snippet.search_terms),
                "priority": snippet.priority,
            }
            for snippet in snippets
        ]
        encoded = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
