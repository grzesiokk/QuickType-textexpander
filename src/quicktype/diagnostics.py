from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from .constants import APP_VERSION
from .maintenance import collect_data_summary, format_file_size
from .storage import Storage


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    created_at: datetime
    app_version: str
    operating_system: str
    frozen: bool
    database_integrity: str
    database_size: str
    snippet_count: int
    backup_count: int
    automatic_backups: bool
    backup_retention: int
    engine_active: bool

    def as_text(self) -> str:
        return "\n".join(
            (
                "QuickType diagnostic report",
                f"Created (UTC): {self.created_at.isoformat(timespec='seconds')}",
                f"QuickType: {self.app_version}",
                f"Windows: {self.operating_system}",
                f"Frozen executable: {'yes' if self.frozen else 'no'}",
                f"Database integrity: {self.database_integrity}",
                f"Database size: {self.database_size}",
                f"Snippet count: {self.snippet_count}",
                f"Backup count: {self.backup_count}",
                f"Automatic backups: {'enabled' if self.automatic_backups else 'disabled'}",
                f"Backup retention: {self.backup_retention}",
                f"Expansion engine: {'active' if self.engine_active else 'paused'}",
                "",
                "Privacy: this report contains no abbreviations, expansion text, "
                "clipboard data, typed characters, categories, or application lists.",
            )
        )


def collect_diagnostic_report(storage: Storage) -> DiagnosticReport:
    summary = collect_data_summary(storage)
    try:
        valid, details = storage.check_integrity()
    except Exception as error:
        integrity = f"error ({type(error).__name__})"
    else:
        integrity = "ok" if valid else f"failed ({details})"
    return DiagnosticReport(
        created_at=datetime.now(timezone.utc),
        app_version=APP_VERSION,
        operating_system=platform.platform(),
        frozen=bool(getattr(sys, "frozen", False)),
        database_integrity=integrity,
        database_size=format_file_size(summary.database_bytes),
        snippet_count=summary.snippet_count,
        backup_count=summary.backup_count,
        automatic_backups=storage.get_setting("automatic_backups", "1") != "0",
        backup_retention=_setting_int(
            storage,
            "backup_retention",
            default=20,
            minimum=1,
            maximum=200,
        ),
        engine_active=storage.get_setting("engine_active", "1") != "0",
    )


def _setting_int(
    storage: Storage,
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(storage.get_setting(key, str(default)) or default)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))
