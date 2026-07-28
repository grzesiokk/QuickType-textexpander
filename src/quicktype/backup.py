from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Snippet, TriggerMode, validate_abbreviation

BACKUP_FORMAT = "quicktype-backup"
BACKUP_VERSION = 1
MAX_BACKUP_SIZE = 10 * 1024 * 1024


class BackupFormatError(ValueError):
    pass


def export_backup(path: Path, snippets: list[Snippet]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snippets": [_snippet_to_dict(snippet) for snippet in snippets],
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def import_backup(path: Path) -> list[Snippet]:
    source = Path(path)
    if source.stat().st_size > MAX_BACKUP_SIZE:
        raise BackupFormatError("Backup file is larger than 10 MB.")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupFormatError(str(error)) from error

    if not isinstance(document, dict):
        raise BackupFormatError("Backup root must be a JSON object.")
    if document.get("format") != BACKUP_FORMAT:
        raise BackupFormatError("This is not a QuickType backup.")
    if document.get("version") != BACKUP_VERSION:
        raise BackupFormatError("Unsupported QuickType backup version.")
    raw_snippets = document.get("snippets")
    if not isinstance(raw_snippets, list):
        raise BackupFormatError("Backup does not contain a snippets list.")

    snippets = [_snippet_from_dict(item, index) for index, item in enumerate(raw_snippets)]
    abbreviations = [snippet.abbreviation for snippet in snippets]
    if len(abbreviations) != len(set(abbreviations)):
        raise BackupFormatError("Backup contains duplicate abbreviations.")
    return snippets


def _snippet_to_dict(snippet: Snippet) -> dict[str, Any]:
    return {
        "abbreviation": snippet.abbreviation,
        "expansion": snippet.expansion,
        "trigger_mode": snippet.trigger_mode.value,
        "enabled": snippet.enabled,
        "created_at": snippet.created_at.isoformat(timespec="seconds")
        if snippet.created_at
        else None,
        "updated_at": snippet.updated_at.isoformat(timespec="seconds")
        if snippet.updated_at
        else None,
        "usage_count": snippet.usage_count,
        "last_used_at": snippet.last_used_at.isoformat(timespec="seconds")
        if snippet.last_used_at
        else None,
        "category": snippet.category,
        "favorite": snippet.favorite,
    }


def _snippet_from_dict(value: Any, index: int) -> Snippet:
    if not isinstance(value, dict):
        raise BackupFormatError(f"Snippet #{index + 1} must be an object.")
    abbreviation = value.get("abbreviation")
    expansion = value.get("expansion")
    trigger_mode = value.get("trigger_mode")
    enabled = value.get("enabled")
    usage_count = value.get("usage_count", 0)
    category = value.get("category", "")
    favorite = value.get("favorite", False)

    if not isinstance(abbreviation, str) or validate_abbreviation(abbreviation):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid abbreviation.")
    if not isinstance(expansion, str):
        raise BackupFormatError(f"Snippet #{index + 1} has invalid expansion text.")
    try:
        mode = TriggerMode(trigger_mode)
    except (TypeError, ValueError) as error:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid trigger mode.") from error
    if not isinstance(enabled, bool):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid enabled value.")
    if not isinstance(usage_count, int) or isinstance(usage_count, bool) or usage_count < 0:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid usage count.")
    if (
        not isinstance(category, str)
        or len(category.strip()) > 64
        or any(
            character in "\r\n" or ord(character) < 32 or ord(character) == 127
            for character in category
        )
    ):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid category.")
    if not isinstance(favorite, bool):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid favorite value.")

    return Snippet(
        id=None,
        abbreviation=abbreviation,
        expansion=expansion,
        trigger_mode=mode,
        enabled=enabled,
        created_at=_optional_datetime(value.get("created_at"), index, "created_at"),
        updated_at=_optional_datetime(value.get("updated_at"), index, "updated_at"),
        usage_count=usage_count,
        last_used_at=_optional_datetime(value.get("last_used_at"), index, "last_used_at"),
        category=category.strip(),
        favorite=favorite,
    )


def _optional_datetime(value: Any, index: int, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid {field}.")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise BackupFormatError(f"Snippet #{index + 1} has an invalid {field}.") from error
