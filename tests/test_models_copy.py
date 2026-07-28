from quicktype.models import next_copy_abbreviation


def test_copy_abbreviation_uses_next_available_suffix() -> None:
    assert next_copy_abbreviation("sig", {"sig", "sig_copy", "sig_copy2"}) == "sig_copy3"


def test_copy_abbreviation_is_limited_to_64_characters() -> None:
    result = next_copy_abbreviation("x" * 64, {"x" * 64})
    assert result == ("x" * 59) + "_copy"
    assert len(result) == 64
