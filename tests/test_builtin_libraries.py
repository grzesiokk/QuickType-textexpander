from pathlib import Path
from time import perf_counter

from quicktype.builtin_libraries import (
    LIBRARY_DEFINITIONS,
    BuiltinCatalog,
    BuiltinLibraryId,
    BuiltinLibrarySettings,
)
from quicktype.matcher import SnippetMatcher
from quicktype.models import SnippetKind, TriggerMode
from quicktype.storage import Storage
from quicktype.template_engine import render_template


def _catalog(tmp_path: Path) -> BuiltinCatalog:
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    return BuiltinCatalog(storage)


def test_builtin_resources_have_expected_stable_counts(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    assert catalog.item_count(BuiltinLibraryId.AUTOCORRECT_PL) == 3000
    assert sum(
        item.profile == "conservative"
        for item in catalog.items(BuiltinLibraryId.AUTOCORRECT_PL)
    ) == 800
    assert catalog.item_count(BuiltinLibraryId.EMOJI) >= 3600
    assert catalog.item_count(BuiltinLibraryId.FLAGS) >= 250
    assert catalog.item_count(BuiltinLibraryId.POSTAL_PL) >= 50_000

    for definition in LIBRARY_DEFINITIONS:
        items = catalog.items(definition.library_id)
        assert len({item.item_id for item in items}) == len(items)
        assert len({item.slug for item in items}) == len(items)


def test_libraries_are_disabled_by_default_and_settings_persist(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)

    assert not catalog.runtime_snippets()
    catalog.set_settings(
        BuiltinLibraryId.EMOJI,
        BuiltinLibrarySettings(True, "full", "::"),
    )

    assert catalog.settings(BuiltinLibraryId.EMOJI) == BuiltinLibrarySettings(
        True,
        "full",
        "::",
    )
    first = catalog.items(BuiltinLibraryId.EMOJI)[0]
    assert catalog.trigger_for_item(first).startswith("::")


def test_autocorrection_profiles_case_and_item_override(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.set_settings(
        BuiltinLibraryId.AUTOCORRECT_PL,
        BuiltinLibrarySettings(True, "conservative", ""),
    )
    conservative = list(catalog.active_items(BuiltinLibraryId.AUTOCORRECT_PL))
    assert len(conservative) == 800
    selected = conservative[0]
    catalog.set_item_enabled(selected, enabled=False)

    runtime = catalog.runtime_snippets()
    source_items = {
        snippet.source_item_id
        for snippet in runtime
        if snippet.source_library == BuiltinLibraryId.AUTOCORRECT_PL.value
    }
    assert selected.item_id not in source_items
    variants = [
        snippet
        for snippet in runtime
        if snippet.source_library == BuiltinLibraryId.AUTOCORRECT_PL.value
    ]
    assert any(snippet.abbreviation[:1].isupper() for snippet in variants)

    catalog.set_settings(
        BuiltinLibraryId.AUTOCORRECT_PL,
        BuiltinLibrarySettings(True, "extended", ""),
    )
    assert len(list(catalog.active_items(BuiltinLibraryId.AUTOCORRECT_PL))) == 2999


def test_inline_calculator_is_safe_regex_library(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.set_settings(
        BuiltinLibraryId.CALCULATOR,
        BuiltinLibrarySettings(True, "full", ""),
    )
    snippet = catalog.runtime_snippets()[0]

    assert snippet.kind == SnippetKind.REGEX
    assert snippet.trigger_mode == TriggerMode.IMMEDIATE
    matcher = SnippetMatcher([snippet])
    action = None
    for character in "10+5=?":
        action = matcher.feed_character(character)
    assert action is not None
    rendered = render_template(
        action.snippet.expansion,
        match_groups=dict(action.match_groups),
    )
    assert rendered.text == "15"
    assert not rendered.issues


def test_large_enabled_catalog_builds_index_within_budget(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.set_settings(
        BuiltinLibraryId.POSTAL_PL,
        BuiltinLibrarySettings(True, "full", ":kod-"),
    )

    started = perf_counter()
    snippets = catalog.runtime_snippets()
    matcher = SnippetMatcher(snippets)
    elapsed = perf_counter() - started

    assert len(snippets) >= 50_000
    assert matcher is not None
    assert elapsed < 8.0
