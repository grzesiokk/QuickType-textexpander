from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterator

from .constants import resource_path
from .models import Snippet, SnippetKind, TriggerMode
from .storage import Storage


class BuiltinLibraryId(StrEnum):
    AUTOCORRECT_PL = "autocorrect_pl"
    EMOJI = "emoji"
    FLAGS = "flags"
    POSTAL_PL = "postal_pl"
    CALCULATOR = "calculator"


class BuiltinLibrarySettingsError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BuiltinLibraryDefinition:
    library_id: BuiltinLibraryId
    filename: str | None
    name_key: str
    description_key: str
    default_profile: str
    profiles: tuple[str, ...]
    default_prefix: str
    suffix: str
    prefix_editable: bool = True


@dataclass(frozen=True, slots=True)
class BuiltinLibrarySettings:
    enabled: bool
    profile: str
    prefix: str


@dataclass(frozen=True, slots=True)
class BuiltinItem:
    item_id: str
    library_id: BuiltinLibraryId
    title: str
    expansion: str
    slug: str
    keywords: tuple[str, ...]
    profile: str
    search_text: str = ""


LIBRARY_DEFINITIONS = (
    BuiltinLibraryDefinition(
        BuiltinLibraryId.AUTOCORRECT_PL,
        "builtin_autocorrect_pl.json.gz",
        "library_autocorrect_pl",
        "library_autocorrect_pl_description",
        "conservative",
        ("conservative", "extended"),
        "",
        "",
        prefix_editable=False,
    ),
    BuiltinLibraryDefinition(
        BuiltinLibraryId.EMOJI,
        "builtin_emoji.json.gz",
        "library_emoji",
        "library_emoji_description",
        "full",
        ("full",),
        ":",
        ":",
    ),
    BuiltinLibraryDefinition(
        BuiltinLibraryId.FLAGS,
        "builtin_flags.json.gz",
        "library_flags",
        "library_flags_description",
        "full",
        ("full",),
        ":flaga-",
        ":",
    ),
    BuiltinLibraryDefinition(
        BuiltinLibraryId.POSTAL_PL,
        "builtin_postal_pl.json.gz",
        "library_postal_pl",
        "library_postal_pl_description",
        "full",
        ("full",),
        ":kod-",
        ":",
    ),
    BuiltinLibraryDefinition(
        BuiltinLibraryId.CALCULATOR,
        None,
        "library_calculator",
        "library_calculator_description",
        "full",
        ("full",),
        "",
        "",
        prefix_editable=False,
    ),
)
DEFINITIONS_BY_ID = {
    definition.library_id: definition
    for definition in LIBRARY_DEFINITIONS
}
CALCULATOR_PATTERN = (
    r"(?P<expression>(?:\d+(?:[.,]\d+)?|[+\-*/%(). ]){1,120})=\?"
)


class BuiltinCatalog:
    def __init__(
        self,
        storage: Storage,
        *,
        resources: Path | None = None,
    ) -> None:
        self.storage = storage
        self.resources = resources
        self._cache: dict[BuiltinLibraryId, tuple[BuiltinItem, ...]] = {}

    def settings(
        self,
        library_id: BuiltinLibraryId,
    ) -> BuiltinLibrarySettings:
        definition = DEFINITIONS_BY_ID[library_id]
        stored = self.storage.get_builtin_library_settings(library_id.value)
        if stored is None:
            return BuiltinLibrarySettings(
                enabled=False,
                profile=definition.default_profile,
                prefix=definition.default_prefix,
            )
        enabled, profile, prefix = stored
        if profile not in definition.profiles:
            profile = definition.default_profile
        if not definition.prefix_editable:
            prefix = definition.default_prefix
        return BuiltinLibrarySettings(enabled, profile, prefix)

    def set_settings(
        self,
        library_id: BuiltinLibraryId,
        settings: BuiltinLibrarySettings,
    ) -> None:
        definition = DEFINITIONS_BY_ID[library_id]
        if settings.profile not in definition.profiles:
            raise BuiltinLibrarySettingsError("profile")
        prefix = settings.prefix if definition.prefix_editable else definition.default_prefix
        self._validate_prefix(
            library_id,
            prefix,
            enabled=settings.enabled,
        )
        self.storage.set_builtin_library_settings(
            library_id.value,
            enabled=settings.enabled,
            profile=settings.profile,
            prefix=prefix,
        )

    def items(
        self,
        library_id: BuiltinLibraryId,
    ) -> tuple[BuiltinItem, ...]:
        if library_id == BuiltinLibraryId.CALCULATOR:
            return ()
        cached = self._cache.get(library_id)
        if cached is not None:
            return cached
        definition = DEFINITIONS_BY_ID[library_id]
        if definition.filename is None:
            return ()
        path = (
            self.resources / definition.filename
            if self.resources is not None
            else resource_path(definition.filename)
        )
        loaded = _load_catalog(path, library_id)
        self._cache[library_id] = loaded
        return loaded

    def active_items(
        self,
        library_id: BuiltinLibraryId,
    ) -> Iterator[BuiltinItem]:
        settings = self.settings(library_id)
        if not settings.enabled:
            return
        disabled = self.storage.list_disabled_builtin_items(library_id.value)
        for item in self.items(library_id):
            if item.item_id in disabled:
                continue
            if (
                library_id == BuiltinLibraryId.AUTOCORRECT_PL
                and settings.profile == "conservative"
                and item.profile != "conservative"
            ):
                continue
            yield item

    def runtime_snippets(self) -> list[Snippet]:
        snippets: list[Snippet] = []
        for library_id in (
            BuiltinLibraryId.EMOJI,
            BuiltinLibraryId.FLAGS,
            BuiltinLibraryId.POSTAL_PL,
        ):
            settings = self.settings(library_id)
            if not settings.enabled:
                continue
            definition = DEFINITIONS_BY_ID[library_id]
            for item in self.active_items(library_id):
                snippets.append(
                    _item_to_snippet(
                        item,
                        abbreviation=settings.prefix + item.slug + definition.suffix,
                        mode=TriggerMode.IMMEDIATE,
                    )
                )
        autocorrect_settings = self.settings(BuiltinLibraryId.AUTOCORRECT_PL)
        if autocorrect_settings.enabled:
            for item in self.active_items(BuiltinLibraryId.AUTOCORRECT_PL):
                snippets.extend(_autocorrection_snippets(item))
        if self.settings(BuiltinLibraryId.CALCULATOR).enabled:
            snippets.append(
                Snippet(
                    id=None,
                    abbreviation=CALCULATOR_PATTERN,
                    expansion="{{calc-match:expression}}",
                    trigger_mode=TriggerMode.IMMEDIATE,
                    enabled=True,
                    category="QuickType",
                    kind=SnippetKind.REGEX,
                    description="Inline calculation",
                    search_terms=("calculation", "calculator", "obliczenie", "kalkulator"),
                    priority=900,
                    source_library=BuiltinLibraryId.CALCULATOR.value,
                    source_item_id="calculator-inline",
                )
            )
        return snippets

    def trigger_for_item(self, item: BuiltinItem) -> str:
        definition = DEFINITIONS_BY_ID[item.library_id]
        if item.library_id == BuiltinLibraryId.AUTOCORRECT_PL:
            return item.slug
        return (
            self.settings(item.library_id).prefix
            + item.slug
            + definition.suffix
        )

    def snippet_for_item(
        self,
        item: BuiltinItem,
        *,
        abbreviation: str | None = None,
    ) -> Snippet:
        return _item_to_snippet(
            item,
            abbreviation=abbreviation or self.trigger_for_item(item),
            mode=(
                TriggerMode.DELIMITER
                if item.library_id == BuiltinLibraryId.AUTOCORRECT_PL
                else TriggerMode.IMMEDIATE
            ),
        )

    def copy_as_snippet(self, item: BuiltinItem) -> Snippet:
        trigger = self.trigger_for_item(item)
        if len(trigger) > 64:
            trigger = trigger[:55] + "-" + item.item_id[-8:]
        return Snippet(
            id=None,
            abbreviation=trigger,
            expansion=item.expansion,
            trigger_mode=(
                TriggerMode.DELIMITER
                if item.library_id == BuiltinLibraryId.AUTOCORRECT_PL
                else TriggerMode.IMMEDIATE
            ),
            category="QuickType",
            description=item.title,
            search_terms=item.keywords[:32],
        )

    def set_item_enabled(
        self,
        item: BuiltinItem,
        *,
        enabled: bool,
    ) -> None:
        self.storage.set_builtin_item_enabled(
            item.library_id.value,
            item.item_id,
            enabled=enabled,
        )

    def item_count(self, library_id: BuiltinLibraryId) -> int:
        if library_id == BuiltinLibraryId.CALCULATOR:
            return 1
        return len(self.items(library_id))

    def _validate_prefix(
        self,
        library_id: BuiltinLibraryId,
        prefix: str,
        *,
        enabled: bool,
    ) -> None:
        definition = DEFINITIONS_BY_ID[library_id]
        if definition.prefix_editable and not prefix:
            raise BuiltinLibrarySettingsError("prefix_required")
        if (
            len(prefix) > 32
            or any(character.isspace() for character in prefix)
            or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
        ):
            raise BuiltinLibrarySettingsError("prefix_invalid")
        for other in LIBRARY_DEFINITIONS:
            if (
                not enabled
                or
                other.library_id == library_id
                or not other.prefix_editable
            ):
                continue
            other_settings = self.settings(other.library_id)
            if (
                other_settings.enabled
                and prefix
                and other_settings.prefix == prefix
            ):
                raise BuiltinLibrarySettingsError("prefix_conflict")


def _load_catalog(
    path: Path,
    expected_library_id: BuiltinLibraryId,
) -> tuple[BuiltinItem, ...]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        document = json.load(source)
    if (
        not isinstance(document, dict)
        or document.get("format") != "quicktype-builtin-library"
        or document.get("version") != 1
        or document.get("library_id") != expected_library_id.value
        or not isinstance(document.get("items"), list)
    ):
        raise ValueError(f"Invalid built-in library resource: {path.name}")
    items: list[BuiltinItem] = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for raw in document["items"]:
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid item in built-in library: {path.name}")
        item_id = raw.get("id")
        title = raw.get("title")
        expansion = raw.get("expansion")
        slug = raw.get("slug")
        keywords = raw.get("keywords")
        profile = raw.get("profile")
        search_text = raw.get("search", "")
        if (
            not isinstance(item_id, str)
            or not item_id
            or not isinstance(title, str)
            or not title
            or not isinstance(expansion, str)
            or not expansion
            or not isinstance(slug, str)
            or not slug
            or not isinstance(profile, str)
            or not profile
            or not isinstance(keywords, list)
            or not all(isinstance(value, str) for value in keywords)
            or not isinstance(search_text, str)
            or item_id in seen_ids
            or slug in seen_slugs
        ):
            raise ValueError(f"Invalid item in built-in library: {path.name}")
        seen_ids.add(item_id)
        seen_slugs.add(slug)
        items.append(
            BuiltinItem(
                item_id=item_id,
                library_id=expected_library_id,
                title=title,
                expansion=expansion,
                slug=slug,
                keywords=tuple(dict.fromkeys(keywords)),
                profile=profile,
                search_text=search_text,
            )
        )
    return tuple(items)


def _item_to_snippet(
    item: BuiltinItem,
    *,
    abbreviation: str,
    mode: TriggerMode,
) -> Snippet:
    return Snippet(
        id=None,
        abbreviation=abbreviation,
        expansion=item.expansion,
        trigger_mode=mode,
        enabled=True,
        category=f"QuickType/{item.library_id.value}",
        description=item.title,
        search_terms=item.keywords[:32],
        source_library=item.library_id.value,
        source_item_id=item.item_id,
    )


def _autocorrection_snippets(item: BuiltinItem) -> list[Snippet]:
    variants = [(item.slug, item.expansion)]
    title_wrong = item.slug[:1].upper() + item.slug[1:]
    title_right = item.expansion[:1].upper() + item.expansion[1:]
    variants.append((title_wrong, title_right))
    if item.slug.isalpha() and item.expansion.replace(" ", "").isalpha():
        variants.append((item.slug.upper(), item.expansion.upper()))
    unique: dict[str, str] = {}
    for abbreviation, expansion in variants:
        unique.setdefault(abbreviation, expansion)
    return [
        _item_to_snippet(
            BuiltinItem(
                item_id=item.item_id,
                library_id=item.library_id,
                title=item.title,
                expansion=expansion,
                slug=item.slug,
                keywords=item.keywords,
                profile=item.profile,
                search_text=item.search_text,
            ),
            abbreviation=abbreviation,
            mode=TriggerMode.DELIMITER,
        )
        for abbreviation, expansion in unique.items()
    ]
