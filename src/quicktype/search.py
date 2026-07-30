from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from heapq import heappush, heapreplace
from typing import Iterable

from .builtin_libraries import (
    LIBRARY_DEFINITIONS,
    BuiltinCatalog,
    BuiltinItem,
    BuiltinLibraryDefinition,
    BuiltinLibraryId,
    BuiltinLibrarySettings,
)
from .clipboard_history import ClipboardHistoryItem
from .models import Snippet, SnippetKind

MAX_SEARCH_RESULTS = 200
_POLISH_TRANSLATION = str.maketrans({"ł": "l", "Ł": "L"})
_QUERY_PART = re.compile(
    r'(?:(?P<scope>[\w-]+):)?(?:"(?P<quoted>[^"]+)"|(?P<word>\S+))'
)
_SCOPE_ALIASES = {
    "clip": "clipboard",
    "clipboard": "clipboard",
    "schowek": "clipboard",
    "emoji": BuiltinLibraryId.EMOJI.value,
    "flaga": BuiltinLibraryId.FLAGS.value,
    "flagi": BuiltinLibraryId.FLAGS.value,
    "flag": BuiltinLibraryId.FLAGS.value,
    "kod": BuiltinLibraryId.POSTAL_PL.value,
    "kody": BuiltinLibraryId.POSTAL_PL.value,
    "poczta": BuiltinLibraryId.POSTAL_PL.value,
    "autokorekta": BuiltinLibraryId.AUTOCORRECT_PL.value,
}


def normalize_search_text(value: object) -> str:
    translated = str(value or "").translate(_POLISH_TRANSLATION).casefold()
    decomposed = unicodedata.normalize("NFKD", translated)
    return " ".join(
        "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        ).split()
    )


@dataclass(frozen=True, slots=True)
class SearchQuery:
    terms: tuple[str, ...]
    source_scope: str = ""


def parse_search_query(value: str) -> SearchQuery:
    scope = ""
    terms: list[str] = []
    matches = list(_QUERY_PART.finditer(value))
    for index, match in enumerate(matches):
        raw_scope = normalize_search_text(match.group("scope"))
        part = normalize_search_text(
            match.group("quoted") or match.group("word")
        )
        candidate = _SCOPE_ALIASES.get(raw_scope)
        if candidate:
            scope = candidate
            if part:
                terms.append(part)
            continue
        if index == 0 and part in _SCOPE_ALIASES:
            scope = _SCOPE_ALIASES[part]
            continue
        terms.append(part)
    return SearchQuery(tuple(dict.fromkeys(terms)), scope)


@dataclass(frozen=True, slots=True)
class SearchEntry:
    key: str
    title: str
    abbreviation: str
    category: str
    preview: str
    source: str
    snippet: Snippet | None = None
    clipboard_text: str | None = None
    favorite: bool = False
    usage_count: int = 0
    last_used_at: datetime | None = None
    item: BuiltinItem | None = None
    _title_search: str = field(default="", repr=False)
    _abbreviation_search: str = field(default="", repr=False)
    _haystack: str = field(default="", repr=False)


class SearchIndex:
    def __init__(
        self,
        entries: Iterable[SearchEntry],
        *,
        catalog: BuiltinCatalog | None = None,
        builtin_sources: tuple[_BuiltinSource, ...] = (),
        builtin_usage: dict[tuple[str, str], tuple[int, datetime | None]] | None = None,
        clipboard_entries: tuple[SearchEntry, ...] = (),
    ) -> None:
        self.entries = tuple(entries)
        self.catalog = catalog
        self.builtin_sources = builtin_sources
        self.builtin_usage = builtin_usage or {}
        self.clipboard_entries = clipboard_entries

    @classmethod
    def build(
        cls,
        snippets: Iterable[Snippet],
        catalog: BuiltinCatalog | None = None,
        *,
        process_name: str = "",
        clipboard_history: Iterable[ClipboardHistoryItem] = (),
    ) -> SearchIndex:
        entries = [
            _user_entry(snippet)
            for snippet in snippets
            if snippet.enabled
            and snippet.kind == SnippetKind.LITERAL
            and (
                not process_name
                or not snippet.applications
                or process_name.casefold()
                in {name.casefold() for name in snippet.applications}
            )
        ]
        sources: list[_BuiltinSource] = []
        usage: dict[tuple[str, str], tuple[int, datetime | None]] = {}
        if catalog is not None:
            usage = catalog.storage.list_builtin_usage()
            for definition in LIBRARY_DEFINITIONS:
                library_id = definition.library_id
                if library_id == BuiltinLibraryId.CALCULATOR:
                    continue
                settings = catalog.settings(library_id)
                if not settings.enabled:
                    continue
                sources.append(
                    _BuiltinSource(
                        definition,
                        settings,
                        catalog.items(library_id),
                        catalog.storage.list_disabled_builtin_items(
                            library_id.value
                        ),
                    )
                )
        return cls(
            entries,
            catalog=catalog,
            builtin_sources=tuple(sources),
            builtin_usage=usage,
            clipboard_entries=tuple(
                _clipboard_entry(item) for item in clipboard_history
            ),
        )

    def search(
        self,
        value: str,
        *,
        limit: int = MAX_SEARCH_RESULTS,
    ) -> list[SearchEntry]:
        query = parse_search_query(value)
        now = datetime.now()
        recent_only = value.strip() == "."
        terms = () if recent_only else query.terms
        result_limit = max(1, min(limit, MAX_SEARCH_RESULTS))
        heap: list[tuple[float, int, int, int]] = []
        order = 0

        def consider(
            score: float,
            source_index: int,
            item_index: int,
        ) -> None:
            nonlocal order
            candidate = (score, -order, source_index, item_index)
            order += 1
            if len(heap) < result_limit:
                heappush(heap, candidate)
            elif candidate[:2] > heap[0][:2]:
                heapreplace(heap, candidate)

        for entry_index, entry in enumerate(self.entries):
            if query.source_scope and entry.source != query.source_scope:
                continue
            if entry.source == "clipboard" and query.source_scope != "clipboard":
                continue
            if terms and not all(term in entry._haystack for term in terms):
                continue
            if recent_only and entry.last_used_at is None:
                continue
            consider(_score(entry, terms, now), -1, entry_index)

        for item_index, entry in enumerate(self.clipboard_entries):
            if query.source_scope != "clipboard":
                continue
            if terms and not all(term in entry._haystack for term in terms):
                continue
            consider(_score(entry, terms, now), -2, item_index)

        for source_index, source in enumerate(self.builtin_sources):
            library_id = source.definition.library_id
            if query.source_scope and query.source_scope != library_id.value:
                continue
            for item_index, item in enumerate(source.items):
                if item.item_id in source.disabled_items:
                    continue
                if (
                    library_id == BuiltinLibraryId.AUTOCORRECT_PL
                    and source.settings.profile == "conservative"
                    and item.profile != "conservative"
                ):
                    continue
                haystack = item.search_text or normalize_search_text(
                    " ".join(
                        (item.title, item.slug, item.expansion, *item.keywords)
                    )
                )
                if terms and not all(term in haystack for term in terms):
                    continue
                count, last_used = self.builtin_usage.get(
                    (library_id.value, item.item_id),
                    (0, None),
                )
                if recent_only and last_used is None:
                    continue
                abbreviation = source.abbreviation(item)
                score = _score_values(
                    title_search=(
                        normalize_search_text(item.title)
                        if terms
                        else ""
                    ),
                    abbreviation_search=abbreviation.casefold(),
                    terms=terms,
                    favorite=False,
                    usage_count=count,
                    last_used_at=last_used,
                    source=library_id.value,
                    now=now,
                )
                consider(score, source_index, item_index)

        matches: list[tuple[float, SearchEntry]] = []
        for score, _order, source_index, item_index in heap:
            if source_index == -1:
                entry = self.entries[item_index]
            elif source_index == -2:
                entry = self.clipboard_entries[item_index]
            else:
                source = self.builtin_sources[source_index]
                item = source.items[item_index]
                count, last_used = self.builtin_usage.get(
                    (source.definition.library_id.value, item.item_id),
                    (0, None),
                )
                if self.catalog is None:
                    continue
                entry = _builtin_entry(
                    item,
                    self.catalog,
                    abbreviation=source.abbreviation(item),
                    usage_count=count,
                    last_used_at=last_used,
                )
            matches.append((score, entry))
        matches.sort(
            key=lambda result: (
                -result[0],
                result[1].title.casefold(),
                result[1].abbreviation.casefold(),
            )
        )
        return [entry for _score_value, entry in matches]


@dataclass(frozen=True, slots=True)
class _BuiltinSource:
    definition: BuiltinLibraryDefinition
    settings: BuiltinLibrarySettings
    items: tuple[BuiltinItem, ...]
    disabled_items: set[str]

    def abbreviation(self, item: BuiltinItem) -> str:
        if self.definition.library_id == BuiltinLibraryId.AUTOCORRECT_PL:
            return item.slug
        return self.settings.prefix + item.slug + self.definition.suffix


def _user_entry(snippet: Snippet) -> SearchEntry:
    title = snippet.description or snippet.abbreviation
    preview = snippet.expansion.replace("\r", "").replace("\n", " ↵ ")
    haystack = normalize_search_text(
        " ".join(
            (
                title,
                snippet.abbreviation,
                snippet.category,
                snippet.expansion,
                *snippet.search_terms,
                *snippet.applications,
            )
        )
    )
    return SearchEntry(
        key=f"user:{snippet.id if snippet.id is not None else snippet.abbreviation}",
        title=title,
        abbreviation=snippet.abbreviation,
        category=snippet.category,
        preview=preview,
        source="user",
        snippet=snippet,
        favorite=snippet.favorite,
        usage_count=snippet.usage_count,
        last_used_at=snippet.last_used_at,
        _title_search=normalize_search_text(title),
        _abbreviation_search=normalize_search_text(snippet.abbreviation),
        _haystack=haystack,
    )


def _builtin_entry(
    item: BuiltinItem,
    catalog: BuiltinCatalog,
    *,
    abbreviation: str,
    usage_count: int,
    last_used_at: datetime | None,
) -> SearchEntry:
    haystack = (
        f"{item.search_text} {abbreviation.casefold()}"
        if item.search_text
        else normalize_search_text(
            " ".join((item.title, abbreviation, item.expansion, *item.keywords))
        )
    )
    return SearchEntry(
        key=f"{item.library_id.value}:{item.item_id}",
        title=item.title,
        abbreviation=abbreviation,
        category="",
        preview=item.expansion,
        source=item.library_id.value,
        snippet=catalog.snippet_for_item(item, abbreviation=abbreviation),
        usage_count=usage_count,
        last_used_at=last_used_at,
        item=item,
        _title_search=normalize_search_text(item.title),
        _abbreviation_search=normalize_search_text(abbreviation),
        _haystack=haystack,
    )


def _clipboard_entry(item: ClipboardHistoryItem) -> SearchEntry:
    first_line = item.text.replace("\r", "").split("\n", 1)[0].strip()
    title = first_line[:80] or "Clipboard"
    preview = item.text.replace("\r", "").replace("\n", " ↵ ")
    haystack = normalize_search_text(item.text)
    return SearchEntry(
        key=f"clipboard:{item.captured_at.isoformat()}:{hash(item.text)}",
        title=title,
        abbreviation="clipboard",
        category="",
        preview=preview,
        source="clipboard",
        clipboard_text=item.text,
        last_used_at=item.captured_at,
        _title_search=normalize_search_text(title),
        _abbreviation_search="clipboard",
        _haystack=haystack,
    )


def _score(
    entry: SearchEntry,
    terms: tuple[str, ...],
    now: datetime,
) -> float:
    return _score_values(
        title_search=entry._title_search,
        abbreviation_search=entry._abbreviation_search,
        terms=terms,
        favorite=entry.favorite,
        usage_count=entry.usage_count,
        last_used_at=entry.last_used_at,
        source=entry.source,
        now=now,
    )


def _score_values(
    *,
    title_search: str,
    abbreviation_search: str,
    terms: tuple[str, ...],
    favorite: bool,
    usage_count: int,
    last_used_at: datetime | None,
    source: str,
    now: datetime,
) -> float:
    score = 0.0
    joined = " ".join(terms)
    if joined:
        if abbreviation_search == joined:
            score += 1_000
        elif abbreviation_search.startswith(joined):
            score += 500
        if title_search == joined:
            score += 700
        elif title_search.startswith(joined):
            score += 350
        score += sum(
            80 if title_search.startswith(term) else 25
            for term in terms
        )
    if favorite:
        score += 160
    score += min(120.0, math.log2(usage_count + 1) * 18)
    if last_used_at is not None:
        age_days = max(0.0, (now - last_used_at).total_seconds() / 86_400)
        score += max(0.0, 100.0 - min(age_days, 100.0))
    if source == "user":
        score += 20
    return score
