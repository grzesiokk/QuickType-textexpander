from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import regex


class TriggerMode(StrEnum):
    IMMEDIATE = "immediate"
    DELIMITER = "delimiter"


class SnippetKind(StrEnum):
    LITERAL = "literal"
    REGEX = "regex"


class SnippetContentFormat(StrEnum):
    PLAIN = "plain"
    RICH = "rich"


@dataclass(frozen=True, slots=True)
class SnippetAsset:
    asset_id: str
    mime_type: str
    data: bytes
    original_name: str
    width: int
    height: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Snippet:
    id: int | None
    abbreviation: str
    expansion: str
    trigger_mode: TriggerMode
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None
    category: str = ""
    favorite: bool = False
    applications: tuple[str, ...] = ()
    kind: SnippetKind = SnippetKind.LITERAL
    description: str = ""
    search_terms: tuple[str, ...] = ()
    priority: int = 0
    source_library: str = ""
    source_item_id: str = ""
    content_format: SnippetContentFormat = SnippetContentFormat.PLAIN
    rich_html: str = ""


@dataclass(frozen=True, slots=True)
class SnippetBundle:
    snippet: Snippet
    assets: tuple[SnippetAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedContent:
    plain_text: str
    html: str = ""
    rtf: bytes = b""
    cursor_from_end: int = 0
    cursor_present: bool = False


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str


def validate_abbreviation(value: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not value:
        issues.append(ValidationIssue("required", "Abbreviation is required."))
    if len(value) > 64:
        issues.append(ValidationIssue("too_long", "Abbreviation must have at most 64 characters."))
    if any(character.isspace() for character in value):
        issues.append(ValidationIssue("whitespace", "Abbreviation cannot contain whitespace."))
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        issues.append(ValidationIssue("control", "Abbreviation cannot contain control characters."))
    return issues


def validate_regex_pattern(value: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not value:
        issues.append(ValidationIssue("required", "Regular expression is required."))
    if len(value) > 512:
        issues.append(
            ValidationIssue("too_long", "Regular expression must have at most 512 characters.")
        )
    if any(ord(character) == 0 for character in value):
        issues.append(
            ValidationIssue("control", "Regular expression cannot contain null characters.")
        )
    if value and len(value) <= 512:
        try:
            regex.compile(value, flags=regex.VERSION1)
        except regex.error as error:
            issues.append(ValidationIssue("invalid_regex", str(error)))
    return issues


def validate_snippet_trigger(value: str, kind: SnippetKind) -> list[ValidationIssue]:
    if kind == SnippetKind.REGEX:
        return validate_regex_pattern(value)
    return validate_abbreviation(value)


def validate_category(value: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(value) > 64:
        issues.append(ValidationIssue("too_long", "Category must have at most 64 characters."))
    if any(
        character in "\r\n" or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        issues.append(ValidationIssue("control", "Category cannot contain control characters."))
    return issues


def validate_description(value: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(value) > 500:
        issues.append(
            ValidationIssue("too_long", "Description must have at most 500 characters.")
        )
    if any(ord(character) == 0 for character in value):
        issues.append(ValidationIssue("control", "Description cannot contain null characters."))
    return issues


def normalize_search_terms(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for raw_value in values:
        value = raw_value.strip()
        if not value:
            continue
        if (
            len(value) > 80
            or any(
                character in "\r\n" or ord(character) < 32 or ord(character) == 127
                for character in value
            )
        ):
            raise ValueError("Search terms must be single-line values up to 80 characters.")
        normalized.setdefault(value.casefold(), value)
    if len(normalized) > 32:
        raise ValueError("A snippet can have at most 32 search terms.")
    return tuple(sorted(normalized.values(), key=str.casefold))


def normalize_priority(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Priority must be an integer.")
    if not -1000 <= value <= 1000:
        raise ValueError("Priority must be between -1000 and 1000.")
    return value


def normalize_applications(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for raw_value in values:
        value = raw_value.strip()
        if not value:
            continue
        if (
            len(value) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or "/" in value
            or "\\" in value
        ):
            raise ValueError("Application names must be executable file names.")
        normalized.setdefault(value.casefold(), value)
    if len(normalized) > 32:
        raise ValueError("A snippet can target at most 32 applications.")
    return tuple(sorted(normalized.values(), key=str.casefold))


def snippet_applies_to_process(snippet: Snippet, process_name: str) -> bool:
    if not snippet.applications:
        return True
    target = process_name.casefold()
    return any(application.casefold() == target for application in snippet.applications)


def next_copy_abbreviation(
    abbreviation: str,
    existing_abbreviations: set[str],
) -> str:
    for number in range(1, 100_000):
        suffix = "_copy" if number == 1 else f"_copy{number}"
        candidate = f"{abbreviation[: 64 - len(suffix)]}{suffix}"
        if candidate not in existing_abbreviations:
            return candidate
    raise ValueError("A unique copy abbreviation could not be generated.")
