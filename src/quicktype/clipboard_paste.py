from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QByteArray, QMimeData, QTimer
from PySide6.QtGui import QClipboard, QImage

from .models import RenderedContent

RTF_MIME = 'application/x-qt-windows-mime;value="Rich Text Format"'
RESTORE_DELAY_MS = 750

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = ctypes.c_uint


@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    formats: tuple[tuple[str, bytes], ...]
    image: QImage | None
    text: str

    @classmethod
    def capture(cls, clipboard: QClipboard) -> ClipboardSnapshot:
        mime = clipboard.mimeData()
        if mime is None:
            return cls(formats=(), image=None, text="")
        formats: list[tuple[str, bytes]] = []
        for mime_type in mime.formats():
            try:
                formats.append((mime_type, bytes(mime.data(mime_type).data())))
            except (RuntimeError, TypeError):
                continue
        image_data = mime.imageData() if mime.hasImage() else None
        image = QImage(image_data) if isinstance(image_data, QImage) else None
        return cls(
            formats=tuple(formats),
            image=image.copy() if image is not None else None,
            text=mime.text() if mime.hasText() else "",
        )

    def to_mime_data(self) -> QMimeData:
        mime = QMimeData()
        for mime_type, data in self.formats:
            mime.setData(mime_type, QByteArray(data))
        if self.image is not None and not self.image.isNull():
            mime.setImageData(self.image)
        if self.text and not mime.hasText():
            mime.setText(self.text)
        return mime


class RichClipboardCoordinator:
    def __init__(self, clipboard: QClipboard) -> None:
        self.clipboard = clipboard
        self._internal_change = False
        self._generation = 0

    @property
    def internal_change(self) -> bool:
        return self._internal_change

    def snapshot(self) -> ClipboardSnapshot:
        return ClipboardSnapshot.capture(self.clipboard)

    def stage_for_paste(
        self,
        rendered: RenderedContent,
        snapshot: ClipboardSnapshot,
        on_ready: Callable[[], None],
    ) -> None:
        self._generation += 1
        generation = self._generation
        mime = self._rendered_mime(rendered)
        self._set_mime_data(mime)
        staged_sequence = int(user32.GetClipboardSequenceNumber())
        try:
            on_ready()
        except Exception:
            self._restore(snapshot)
            raise
        QTimer.singleShot(
            RESTORE_DELAY_MS,
            lambda: self._restore_if_unchanged(
                generation,
                staged_sequence,
                snapshot,
            ),
        )

    def copy_rendered(self, rendered: RenderedContent) -> None:
        self._generation += 1
        self._set_mime_data(self._rendered_mime(rendered))

    def _restore_if_unchanged(
        self,
        generation: int,
        staged_sequence: int,
        snapshot: ClipboardSnapshot,
    ) -> None:
        if generation != self._generation:
            return
        if int(user32.GetClipboardSequenceNumber()) != staged_sequence:
            return
        self._restore(snapshot)

    def _restore(self, snapshot: ClipboardSnapshot) -> None:
        self._set_mime_data(snapshot.to_mime_data())

    def _set_mime_data(self, mime: QMimeData) -> None:
        self._internal_change = True
        try:
            self.clipboard.setMimeData(mime)
        finally:
            QTimer.singleShot(0, self._finish_internal_change)

    def _finish_internal_change(self) -> None:
        self._internal_change = False

    @staticmethod
    def _rendered_mime(rendered: RenderedContent) -> QMimeData:
        mime = QMimeData()
        if rendered.rtf:
            mime.setData(RTF_MIME, QByteArray(rendered.rtf + b"\0"))
        if rendered.html:
            mime.setHtml(rendered.html)
        mime.setText(rendered.plain_text)
        return mime
