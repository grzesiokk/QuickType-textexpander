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
