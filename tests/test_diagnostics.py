from pathlib import Path

from quicktype.diagnostics import collect_diagnostic_report
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage


def test_diagnostic_report_contains_health_but_no_snippet_content(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "secret-abbreviation", "private expansion", TriggerMode.IMMEDIATE)
    )
    storage.set_setting("backup_retention", "37")

    report = collect_diagnostic_report(storage)
    text = report.as_text()

    assert report.database_integrity == "ok"
    assert report.snippet_count == 1
    assert report.backup_retention == 37
    assert "secret-abbreviation" not in text
    assert "private expansion" not in text
    assert "contains no abbreviations" in text
