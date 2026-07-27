from quicktype.i18n import Translator


def test_translation_and_formatting() -> None:
    translator = Translator("pl")
    assert translator("saved", abbr="sig") == "Zapisano snippet „sig”."
    translator.set_language("en")
    assert translator("saved", abbr="sig") == "Saved snippet “sig”."


def test_unknown_language_falls_back_to_english() -> None:
    translator = Translator("xx")
    assert translator.language == "en"
    assert translator("save") == "Save"
