from quicktype.models import validate_abbreviation


def test_abbreviation_validation_accepts_printable_non_whitespace() -> None:
    assert validate_abbreviation(";sig") == []
    assert validate_abbreviation("żółć") == []


def test_abbreviation_validation_reports_each_invalid_shape() -> None:
    assert validate_abbreviation("")[0].code == "required"
    assert validate_abbreviation("two words")[0].code == "whitespace"
    assert validate_abbreviation("x" * 65)[0].code == "too_long"
    assert validate_abbreviation("bad\x01")[0].code == "control"
