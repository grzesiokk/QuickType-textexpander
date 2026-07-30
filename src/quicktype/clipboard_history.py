from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_CLIPBOARD_HISTORY_ENTRIES = 50
MAX_CLIPBOARD_HISTORY_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ClipboardHistoryItem:
    text: str
    captured_at: datetime


class ClipboardHistory:
    """Bounded, session-only text clipboard history."""

    def __init__(
        self,
        *,
        max_entries: int = MAX_CLIPBOARD_HISTORY_ENTRIES,
        max_bytes: int = MAX_CLIPBOARD_HISTORY_BYTES,
    ) -> None:
        self.max_entries = max(1, max_entries)
        self.max_bytes = max(1, max_bytes)
        self._items: list[ClipboardHistoryItem] = []
        self._total_bytes = 0

    @property
    def items(self) -> tuple[ClipboardHistoryItem, ...]:
        return tuple(self._items)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def add(self, text: str, *, captured_at: datetime | None = None) -> bool:
        if not text or not text.strip():
            return False
        text_bytes = len(text.encode("utf-8"))
        if text_bytes > self.max_bytes:
            return False

        self._remove_text(text)
        self._items.insert(
            0,
            ClipboardHistoryItem(
                text=text,
                captured_at=captured_at or datetime.now(),
            ),
        )
        self._total_bytes += text_bytes
        while (
            len(self._items) > self.max_entries
            or self._total_bytes > self.max_bytes
        ):
            removed = self._items.pop()
            self._total_bytes -= self._size_of(removed.text)
        return True

    def clear(self) -> None:
        self._items.clear()
        self._total_bytes = 0

    def _remove_text(self, text: str) -> None:
        for index, item in enumerate(self._items):
            if item.text == text:
                self._items.pop(index)
                self._total_bytes -= self._size_of(item.text)
                return

    @staticmethod
    def _size_of(text: str) -> int:
        return len(text.encode("utf-8"))
