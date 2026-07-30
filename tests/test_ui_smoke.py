from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QToolBar,
)

from quicktype.auto_backup import AutomaticBackupManager
from quicktype.backup import export_backup, import_backup
from quicktype.backup_catalog import BackupKind
from quicktype.builtin_libraries import (
    BuiltinCatalog,
    BuiltinLibraryId,
)
from quicktype.i18n import Translator
from quicktype.importing import ImportMode, analyze_import
from quicktype.models import Snippet, TriggerMode
from quicktype.recovery import RestoreChangeKind
from quicktype.storage import Storage
from quicktype.ui import (
    BackupRestoreDialog,
    BuiltinLibraryManagerDialog,
    CategoryManagerDialog,
    DataMaintenanceDialog,
    ImportPreviewDialog,
    MainWindow,
    QuickAccessDialog,
    SettingsDialog,
    StatisticsDialog,
    TemplateAssistantDialog,
    TrayController,
    apply_application_style,
    normalize_theme,
)


def test_library_manager_and_menu_keep_large_catalog_readable(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    catalog = BuiltinCatalog(storage)
    manager = BuiltinLibraryManagerDialog(catalog, Translator("en"))
    changed: list[bool] = []
    manager.settings_changed.connect(lambda: changed.append(True))
    emoji_index = manager.library_combo.findData(BuiltinLibraryId.EMOJI.value)
    manager.library_combo.setCurrentIndex(emoji_index)

    assert manager.table.rowCount() <= 300
    manager.enabled_checkbox.setChecked(True)
    manager.save_settings()
    assert changed
    assert catalog.settings(BuiltinLibraryId.EMOJI).enabled

    window = MainWindow(
        storage,
        Translator("en"),
        catalog=catalog,
        engine_active=True,
        autostart=False,
    )
    toolbar = window.findChild(QToolBar)
    assert toolbar is not None
    assert window.file_menu.title() == "File"
    assert window.snippets_menu.title() == "Snippets"
    assert window.libraries_menu.title() == "Libraries"
    assert window.tools_menu.title() == "Tools"
    assert window.quick_search_action in toolbar.actions()
    assert window.import_action not in toolbar.actions()

    manager.deleteLater()
    window.deleteLater()
    application.processEvents()


def test_import_preview_shows_conflicts_and_mode_choice(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "keep", "Current", TriggerMode.IMMEDIATE)
    )
    source = tmp_path / "incoming.json"
    export_backup(
        source,
        [
            Snippet(None, "keep", "Conflict", TriggerMode.IMMEDIATE),
            Snippet(None, "new", "New", TriggerMode.DELIMITER),
        ],
    )
    dialog = ImportPreviewDialog(
        analyze_import(storage, source),
        Translator("en"),
    )

    assert dialog.conflict_table.rowCount() == 1
    assert dialog.conflict_table.columnCount() == 3
    assert dialog.conflict_table.item(0, 0).text() == "keep"
    assert dialog.conflict_table.item(0, 1).text() == "Current"
    assert dialog.conflict_table.item(0, 2).text() == "Conflict"
    assert dialog.import_mode == ImportMode.MERGE
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.import_mode == ImportMode.UPDATE
    dialog.mode_combo.setCurrentIndex(2)
    assert dialog.import_mode == ImportMode.REPLACE

    dialog.deleteLater()
    application.processEvents()


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
        clipboard_capture_hotkey="alt_shift_n",
        backup_retention=37,
        theme="dark",
    )
    assert dialog.selected_excluded_processes == {"KeePass.exe", "Code.exe"}
    assert dialog.selected_quick_access_hotkey == "ctrl_shift_space"
    assert dialog.selected_clipboard_capture_hotkey == "alt_shift_n"
    assert dialog.selected_backup_retention == 37
    assert dialog.selected_theme == "dark"
    assert not dialog.selected_clipboard_history_enabled
    dialog.clipboard_history_checkbox.setChecked(True)
    assert dialog.selected_clipboard_history_enabled

    assistant = TemplateAssistantDialog(translator)
    assistant.type_combo.setCurrentIndex(assistant.type_combo.findData("transform"))
    assistant.transform_combo.setCurrentIndex(
        assistant.transform_combo.findData("default")
    )
    assistant.identifier_edit.setText("var:name")
    assistant.values_edit.setText("Brak|danych")
    assert assistant.token == r"{{default:var:name|Brak\|danych}}"
    assistant.deleteLater()
    dialog.deleteLater()
    window.deleteLater()
    application.processEvents()


def test_themes_and_main_window_layout_state_are_persisted(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.IMMEDIATE)
    )
    first = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
        theme="dark",
    )
    first.resize(1180, 740)
    first.table.setColumnWidth(3, 181)
    first.main_splitter.setSizes([470, 680])
    saved_splitter_sizes = first.main_splitter.sizes()
    first._save_ui_state()

    second = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
        theme="dark",
    )

    assert second.table.columnWidth(3) == 181
    assert second.main_splitter.sizes() == saved_splitter_sizes
    assert second.search_edit.accessibleName() == "Snippet search"
    apply_application_style(application, "dark")
    assert "#1e2027" in application.styleSheet()
    apply_application_style(application, "high_contrast")
    assert "#ffff00" in application.styleSheet()
    assert normalize_theme("unknown") == "light"
    apply_application_style(application, "light")

    first.deleteLater()
    second.deleteLater()
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
    assert dialog.model.rowCount() == 2
    assert dialog.model.index(0, 0).data() == "★ ;mail"
    assert dialog.model.index(0, 2).data() == ";mail"
    assert dialog.model.index(1, 2).data() == ";often"
    assert "Ctrl+Alt+Space" in dialog.hint_label.text()
    dialog.set_hotkey("alt_shift_space")
    assert "Alt+Shift+Space" in dialog.hint_label.text()
    dialog.apply_filter("work")
    assert dialog.model.rowCount() == 1
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


def test_backup_restore_dialog_lists_and_filters_all_backup_types(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    storage.save_snippet(
        Snippet(None, "local", "Local only", TriggerMode.IMMEDIATE)
    )
    storage.save_snippet(
        Snippet(None, "same", "Same text", TriggerMode.IMMEDIATE)
    )
    manager = AutomaticBackupManager(storage)
    backup = manager.create_if_changed()
    assert backup is not None
    manual = manager.directory / (
        "QuickType-manual-20260729-120000-000001.json"
    )
    before_import = manager.directory / (
        "QuickType-before-import-20260729-120001-000001.json"
    )
    before_restore = manager.directory / (
        "QuickType-before-restore-20260729-120002-000001.json"
    )
    for path in (manual, before_restore):
        export_backup(path, storage.list_snippets())
    export_backup(
        before_import,
        [
            Snippet(None, "sig", "Changed regards", TriggerMode.DELIMITER),
            Snippet(None, "new", "New text", TriggerMode.IMMEDIATE),
            Snippet(None, "same", "Same text", TriggerMode.IMMEDIATE),
        ],
    )

    dialog = BackupRestoreDialog(storage, Translator("en"))

    assert dialog.table.rowCount() == 4
    assert dialog.restore_button.isEnabled()
    dialog.type_filter.setCurrentIndex(
        dialog.type_filter.findData(BackupKind.BEFORE_IMPORT.value)
    )
    visible_rows = [
        row
        for row in range(dialog.table.rowCount())
        if not dialog.table.isRowHidden(row)
    ]
    assert len(visible_rows) == 1
    assert dialog.table.item(visible_rows[0], 1).text() == "Before import"
    assert dialog.table.item(visible_rows[0], 2).text() == "3"
    assert dialog.selected_path == before_import
    assert dialog.selected_analysis is not None
    assert dialog.selected_analysis.added == 1
    assert dialog.selected_analysis.updated == 1
    assert dialog.selected_analysis.removed == 1
    assert dialog.selected_analysis.unchanged == 1
    assert dialog.impact_label.text() == (
        "Added: 1 · changed: 1 · removed: 1 · unchanged: 1"
    )
    dialog.change_filter.setCurrentIndex(
        dialog.change_filter.findData(RestoreChangeKind.CHANGED.value)
    )
    assert dialog.change_table.rowCount() == 1
    assert dialog.change_table.item(0, 0).text() == "Changed"
    assert dialog.change_table.item(0, 1).text() == "sig"
    assert dialog.change_table.item(0, 2).text() == "expansion"
    assert dialog.change_table.item(0, 3).text() == "Regards"
    assert dialog.change_table.item(0, 4).text() == "Changed regards"
    dialog.copy_restore_report()
    report = QApplication.clipboard().text()
    assert "QuickType — restore report" in report
    assert "[Added] new" in report
    assert "[Changed] sig — expansion" in report
    assert "[Removed] local" in report
    assert "[Unchanged] same" in report
    dialog.change_filter.setCurrentIndex(0)
    dialog.change_search.setText("Local only")
    assert dialog.change_table.rowCount() == 1
    assert dialog.change_table.item(0, 1).text() == "local"

    dialog.deleteLater()
    application.processEvents()


def test_backup_dialog_refreshes_opens_folder_and_deletes_selected_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    manager = AutomaticBackupManager(storage)
    first = manager.directory / (
        "QuickType-manual-20260729-120000-000001.json"
    )
    second = manager.directory / (
        "QuickType-manual-20260729-120001-000001.json"
    )
    export_backup(first, storage.list_snippets())
    dialog = BackupRestoreDialog(storage, Translator("en"))
    assert dialog.table.rowCount() == 1

    export_backup(second, storage.list_snippets())
    dialog.refresh_button.click()
    assert dialog.table.rowCount() == 2
    assert dialog.action_label.text() == "Backup catalog refreshed."

    opened_urls = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened_urls.append(url) or True,
    )
    dialog.open_folder_button.click()
    assert Path(opened_urls[0].toLocalFile()) == manager.directory

    selected = dialog.selected_path
    assert selected is not None
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.delete_backup_button.click()
    assert not selected.exists()
    assert dialog.table.rowCount() == 1
    assert dialog.action_label.text() == f'Deleted backup “{selected.name}”.'

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


def test_statistics_dialog_shows_total_and_ranking(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            "often",
            "Often",
            TriggerMode.IMMEDIATE,
            usage_count=7,
            last_used_at=datetime(2026, 7, 29, 10, 30),
            category="Work",
        )
    )
    storage.save_snippet(
        Snippet(
            None,
            "sometimes",
            "Sometimes",
            TriggerMode.IMMEDIATE,
            usage_count=2,
            last_used_at=datetime(2026, 7, 28, 8, 0),
        )
    )
    storage.save_snippet(
        Snippet(None, "never", "Never", TriggerMode.IMMEDIATE)
    )

    dialog = StatisticsDialog(storage, Translator("en"))

    assert dialog.summary_label.text() == (
        "Total expansions: 9 · used snippets: 2/3"
    )
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 0).text() == "often"
    assert dialog.table.item(0, 1).text() == "Work"
    assert dialog.table.item(0, 2).text() == "7"
    assert dialog.table.item(0, 3).text() == "2026-07-29 10:30"
    assert dialog.table.item(1, 0).text() == "sometimes"
    assert dialog.reset_selected_button.isEnabled()
    assert dialog.reset_all_button.isEnabled()

    dialog.deleteLater()
    application.processEvents()


def test_main_window_sorts_counts_and_exports_visible_snippets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            "alpha",
            "First",
            TriggerMode.IMMEDIATE,
            usage_count=2,
            category="Work",
        )
    )
    storage.save_snippet(
        Snippet(
            None,
            "beta",
            "Second",
            TriggerMode.IMMEDIATE,
            usage_count=10,
            category="Home",
        )
    )
    storage.save_snippet(
        Snippet(
            None,
            "gamma",
            "Third",
            TriggerMode.IMMEDIATE,
            usage_count=1,
            category="Work",
        )
    )
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )

    assert window.filter_count_label.text() == "Visible: 3 of 3"
    window.table.sortItems(5, Qt.SortOrder.DescendingOrder)
    assert window.table.item(0, 2).text() == "beta"
    assert window.table.item(1, 2).text() == "alpha"
    assert window.table.item(2, 2).text() == "gamma"

    window.category_filter.setCurrentIndex(
        window.category_filter.findData("Work")
    )
    assert window.filter_count_label.text() == "Visible: 2 of 3"
    assert {
        snippet.abbreviation for snippet in window.filtered_snippets()
    } == {"alpha", "gamma"}

    destination = tmp_path / "visible.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), ""),
    )
    window.export_filtered_snippets()
    assert {
        snippet.abbreviation for snippet in import_backup(destination)
    } == {"alpha", "gamma"}

    window.deleteLater()
    application.processEvents()


def test_main_window_bulk_updates_exports_and_deletes_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    for abbreviation in ("alpha", "beta", "gamma"):
        storage.save_snippet(
            Snippet(
                None,
                abbreviation,
                abbreviation.upper(),
                TriggerMode.IMMEDIATE,
            )
        )
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )

    def select(*abbreviations: str) -> None:
        window.table.clearSelection()
        selection = window.table.selectionModel()
        for row in range(window.table.rowCount()):
            if window.table.item(row, 2).text() in abbreviations:
                selection.select(
                    window.table.model().index(row, 2),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    select("alpha", "beta")
    assert len(window.selected_snippet_ids()) == 2
    window.bulk_set_enabled(False)
    current = {
        snippet.abbreviation: snippet for snippet in storage.list_snippets()
    }
    assert not current["alpha"].enabled
    assert not current["beta"].enabled
    assert current["gamma"].enabled

    select("alpha", "beta")
    window.bulk_set_favorite(True)
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Team", True),
    )
    select("alpha", "beta")
    window.bulk_change_category()
    current = {
        snippet.abbreviation: snippet for snippet in storage.list_snippets()
    }
    assert current["alpha"].favorite and current["beta"].favorite
    assert current["alpha"].category == "Team"
    assert current["beta"].category == "Team"

    destination = tmp_path / "selected.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), ""),
    )
    select("alpha", "beta")
    window.export_selected_snippets()
    assert {
        snippet.abbreviation for snippet in import_backup(destination)
    } == {"alpha", "beta"}

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    select("alpha", "beta")
    window.delete_selected()
    assert [
        snippet.abbreviation for snippet in storage.list_snippets()
    ] == ["gamma"]

    window.deleteLater()
    application.processEvents()


def test_data_maintenance_dialog_creates_backup_and_checks_database(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "QuickTypeData" / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(None, "sig", "Regards", TriggerMode.DELIMITER)
    )
    dialog = DataMaintenanceDialog(storage, Translator("en"))

    assert "Snippets: 1" in dialog.summary_label.text()
    assert "JSON backups: 0" in dialog.summary_label.text()
    dialog.create_backup()
    assert "Created backup" in dialog.result_label.text()
    assert "JSON backups: 1" in dialog.summary_label.text()
    dialog.check_database()
    assert dialog.result_label.text() == "The database integrity check passed."
    dialog.copy_diagnostics_button.click()
    assert "QuickType diagnostic report" in QApplication.clipboard().text()
    assert "Regards" not in QApplication.clipboard().text()

    dialog.deleteLater()
    application.processEvents()


def test_keyboard_filter_helpers_and_rendered_copy(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    storage.save_snippet(
        Snippet(
            None,
            "sig",
            "Hello {{clipboard}}{{cursor}}",
            TriggerMode.DELIMITER,
            category="Work",
        )
    )
    storage.save_snippet(
        Snippet(None, "home", "Home", TriggerMode.IMMEDIATE, category="Home")
    )
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )
    window.show()
    application.processEvents()

    window.search_edit.setText("sig")
    window.category_filter.setCurrentIndex(
        window.category_filter.findData("Work")
    )
    assert window.filter_count_label.text() == "Visible: 1 of 2"
    window.clear_filters()
    assert window.search_edit.text() == ""
    assert window.category_filter.currentData() is None
    assert window.filter_count_label.text() == "Visible: 2 of 2"
    assert window.search_edit.hasFocus()

    window._select_id(
        next(snippet.id for snippet in window.snippets if snippet.abbreviation == "sig")
    )
    QApplication.clipboard().setText("CLIP")
    window.copy_preview()
    assert QApplication.clipboard().text() == "Hello CLIP"
    assert window.status_message.text() == "Copied the rendered preview."

    QApplication.clipboard().setText("AGAIN")
    window.copy_current_rendered()
    assert QApplication.clipboard().text() == "Hello AGAIN"
    assert "sig" in window.status_message.text()

    window.close()
    window.deleteLater()
    application.processEvents()


def test_new_snippet_from_clipboard_preserves_multiline_unicode(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "quicktype.sqlite3")
    storage.initialize()
    window = MainWindow(
        storage,
        Translator("en"),
        engine_active=True,
        autostart=False,
    )
    assert window.new_button.defaultAction() is window.new_action
    assert (
        window.new_from_clipboard_action.shortcut().toString()
        == "Ctrl+Shift+N"
    )
    clipboard_text = "Zażółć gęślą jaźń\nSecond line"
    QApplication.clipboard().setText(clipboard_text)

    window.new_snippet_from_clipboard()

    assert window._is_new
    assert window._dirty
    assert window.expansion_edit.toPlainText() == clipboard_text
    assert f"Loaded {len(clipboard_text)} characters" in window.status_message.text()
    window.abbreviation_edit.setText(";clip")
    assert window.save_current()
    saved = storage.list_snippets()
    assert len(saved) == 1
    assert saved[0].abbreviation == ";clip"
    assert saved[0].expansion == clipboard_text

    QApplication.clipboard().clear()
    window.new_snippet_from_clipboard()
    assert window.expansion_edit.toPlainText() == clipboard_text
    assert window.status_message.text() == "The clipboard does not contain text."

    window.deleteLater()
    application.processEvents()


def test_tray_exposes_new_from_clipboard_action() -> None:
    application = QApplication.instance() or QApplication([])
    events: list[str] = []
    tray = TrayController(
        Translator("en"),
        active=True,
        on_open=lambda: events.append("open"),
        on_new_from_clipboard=lambda: events.append("clipboard"),
        on_active=lambda active: events.append(f"active:{active}"),
        on_autostart=lambda enabled: events.append(f"autostart:{enabled}"),
        on_quit=lambda: events.append("quit"),
        autostart=False,
    )

    assert tray.new_from_clipboard_action.text() == "New from clipboard"
    tray.new_from_clipboard_action.trigger()
    assert events == ["clipboard"]

    tray.tray.hide()
    tray.tray.deleteLater()
    application.processEvents()
