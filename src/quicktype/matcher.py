from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

import regex

from .models import Snippet, SnippetKind, TriggerMode

TEXT_SEPARATORS = frozenset(" \t\n\r.,;:!?)]}")
REGEX_TIMEOUT_SECONDS = 0.005


@dataclass(frozen=True, slots=True)
class ExpansionAction:
    snippet: Snippet
    delete_count: int
    success_suffix_text: str = ""
    success_suffix_vk: int | None = None
    fallback_text: str = ""
    fallback_vk: int | None = None
    match_groups: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _SnippetMatch:
    snippet: Snippet
    matched_length: int
    match_groups: tuple[tuple[str, str], ...] = ()


class SnippetMatcher:
    """Maintains a short in-memory typing buffer and detects snippet matches."""

    def __init__(self, snippets: list[Snippet] | None = None, max_buffer: int = 256) -> None:
        self._lock = RLock()
        self._max_buffer = max_buffer
        self._buffer = ""
        self._snippets: tuple[Snippet, ...] = ()
        self._suffix_index: dict[
            tuple[TriggerMode, str],
            tuple[Snippet, ...],
        ] = {}
        self._regex_index: dict[
            TriggerMode,
            tuple[tuple[Snippet, regex.Pattern[str]], ...],
        ] = {}
        self.replace_snippets(snippets or [])

    @property
    def buffer(self) -> str:
        with self._lock:
            return self._buffer

    def replace_snippets(self, snippets: list[Snippet]) -> None:
        with self._lock:
            active = [
                snippet
                for snippet in snippets
                if snippet.enabled and snippet.kind == SnippetKind.LITERAL
            ]
            self._snippets = tuple(
                sorted(active, key=lambda snippet: len(snippet.abbreviation), reverse=True)
            )
            index: dict[tuple[TriggerMode, str], list[Snippet]] = {}
            for snippet in self._snippets:
                key = (
                    snippet.trigger_mode,
                    snippet.abbreviation[-1],
                )
                index.setdefault(key, []).append(snippet)
            self._suffix_index = {
                key: tuple(values) for key, values in index.items()
            }
            regex_index: dict[
                TriggerMode,
                list[tuple[Snippet, regex.Pattern[str]]],
            ] = {}
            regex_snippets = sorted(
                (
                    snippet
                    for snippet in snippets
                    if snippet.enabled and snippet.kind == SnippetKind.REGEX
                ),
                key=lambda snippet: (-snippet.priority, snippet.id or 0),
            )
            for snippet in regex_snippets:
                try:
                    compiled = regex.compile(
                        f"(?:{snippet.abbreviation})$",
                        flags=regex.VERSION1,
                    )
                except regex.error:
                    continue
                regex_index.setdefault(snippet.trigger_mode, []).append(
                    (snippet, compiled)
                )
            self._regex_index = {
                mode: tuple(values)
                for mode, values in regex_index.items()
            }
            self._buffer = ""

    def clear(self) -> None:
        with self._lock:
            self._buffer = ""

    def backspace(self) -> None:
        with self._lock:
            self._buffer = self._buffer[:-1]

    def feed_character(self, character: str) -> ExpansionAction | None:
        if len(character) != 1:
            raise ValueError("feed_character expects one character")

        with self._lock:
            if character in TEXT_SEPARATORS:
                match = self._find_match(TriggerMode.DELIMITER, self._buffer)
                if match is not None:
                    self._buffer = ""
                    return ExpansionAction(
                        snippet=match.snippet,
                        delete_count=match.matched_length,
                        success_suffix_text=character,
                        fallback_text=character,
                        match_groups=match.match_groups,
                    )

            candidate = (self._buffer + character)[-self._max_buffer :]
            match = self._find_match(TriggerMode.IMMEDIATE, candidate)
            if match is not None:
                self._buffer = ""
                return ExpansionAction(
                    snippet=match.snippet,
                    delete_count=max(0, match.matched_length - 1),
                    fallback_text=character,
                    match_groups=match.match_groups,
                )

            if character in "\t\n\r ":
                self._buffer = ""
            else:
                self._buffer = candidate
            return None

    def feed_special_separator(self, virtual_key: int) -> ExpansionAction | None:
        with self._lock:
            match = self._find_match(TriggerMode.DELIMITER, self._buffer)
            self._buffer = ""
            if match is None:
                return None
            return ExpansionAction(
                snippet=match.snippet,
                delete_count=match.matched_length,
                success_suffix_vk=virtual_key,
                fallback_vk=virtual_key,
                match_groups=match.match_groups,
            )

    def _find_match(
        self,
        trigger_mode: TriggerMode,
        candidate: str,
    ) -> _SnippetMatch | None:
        if not candidate:
            return None
        for snippet in self._suffix_index.get(
            (trigger_mode, candidate[-1]),
            (),
        ):
            abbreviation = snippet.abbreviation
            if not candidate.endswith(abbreviation):
                continue
            start = len(candidate) - len(abbreviation)
            if start == 0 or not _is_word_character(candidate[start - 1]):
                return _SnippetMatch(snippet, len(abbreviation))
        for snippet, pattern in self._regex_index.get(trigger_mode, ()):
            try:
                matched = pattern.search(
                    candidate,
                    timeout=REGEX_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                continue
            if matched is None or not matched.group(0):
                continue
            groups: dict[str, str] = {
                str(index): (matched.group(index) or "")
                for index in range(0, len(matched.groups()) + 1)
            }
            groups.update(
                {
                    name: value or ""
                    for name, value in matched.groupdict().items()
                }
            )
            return _SnippetMatch(
                snippet=snippet,
                matched_length=len(matched.group(0)),
                match_groups=tuple(groups.items()),
            )
        return None


def _is_word_character(character: str) -> bool:
    return character.isalnum() or character == "_"
