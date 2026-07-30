from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import perf_counter

from quicktype.builtin_libraries import (
    BuiltinCatalog,
    BuiltinLibraryId,
    BuiltinLibrarySettings,
)
from quicktype.models import Snippet, TriggerMode
from quicktype.search import SearchIndex, normalize_search_text, parse_search_query
from quicktype.storage import Storage


def test_normalizes_polish_diacritics_and_parses_scopes() -> None:
    assert normalize_search_text("Łódź, ŻÓŁĆ") == "lodz, zolc"
    query = parse_search_query('emoji:"uśmiechnięta twarz" oczy')
    assert query.source_scope == "emoji"
    assert query.terms == ("usmiechnieta twarz", "oczy")
    assert parse_search_query("kod krakow").source_scope == "postal_pl"
    assert parse_search_query("emoji").terms == ()


def test_search_uses_all_terms_phrases_and_user_ranking() -> None:
    snippets = [
        Snippet(
            1,
            ";adres",
            "ul. Długa 1, Łódź",
            TriggerMode.IMMEDIATE,
            description="Adres oddziału",
            search_terms=("biuro", "centrala"),
            favorite=True,
            last_used_at=datetime.now(),
        ),
        Snippet(
            2,
            ";inne",
            "Adres w Krakowie",
            TriggerMode.IMMEDIATE,
            description="Inne biuro",
        ),
    ]
    index = SearchIndex.build(snippets)
    assert [item.snippet.id for item in index.search('"adres oddzialu" lodz')] == [1]
    assert [item.snippet.id for item in index.search("adres centrala")] == [1]
    assert index.search(".")[0].snippet.id == 1


def test_search_includes_only_enabled_builtin_libraries(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    catalog = BuiltinCatalog(storage)
    catalog.set_settings(
        BuiltinLibraryId.FLAGS,
        BuiltinLibrarySettings(True, "full", ":flaga-"),
    )
    index = SearchIndex.build([], catalog)

    results = index.search("flaga polska")

    assert results
    assert results[0].source == BuiltinLibraryId.FLAGS.value
    assert results[0].snippet.expansion == "🇵🇱"
    assert not index.search("emoji usmiech")


def test_search_returns_at_most_two_hundred_results() -> None:
    snippets = [
        Snippet(
            index,
            f";test-{index}",
            "wspólny tekst",
            TriggerMode.IMMEDIATE,
        )
        for index in range(500)
    ]
    assert len(SearchIndex.build(snippets).search("wspolny")) == 200


def test_full_catalog_index_and_query_remain_responsive(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    catalog = BuiltinCatalog(storage)
    for library_id, profile, prefix in (
        (BuiltinLibraryId.EMOJI, "full", ":"),
        (BuiltinLibraryId.FLAGS, "full", ":flaga-"),
        (BuiltinLibraryId.POSTAL_PL, "full", ":kod-"),
        (BuiltinLibraryId.AUTOCORRECT_PL, "extended", ""),
    ):
        catalog.set_settings(
            library_id,
            BuiltinLibrarySettings(True, profile, prefix),
        )

    started = perf_counter()
    index = SearchIndex.build([], catalog)
    build_duration = perf_counter() - started
    started = perf_counter()
    results = index.search("kod krakow")
    query_duration = perf_counter() - started

    assert sum(len(source.items) for source in index.builtin_sources) >= 50_000
    assert results
    assert len(results) <= 200
    assert build_duration < 3
    assert query_duration < 0.5
