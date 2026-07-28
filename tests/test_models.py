import pytest

from quicktype.models import (
    Snippet,
    TriggerMode,
    normalize_applications,
    snippet_applies_to_process,
    validate_abbreviation,
    validate_category,
)


def test_abbreviation_validation_accepts_printable_non_whitespace() -> None:
    assert validate_abbreviation(";sig") == []
    assert validate_abbreviation("żółć") == []


def test_abbreviation_validation_reports_each_invalid_shape() -> None:
    assert validate_abbreviation("")[0].code == "required"
    assert validate_abbreviation("two words")[0].code == "whitespace"
    assert validate_abbreviation("x" * 65)[0].code == "too_long"
    assert validate_abbreviation("bad\x01")[0].code == "control"


def test_category_validation_allows_empty_and_rejects_invalid_values() -> None:
    assert validate_category("") == []
    assert validate_category("Praca") == []
    assert validate_category("x" * 65)[0].code == "too_long"
    assert validate_category("bad\ncategory")[0].code == "control"


def test_applications_are_normalized_case_insensitively() -> None:
    assert normalize_applications((" Code.exe ", "WINWORD.EXE", "code.EXE", "")) == (
        "Code.exe",
        "WINWORD.EXE",
    )
    with pytest.raises(ValueError):
        normalize_applications((r"C:\Windows\notepad.exe",))


def test_snippet_application_scope_is_case_insensitive() -> None:
    global_snippet = Snippet(None, "all", "x", TriggerMode.IMMEDIATE)
    scoped = Snippet(
        None,
        "code",
        "x",
        TriggerMode.IMMEDIATE,
        applications=("Code.exe",),
    )
    assert snippet_applies_to_process(global_snippet, "notepad.exe")
    assert snippet_applies_to_process(scoped, "CODE.EXE")
    assert not snippet_applies_to_process(scoped, "notepad.exe")
