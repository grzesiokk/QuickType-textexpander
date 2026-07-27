from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quicktype.i18n import Translator
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage
from quicktype.ui import MainWindow


def test_main_window_loads_selected_snippet_and_switches_language(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, ";sig", "Regards", TriggerMode.DELIMITER, True)
    )
    translator = Translator("pl")
    window = MainWindow(storage, translator, engine_active=True, autostart=False)

    assert window.table.rowCount() == 1
    assert window.abbreviation_edit.text() == ";sig"
    assert window.mode_combo.currentData() == TriggerMode.DELIMITER.value

    translator.set_language("en")
    window.retranslate()
    assert window.save_button.text() == "Save"
    assert window.mode_combo.currentData() == TriggerMode.DELIMITER.value
    window.deleteLater()
    application.processEvents()
