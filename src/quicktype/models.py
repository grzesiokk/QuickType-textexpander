from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TriggerMode(StrEnum):
    IMMEDIATE = "immediate"
    DELIMITER = "delimiter"


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
