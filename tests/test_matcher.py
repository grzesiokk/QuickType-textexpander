from time import perf_counter

from quicktype.matcher import SnippetMatcher
from quicktype.models import Snippet, SnippetKind, TriggerMode


def snippet(abbreviation: str, mode: TriggerMode, expansion: str = "expanded") -> Snippet:
    return Snippet(None, abbreviation, expansion, mode)


def regex_snippet(
    pattern: str,
    mode: TriggerMode,
    expansion: str = "expanded",
    *,
    priority: int = 0,
) -> Snippet:
    return Snippet(
        None,
        pattern,
        expansion,
        mode,
        kind=SnippetKind.REGEX,
        priority=priority,
    )


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


def test_regex_delimiter_match_exposes_numbered_and_named_groups() -> None:
    matcher = SnippetMatcher(
        [
            regex_snippet(
                r"order-(?P<number>\d+)",
                TriggerMode.DELIMITER,
                "Order {{match:number}}",
            )
        ]
    )

    action = feed(matcher, "order-123 ")

    assert action is not None
    assert action.delete_count == len("order-123")
    assert dict(action.match_groups)["number"] == "123"
    assert dict(action.match_groups)["1"] == "123"


def test_literal_wins_over_regex_and_regex_uses_priority() -> None:
    literal = snippet("abc", TriggerMode.DELIMITER, "literal")
    low = regex_snippet(r"a.c", TriggerMode.DELIMITER, "low")
    high = regex_snippet(r"ab.", TriggerMode.DELIMITER, "high", priority=50)
    matcher = SnippetMatcher([low, high, literal])

    literal_action = feed(matcher, "abc ")

    assert literal_action is not None
    assert literal_action.snippet.expansion == "literal"

    matcher.replace_snippets([low, high])
    regex_action = feed(matcher, "abc ")

    assert regex_action is not None
    assert regex_action.snippet.expansion == "high"


def test_invalid_or_empty_regex_does_not_break_literal_matching() -> None:
    matcher = SnippetMatcher(
        [
            regex_snippet("(", TriggerMode.IMMEDIATE, "invalid"),
            regex_snippet("^$", TriggerMode.IMMEDIATE, "empty"),
            snippet("ok", TriggerMode.IMMEDIATE, "valid"),
        ]
    )

    action = feed(matcher, "ok")

    assert action is not None
    assert action.snippet.expansion == "valid"


def test_pathological_regex_is_bounded_by_match_timeout() -> None:
    matcher = SnippetMatcher(
        [
            regex_snippet(
                r"(?:(?:a|aa)+)b",
                TriggerMode.IMMEDIATE,
                "should not match",
            )
        ]
    )

    started = perf_counter()
    action = feed(matcher, ("a" * 255) + "x")
    elapsed = perf_counter() - started

    assert action is None
    assert elapsed < 2.0
