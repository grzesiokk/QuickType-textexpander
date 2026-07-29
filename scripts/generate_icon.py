from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "src" / "quicktype" / "resources" / "quicktype.svg"
    destination = project_root / "build_assets" / "quicktype.ico"
    destination.parent.mkdir(parents=True, exist_ok=True)

    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Cannot read {source}")

    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    if not image.save(str(destination), "ICO"):
        raise RuntimeError(f"Cannot write {destination}")
    print(destination)
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
