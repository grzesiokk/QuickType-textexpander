from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quicktype.auto_backup import AutomaticBackupManager
from quicktype.i18n import Translator
from quicktype.models import Snippet, TriggerMode
from quicktype.storage import Storage
from quicktype.ui import (
    BackupRestoreDialog,
    CategoryManagerDialog,
    MainWindow,
    QuickAccessDialog,
    SettingsDialog,
)


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
            applications=("WINWORD.EXE",),
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
    assert window.applications_edit.text() == "WINWORD.EXE"
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


def test_main_window_duplicates_selected_snippet(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            "x" * 64,
            "Copied text",
            TriggerMode.DELIMITER,
            enabled=False,
            category="Work",
            favorite=True,
            applications=("Code.exe",),
        )
    )
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )

    window.duplicate_current()
    snippets = storage.list_snippets()

    assert len(snippets) == 2
    duplicate = next(snippet for snippet in snippets if snippet.abbreviation != "x" * 64)
    assert duplicate.abbreviation == ("x" * 59) + "_copy"
    assert duplicate.expansion == "Copied text"
    assert duplicate.trigger_mode == TriggerMode.DELIMITER
    assert not duplicate.enabled
    assert duplicate.category == "Work"
    assert duplicate.favorite
    assert duplicate.applications == ("Code.exe",)
    assert duplicate.usage_count == 0
    assert window.abbreviation_edit.text() == duplicate.abbreviation

    window.deleteLater()
    application.processEvents()


def test_main_window_toggles_enabled_and_favorite_state(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    saved = storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )

    window.toggle_current_enabled()
    window.toggle_current_favorite()
    updated = storage.get_snippet(int(saved.id))

    assert updated is not None
    assert not updated.enabled
    assert updated.favorite
    assert window.table.item(0, 0).text() == "★"
    assert window.table.item(0, 1).text() == "○"

    window.deleteLater()
    application.processEvents()


def test_backup_restore_dialog_lists_newest_automatic_backup(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    manager = AutomaticBackupManager(storage)
    backup = manager.create_if_changed()
    assert backup is not None

    dialog = BackupRestoreDialog(manager.directory, Translator("en"))

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "1"
    assert dialog.selected_path == backup
    assert dialog.restore_button.isEnabled()

    dialog.deleteLater()
    application.processEvents()


def test_category_manager_lists_category_counts(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "one", "1", TriggerMode.IMMEDIATE, category="Work")
    )
    storage.save_snippet(
        Snippet(None, "two", "2", TriggerMode.IMMEDIATE, category="Work")
    )
    storage.save_snippet(
        Snippet(None, "three", "3", TriggerMode.IMMEDIATE, category="Home")
    )

    dialog = CategoryManagerDialog(storage, Translator("en"))

    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 0).text() == "Home"
    assert dialog.table.item(0, 1).text() == "1"
    assert dialog.table.item(1, 0).text() == "Work"
    assert dialog.table.item(1, 1).text() == "2"
    assert dialog.selected_category == "Home"
    assert dialog.rename_button.isEnabled()
    assert dialog.clear_button.isEnabled()

    dialog.deleteLater()
    application.processEvents()
