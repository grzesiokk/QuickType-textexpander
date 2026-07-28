from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quicktype.i18n import Translator
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage
from quicktype.ui import MainWindow, QuickAccessDialog, SettingsDialog


def test_main_window_loads_selected_snippet_and_switches_language(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            ";sig",
            "Regards",
            TriggerMode.DELIMITER,
            True,
            category="Work",
            favorite=True,
        )
    )
    translator = Translator("pl")
    window = MainWindow(storage, translator, engine_active=True, autostart=False)

    assert window.table.rowCount() == 1
    assert window.table.columnCount() == 6
    assert window.table.item(0, 0).text() == "★"
    assert window.table.item(0, 3).text() == "Work"
    assert window.table.item(0, 5).text() == "0"
    assert window.abbreviation_edit.text() == ";sig"
    assert window.category_combo.currentText() == "Work"
    assert window.favorite_checkbox.isChecked()
    assert window.mode_combo.currentData() == TriggerMode.DELIMITER.value
    assert window.category_filter.findData("Work") >= 0

    window.category_filter.setCurrentIndex(window.category_filter.findData(""))
    assert window.table.isRowHidden(0)
    window.category_filter.setCurrentIndex(window.category_filter.findData("Work"))
    assert not window.table.isRowHidden(0)

    translator.set_language("en")
    window.retranslate()
    assert window.save_button.text() == "Save"
    assert window.mode_combo.currentData() == TriggerMode.DELIMITER.value
    assert window.stats_label.text() == "Not used yet"

    dialog = SettingsDialog(
        translator,
        language="en",
        engine_active=True,
        autostart=False,
        excluded_processes={"KeePass.exe", "Code.exe"},
        database_path=storage.path,
        quick_access_hotkey="ctrl_shift_space",
    )
    assert dialog.selected_excluded_processes == {"KeePass.exe", "Code.exe"}
    assert dialog.selected_quick_access_hotkey == "ctrl_shift_space"
    dialog.deleteLater()
    window.deleteLater()
    application.processEvents()


def test_quick_access_filters_enabled_snippets_and_emits_choice(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    work = storage.save_snippet(
        Snippet(
            None,
            ";mail",
            "hello@example.com",
            TriggerMode.DELIMITER,
            category="Work",
            favorite=True,
        )
    )
    storage.save_snippet(
        Snippet(
            None,
            ";often",
            "Frequently used",
            TriggerMode.IMMEDIATE,
            usage_count=10,
        )
    )
    storage.save_snippet(
        Snippet(None, ";off", "Disabled", TriggerMode.IMMEDIATE, enabled=False)
    )
    dialog = QuickAccessDialog(storage, Translator("en"))
    chosen: list[tuple[Snippet, int]] = []
    dialog.snippet_chosen.connect(lambda snippet, target: chosen.append((snippet, target)))

    dialog.show_for_window(12345)
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 0).text() == "★"
    assert dialog.table.item(0, 1).text() == ";mail"
    assert dialog.table.item(1, 1).text() == ";often"
    assert "Ctrl+Alt+Space" in dialog.hint_label.text()
    dialog.set_hotkey("alt_shift_space")
    assert "Alt+Shift+Space" in dialog.hint_label.text()
    dialog.apply_filter("work")
    assert not dialog.table.isRowHidden(0)
    dialog.choose_current()

    assert chosen == [(work, 12345)]
    dialog.deleteLater()
    application.processEvents()
