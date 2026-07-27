from pathlib import Path

import pytest

from quicktype.models import Snippet, TriggerMode
from quicktype.storage import DuplicateAbbreviationError, Storage


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    result = Storage(tmp_path / "data" / "quicktype.sqlite3")
    result.initialize()
    return result


def test_database_is_created_and_starts_empty(storage: Storage) -> None:
    assert storage.path.exists()
    assert storage.list_snippets() == []
    assert storage.get_setting("missing", "fallback") == "fallback"


def test_snippet_crud_and_case_sensitive_uniqueness(storage: Storage) -> None:
    first = storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER, True)
    )
    second = storage.save_snippet(
        Snippet(None, "Sig", "Formal regards", TriggerMode.IMMEDIATE, False)
    )
    assert first.id is not None
    assert second.id is not None
    assert len(storage.list_snippets()) == 2

    updated = storage.save_snippet(
        Snippet(first.id, "sig", "Best regards", TriggerMode.IMMEDIATE, True)
    )
    assert updated.expansion == "Best regards"
    assert updated.trigger_mode == TriggerMode.IMMEDIATE

    storage.delete_snippet(second.id)
    assert [entry.abbreviation for entry in storage.list_snippets()] == ["sig"]


def test_duplicate_abbreviation_is_rejected(storage: Storage) -> None:
    storage.save_snippet(Snippet(None, "sig", "One", TriggerMode.DELIMITER))
    with pytest.raises(DuplicateAbbreviationError):
        storage.save_snippet(Snippet(None, "sig", "Two", TriggerMode.IMMEDIATE))


def test_settings_are_upserted(storage: Storage) -> None:
    storage.set_setting("language", "pl")
    storage.set_setting("language", "en")
    assert storage.get_setting("language") == "en"
