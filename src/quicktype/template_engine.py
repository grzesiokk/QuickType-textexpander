from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

TOKEN_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class TemplateIssue:
    code: str
    token: str
    message: str


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    text: str
    cursor_from_end: int
    issues: tuple[TemplateIssue, ...]


def inspect_template(template: str) -> tuple[TemplateIssue, ...]:
    return render_template(template, clipboard_text="", now=datetime(2000, 1, 2, 3, 4, 5)).issues


def render_template(
    template: str,
    *,
    clipboard_text: str = "",
    now: datetime | None = None,
    clipboard_provider: Callable[[], str] | None = None,
) -> RenderedTemplate:
    current = now or datetime.now()
    issues: list[TemplateIssue] = []
    cursor_positions: list[int] = []
    output: list[str] = []
    output_length = 0

    def append(value: str) -> None:
        nonlocal output_length
        output.append(value)
        output_length += len(value)

    position = 0
    for match in TOKEN_RE.finditer(template):
        append(template[position : match.start()])
        raw_token = match.group(1)
        token = raw_token.strip()
        replacement: str | None = None

        if token == "date":
            replacement = current.strftime("%d.%m.%Y")
        elif token.startswith("date:"):
            pattern = token[5:]
            if pattern:
                try:
                    replacement = current.strftime(pattern)
                except (ValueError, TypeError) as error:
                    issues.append(TemplateIssue("invalid_format", token, str(error)))
            else:
                issues.append(TemplateIssue("empty_format", token, "Date format cannot be empty."))
        elif token == "time":
            replacement = current.strftime("%H:%M")
        elif token.startswith("time:"):
            pattern = token[5:]
            if pattern:
                try:
                    replacement = current.strftime(pattern)
                except (ValueError, TypeError) as error:
                    issues.append(TemplateIssue("invalid_format", token, str(error)))
            else:
                issues.append(TemplateIssue("empty_format", token, "Time format cannot be empty."))
        elif token == "clipboard":
            if clipboard_provider is not None:
                try:
                    replacement = clipboard_provider() or ""
                except Exception as error:  # Clipboard access can transiently fail on Windows.
                    issues.append(TemplateIssue("clipboard_error", token, str(error)))
                    replacement = ""
            else:
                replacement = clipboard_text
        elif token == "cursor":
            if cursor_positions:
                issues.append(
                    TemplateIssue("multiple_cursor", token, "The cursor marker can occur only once.")
                )
                replacement = None
            else:
                cursor_positions.append(output_length)
                replacement = ""
        else:
            issues.append(TemplateIssue("unknown_token", token, f"Unknown variable: {token}"))

        if replacement is None:
            append(match.group(0))
        else:
            append(replacement)
        position = match.end()

    append(template[position:])
    text = "".join(output)
    cursor_from_end = len(text) - cursor_positions[0] if cursor_positions else 0
    return RenderedTemplate(text=text, cursor_from_end=cursor_from_end, issues=tuple(issues))
