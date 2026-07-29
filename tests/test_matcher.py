from time import perf_counter

from quicktype.matcher import SnippetMatcher
from quicktype.models import Snippet, TriggerMode


def snippet(abbreviation: str, mode: TriggerMode, expansion: str = "expanded") -> Snippet:
    return Snippet(None, abbreviation, expansion, mode)


def feed(matcher: SnippetMatcher, text: str):
    action = None
    for character in text:
        action = matcher.feed_character(character)
    return action


def test_delimiter_match_suppresses_and_replays_separator() -> None:
    matcher = SnippetMatcher([snippet("addr", TriggerMode.DELIMITER)])
    action = feed(matcher, "addr ")
    assert action is not None
    assert action.delete_count == 4
    assert action.success_suffix_text == " "
    assert action.fallback_text == " "
    assert matcher.buffer == ""


def test_immediate_match_suppresses_only_last_abbreviation_character() -> None:
    matcher = SnippetMatcher([snippet(";sig", TriggerMode.IMMEDIATE)])
    action = feed(matcher, ";sig")
    assert action is not None
    assert action.delete_count == 3
    assert action.success_suffix_text == ""
    assert action.fallback_text == "g"


def test_match_requires_a_word_boundary() -> None:
    matcher = SnippetMatcher([snippet("addr", TriggerMode.DELIMITER)])
    assert feed(matcher, "myaddr ") is None


def test_longest_matching_abbreviation_wins() -> None:
    matcher = SnippetMatcher(
        [
            snippet("sig", TriggerMode.IMMEDIATE, "short"),
            snippet(";sig", TriggerMode.IMMEDIATE, "long"),
        ]
    )
    action = feed(matcher, ";sig")
    assert action is not None
    assert action.snippet.expansion == "long"


def test_backspace_changes_the_match_buffer() -> None:
    matcher = SnippetMatcher([snippet("addr", TriggerMode.DELIMITER)])
    feed(matcher, "addx")
    matcher.backspace()
    action = feed(matcher, "r ")
    assert action is not None


def test_special_separator_is_replayed_as_virtual_key() -> None:
    matcher = SnippetMatcher([snippet("sig", TriggerMode.DELIMITER)])
    feed(matcher, "sig")
    action = matcher.feed_special_separator(0x09)
    assert action is not None
    assert action.success_suffix_vk == 0x09
    assert action.fallback_vk == 0x09


def test_large_library_matching_remains_responsive() -> None:
    snippets = [
        snippet(f";entry{number:05d}", TriggerMode.IMMEDIATE)
        for number in range(10_000)
    ]
    started = perf_counter()
    matcher = SnippetMatcher(snippets)
    action = feed(matcher, ";entry09999")
    elapsed = perf_counter() - started

    assert action is not None
    assert action.snippet.abbreviation == ";entry09999"
    assert elapsed < 2.0
