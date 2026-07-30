from datetime import datetime

from quicktype.clipboard_history import ClipboardHistory
from quicktype.search import SearchIndex, parse_search_query


def test_clipboard_history_deduplicates_and_keeps_newest_first() -> None:
    history = ClipboardHistory(max_entries=3, max_bytes=100)
    first = datetime(2026, 7, 31, 10, 0)
    second = datetime(2026, 7, 31, 10, 1)

    assert history.add("first", captured_at=first)
    assert history.add("second", captured_at=second)
    assert history.add("first", captured_at=second)

    assert [item.text for item in history.items] == ["first", "second"]
    assert history.items[0].captured_at == second


def test_clipboard_history_enforces_entry_and_byte_limits() -> None:
    history = ClipboardHistory(max_entries=2, max_bytes=6)

    assert history.add("123")
    assert history.add("456")
    assert history.add("789")
    assert not history.add("1234567")
    assert not history.add("   ")

    assert [item.text for item in history.items] == ["789", "456"]
    assert history.total_bytes == 6


def test_clipboard_history_clear_releases_all_text() -> None:
    history = ClipboardHistory()
    history.add("sensitive")

    history.clear()

    assert history.items == ()
    assert history.total_bytes == 0


def test_clipboard_scope_is_explicit_and_diacritic_insensitive() -> None:
    history = ClipboardHistory()
    history.add("Zażółć gęślą jaźń")
    index = SearchIndex.build([], clipboard_history=history.items)

    assert parse_search_query("schowek:zolc").source_scope == "clipboard"
    assert index.search("") == []
    results = index.search("clip:zolc")
    assert len(results) == 1
    assert results[0].source == "clipboard"
    assert results[0].snippet is None
    assert results[0].clipboard_text == "Zażółć gęślą jaźń"
