from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .models import Snippet, TriggerMode

TEXT_SEPARATORS = frozenset(" \t\n\r.,;:!?)]}")


@dataclass(frozen=True, slots=True)
class ExpansionAction:
    snippet: Snippet
    delete_count: int
    success_suffix_text: str = ""
    success_suffix_vk: int | None = None
    fallback_text: str = ""
    fallback_vk: int | None = None


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
        self.replace_snippets(snippets or [])

    @property
    def buffer(self) -> str:
        with self._lock:
            return self._buffer

    def replace_snippets(self, snippets: list[Snippet]) -> None:
        with self._lock:
            active = [snippet for snippet in snippets if snippet.enabled]
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
                        snippet=match,
                        delete_count=len(match.abbreviation),
                        success_suffix_text=character,
                        fallback_text=character,
                    )

            candidate = (self._buffer + character)[-self._max_buffer :]
            match = self._find_match(TriggerMode.IMMEDIATE, candidate)
            if match is not None:
                self._buffer = ""
                return ExpansionAction(
                    snippet=match,
                    delete_count=max(0, len(match.abbreviation) - 1),
                    fallback_text=character,
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
                snippet=match,
                delete_count=len(match.abbreviation),
                success_suffix_vk=virtual_key,
                fallback_vk=virtual_key,
            )

    def _find_match(self, trigger_mode: TriggerMode, candidate: str) -> Snippet | None:
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
                return snippet
        return None


def _is_word_character(character: str) -> bool:
    return character.isalnum() or character == "_"
