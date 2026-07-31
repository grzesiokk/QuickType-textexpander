from types import SimpleNamespace

from PySide6.QtCore import QMimeData

from quicktype import clipboard_paste
from quicktype.clipboard_paste import RTF_MIME, RichClipboardCoordinator
from quicktype.models import RenderedContent


def test_rendered_mime_offers_rtf_html_and_plain_fallback() -> None:
    mime = RichClipboardCoordinator._rendered_mime(
        RenderedContent(
            plain_text="Hello",
            html="<p><b>Hello</b></p>",
            rtf=b"{\\rtf1\\b Hello}",
        )
    )

    assert mime.text() == "Hello"
    assert mime.html() == "<p><b>Hello</b></p>"
    assert bytes(mime.data(RTF_MIME).data()).startswith(b"{\\rtf1")


class _FakeClipboard:
    def __init__(self, text: str) -> None:
        self.mime = QMimeData()
        self.mime.setText(text)

    def mimeData(self) -> QMimeData:
        return self.mime

    def setMimeData(self, mime: QMimeData) -> None:
        self.mime = mime


def test_staged_rich_clipboard_restores_when_sequence_is_unchanged(
    monkeypatch,
) -> None:
    clipboard = _FakeClipboard("original")
    coordinator = RichClipboardCoordinator(clipboard)  # type: ignore[arg-type]
    sequence = [42]
    delayed: list[object] = []

    def single_shot(delay: int, callback: object) -> None:
        if delay == 0:
            callback()
        else:
            delayed.append(callback)

    monkeypatch.setattr(clipboard_paste.QTimer, "singleShot", single_shot)
    monkeypatch.setattr(
        clipboard_paste,
        "user32",
        SimpleNamespace(GetClipboardSequenceNumber=lambda: sequence[0]),
    )
    snapshot = coordinator.snapshot()
    ready: list[str] = []

    coordinator.stage_for_paste(
        RenderedContent("new", "<b>new</b>", b"{\\rtf1 new}"),
        snapshot,
        lambda: ready.append(clipboard.mimeData().text()),
    )

    assert ready == ["new"]
    assert clipboard.mimeData().text() == "new"
    delayed[0]()
    assert clipboard.mimeData().text() == "original"


def test_staged_rich_clipboard_preserves_an_external_change(
    monkeypatch,
) -> None:
    clipboard = _FakeClipboard("original")
    coordinator = RichClipboardCoordinator(clipboard)  # type: ignore[arg-type]
    sequence = [7]
    delayed: list[object] = []

    def single_shot(delay: int, callback: object) -> None:
        if delay == 0:
            callback()
        else:
            delayed.append(callback)

    monkeypatch.setattr(clipboard_paste.QTimer, "singleShot", single_shot)
    monkeypatch.setattr(
        clipboard_paste,
        "user32",
        SimpleNamespace(GetClipboardSequenceNumber=lambda: sequence[0]),
    )
    coordinator.stage_for_paste(
        RenderedContent("new"),
        coordinator.snapshot(),
        lambda: None,
    )
    external = QMimeData()
    external.setText("external")
    clipboard.setMimeData(external)
    sequence[0] += 1

    delayed[0]()

    assert clipboard.mimeData().text() == "external"
