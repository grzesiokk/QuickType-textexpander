from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from .auto_backup import (
    AutomaticBackupManager,
    normalize_backup_retention,
)
from .builtin_libraries import BuiltinCatalog
from .constants import APP_NAME, APP_VERSION, database_path, resource_path
from .hook import KeyboardHookEngine
from .hotkeys import (
    normalize_clipboard_capture_hotkey,
    normalize_quick_access_hotkey,
)
from .i18n import Translator
from .matcher import ExpansionAction
from .models import Snippet
from .recovery import latest_recovery_backup, recover_database
from .single_instance import SingleInstance
from .storage import Storage
from .template_engine import collect_form_fields
from .ui import (
    EngineSignals,
    ExpansionFormDialog,
    MainWindow,
    QuickAccessDialog,
    TrayController,
    apply_application_style,
    normalize_theme,
)
from .windows_platform import (
    current_foreground_window,
    is_autostart_enabled,
    repair_autostart_if_enabled,
    restore_foreground_window,
    set_autostart,
)


class QuickTypeController:
    def __init__(self, application: QApplication, *, start_minimized: bool) -> None:
        self.application = application
        self.storage = Storage(database_path())
        self.storage.initialize()
        language = self.storage.get_setting("language") or self._system_language()
        self.translator = Translator(language)
        active = self.storage.get_setting("engine_active", "1") != "0"
        excluded_processes = self._decode_excluded_processes(
            self.storage.get_setting("excluded_processes", "[]")
        )
        quick_access_hotkey = normalize_quick_access_hotkey(
            self.storage.get_setting("quick_access_hotkey")
        )
        clipboard_capture_hotkey = normalize_clipboard_capture_hotkey(
            self.storage.get_setting("clipboard_capture_hotkey")
        )
        repair_autostart_if_enabled()
        autostart = is_autostart_enabled()
        automatic_backups = self.storage.get_setting("automatic_backups", "1") != "0"
        backup_retention = normalize_backup_retention(
            self.storage.get_setting("backup_retention", "20")
        )
        theme = normalize_theme(
            self.storage.get_setting("theme", "light")
        )
        apply_application_style(self.application, theme)
        self.backups = AutomaticBackupManager(
            self.storage,
            retention=backup_retention,
        )

        self.signals = EngineSignals()
        self.catalog = BuiltinCatalog(self.storage)
        self.engine = KeyboardHookEngine(
            self.storage.list_snippets() + self.catalog.runtime_snippets(),
            on_expansion=self.signals.expanded.emit,
            on_error=self.signals.error.emit,
            on_quick_access=self.signals.quick_access.emit,
            on_clipboard_capture=self.signals.clipboard_capture.emit,
            on_form_request=self.signals.form_requested.emit,
            quick_access_hotkey=quick_access_hotkey,
            clipboard_capture_hotkey=clipboard_capture_hotkey,
            excluded_processes=excluded_processes,
        )
        self.window = MainWindow(
            self.storage,
            self.translator,
            catalog=self.catalog,
            engine_active=active,
            autostart=autostart,
            automatic_backups=automatic_backups,
            backup_retention=backup_retention,
            theme=theme,
            excluded_processes=excluded_processes,
            quick_access_hotkey=quick_access_hotkey,
            clipboard_capture_hotkey=clipboard_capture_hotkey,
        )
        self.quick_access = QuickAccessDialog(
            self.storage,
            self.translator,
            self.catalog,
            quick_access_hotkey=quick_access_hotkey,
        )
        self.tray = TrayController(
            self.translator,
            active=active,
            on_open=self.window.show_and_activate,
            on_new_from_clipboard=self.new_snippet_from_clipboard,
            on_active=self.set_active,
            on_autostart=self.set_autostart_enabled,
            on_quit=self.quit,
            autostart=autostart,
        )
        self.instance = SingleInstance(self.window.show_and_activate)
        self.instance.listen()

        self.window.language_change_requested.connect(self.set_language)
        self.window.active_change_requested.connect(self.set_active)
        self.window.autostart_change_requested.connect(self.set_autostart_enabled)
        self.window.automatic_backups_change_requested.connect(
            self.set_automatic_backups
        )
        self.window.backup_retention_change_requested.connect(
            self.set_backup_retention
        )
        self.window.theme_change_requested.connect(self.set_theme)
        self.window.excluded_processes_change_requested.connect(
            self.set_excluded_processes
        )
        self.window.quick_access_hotkey_change_requested.connect(
            self.set_quick_access_hotkey
        )
        self.window.clipboard_capture_hotkey_change_requested.connect(
            self.set_clipboard_capture_hotkey
        )
        self.window.snippets_changed.connect(self.refresh_snippets)
        self.window.builtin_libraries_changed.connect(self.refresh_snippets)
        self.window.quick_search_requested.connect(self.open_quick_search)
        self.window.quit_requested.connect(self.quit)
        self.signals.expanded.connect(self._on_expanded)
        self.signals.error.connect(self._on_engine_error)
        self.signals.quick_access.connect(self.quick_access.show_for_window)
        self.signals.clipboard_capture.connect(
            self.new_snippet_from_clipboard
        )
        self.signals.form_requested.connect(self._on_form_requested)
        self.quick_access.snippet_chosen.connect(self._quick_access_chosen)
        self.application.aboutToQuit.connect(self.shutdown)
        self._form_values: dict[tuple[str, str], str] = {}

        self.engine.set_active(active)
        self.engine.start()
        if automatic_backups and self.storage.list_snippets():
            self._create_automatic_backup()

        first_run = self.storage.get_setting("first_run_done", "0") != "1"
        if first_run:
            self.storage.set_setting("first_run_done", "1")
            self.window.status_message.setText(self.translator("first_run"))
        if not start_minimized:
            self.window.show()

    @staticmethod
    def _system_language() -> str:
        return "pl" if QLocale.system().language() == QLocale.Language.Polish else "en"

    @staticmethod
    def _decode_excluded_processes(value: str | None) -> set[str]:
        try:
            items = json.loads(value or "[]")
        except json.JSONDecodeError:
            return set()
        if not isinstance(items, list):
            return set()
        return {
            Path(item).name
            for item in items
            if isinstance(item, str) and item.strip()
        }

    def set_language(self, language: str) -> None:
        self.translator.set_language(language)
        self.storage.set_setting("language", language)
        self.window.retranslate()
        self.quick_access.retranslate()
        self.tray.retranslate()
        self.window.status_message.setText(self.translator("language_restart_not_required"))

    def new_snippet_from_clipboard(self) -> None:
        self.window.show_and_activate()
        self.window.new_snippet_from_clipboard()

    def open_quick_search(self) -> None:
        self.window.hide()
        QTimer.singleShot(180, self._open_quick_search_for_foreground)

    def _open_quick_search_for_foreground(self) -> None:
        target_window = current_foreground_window()
        if target_window:
            self.quick_access.show_for_window(target_window)

    def set_active(self, active: bool) -> None:
        self.engine.set_active(active)
        self.storage.set_setting("engine_active", "1" if active else "0")
        self.window.set_engine_active(active)
        self.tray.set_active(active)

    def set_autostart_enabled(self, enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except OSError as error:
            self.window.set_autostart(is_autostart_enabled())
            self.tray.set_autostart(is_autostart_enabled())
            text = self.translator("autostart_error", error=str(error))
            self.tray.show_error(text)
            QMessageBox.warning(self.window, APP_NAME, text)
            return
        self.window.set_autostart(enabled)
        self.tray.set_autostart(enabled)

    def refresh_snippets(self) -> None:
        snippets = self.storage.list_snippets()
        self.engine.replace_snippets(snippets + self.catalog.runtime_snippets())
        if self.window.automatic_backups:
            self._create_automatic_backup(snippets)

    def set_automatic_backups(self, enabled: bool) -> None:
        self.storage.set_setting("automatic_backups", "1" if enabled else "0")
        self.window.set_automatic_backups(enabled)
        if enabled:
            self._create_automatic_backup()

    def set_backup_retention(self, retention: int) -> None:
        normalized = normalize_backup_retention(retention)
        self.storage.set_setting("backup_retention", str(normalized))
        self.backups.set_retention(normalized)
        self.window.set_backup_retention(normalized)

    def set_theme(self, theme: str) -> None:
        normalized = normalize_theme(theme)
        self.storage.set_setting("theme", normalized)
        apply_application_style(self.application, normalized)
        self.window.set_theme(normalized)

    def _create_automatic_backup(
        self,
        snippets: list[Snippet] | None = None,
    ) -> None:
        try:
            self.backups.create_if_changed(snippets)
        except OSError as error:
            text = self.translator("automatic_backup_error", error=str(error))
            self.window.status_message.setText(text)
            self.tray.show_error(text)

    def set_excluded_processes(self, processes: object) -> None:
        if not isinstance(processes, set):
            return
        normalized = {
            Path(process).name for process in processes if isinstance(process, str) and process
        }
        self.storage.set_setting(
            "excluded_processes",
            json.dumps(sorted(normalized, key=str.casefold), ensure_ascii=False),
        )
        self.engine.set_excluded_processes(normalized)
        self.window.set_excluded_processes(normalized)

    def set_quick_access_hotkey(self, hotkey: str) -> None:
        normalized = normalize_quick_access_hotkey(hotkey)
        self.storage.set_setting("quick_access_hotkey", normalized)
        self.engine.set_quick_access_hotkey(normalized)
        self.window.set_quick_access_hotkey(normalized)
        self.quick_access.set_hotkey(normalized)

    def set_clipboard_capture_hotkey(self, hotkey: str) -> None:
        normalized = normalize_clipboard_capture_hotkey(hotkey)
        self.storage.set_setting(
            "clipboard_capture_hotkey",
            normalized,
        )
        self.engine.set_clipboard_capture_hotkey(normalized)
        self.window.set_clipboard_capture_hotkey(normalized)

    def _on_expanded(self, snippet: object) -> None:
        abbreviation = getattr(snippet, "abbreviation", "")
        snippet_id = getattr(snippet, "id", None)
        source_library = getattr(snippet, "source_library", "")
        source_item_id = getattr(snippet, "source_item_id", "")
        if source_library and source_item_id:
            self.storage.record_builtin_expansion(
                str(source_library),
                str(source_item_id),
            )
        elif isinstance(snippet_id, int):
            updated = self.storage.record_expansion(snippet_id)
            if updated is not None:
                self.window.refresh_usage(updated)
        self.window.status_message.setText(self.translator("expanded", abbr=abbreviation))

    def _on_engine_error(self, error: str) -> None:
        text = self.translator("engine_error", error=error)
        self.window.status_message.setText(text)
        self.tray.show_error(text)

    def _quick_access_chosen(self, snippet: object, target_window: int) -> None:
        if not isinstance(snippet, Snippet) or not restore_foreground_window(target_window):
            self.window.status_message.setText(self.translator("quick_access_target_error"))
            return
        QTimer.singleShot(
            120,
            lambda: self._insert_quick_access_snippet(snippet, target_window),
        )

    def _on_form_requested(self, action: object, target_window: int) -> None:
        if not isinstance(action, ExpansionAction):
            return
        provider = self.engine.available_snippet_provider(target_window)
        fields, issues = collect_form_fields(
            action.snippet.expansion,
            snippet_provider=provider,
        )
        if issues:
            self._cancel_form_action(action, target_window)
            self._on_engine_error(issues[0].message)
            return
        require_active = bool(
            action.delete_count
            or action.fallback_text
            or action.fallback_vk is not None
        )
        if not fields:
            self.engine.expand_action(
                action,
                target_window,
                require_active=require_active,
            )
            return
        remembered = {
            field.identifier: self._form_values.get(
                (action.snippet.abbreviation, field.identifier),
                field.default,
            )
            for field in fields
        }
        dialog = ExpansionFormDialog(
            self.translator,
            fields,
            remembered_values=remembered,
            parent=self.window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._cancel_form_action(action, target_window)
            return
        values = dialog.values
        for identifier, value in values.items():
            self._form_values[(action.snippet.abbreviation, identifier)] = value
        if not restore_foreground_window(target_window):
            self._cancel_form_action(action, target_window)
            self.window.status_message.setText(
                self.translator("quick_access_target_error")
            )
            return
        QTimer.singleShot(
            120,
            lambda: self.engine.expand_action(
                action,
                target_window,
                values=values,
                require_active=require_active,
            ),
        )

    def _cancel_form_action(
        self,
        action: ExpansionAction,
        target_window: int,
    ) -> None:
        if restore_foreground_window(target_window):
            QTimer.singleShot(120, lambda: self.engine.cancel_action(action))

    def _insert_quick_access_snippet(self, snippet: object, target_window: int) -> None:
        if not isinstance(snippet, Snippet):
            return
        if not self.engine.expand_directly(snippet, target_window):
            self.window.status_message.setText(self.translator("quick_access_target_error"))

    def quit(self) -> None:
        if self.window.prepare_quit():
            self.application.quit()

    def shutdown(self) -> None:
        self.engine.stop()


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--minimized", action="store_true")
    options, _unknown = parser.parse_known_args(argv)

    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName(APP_NAME)
    application.setQuitOnLastWindowClosed(False)
    application.setWindowIcon(QIcon(str(resource_path("quicktype.svg"))))
    apply_application_style(application)

    if SingleInstance.notify_existing():
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "Windows system tray is unavailable.")
        return 1

    startup_translator = Translator(
        "pl"
        if QLocale.system().language() == QLocale.Language.Polish
        else "en"
    )
    if not _prepare_database(startup_translator):
        return 1

    try:
        controller = QuickTypeController(application, start_minimized=options.minimized)
    except Exception as error:
        translator = Translator("pl" if QLocale.system().language() == QLocale.Language.Polish else "en")
        QMessageBox.critical(
            None,
            translator("data_error_title"),
            translator("data_error_text", error=str(error)),
        )
        return 1

    # Keep the Python controller alive for the entire Qt event loop.
    application.quicktype_controller = controller  # type: ignore[attr-defined]
    return application.exec()


def _prepare_database(translator: Translator) -> bool:
    path = database_path()
    storage = Storage(path)
    try:
        storage.initialize()
        valid, _details = storage.check_integrity()
    except (OSError, sqlite3.Error):
        valid = False
    if valid:
        return True

    source = latest_recovery_backup(path)
    if source is not None:
        message = translator(
            "startup_recovery_text",
            file=source.name,
        )
    else:
        message = translator("startup_recovery_empty_text")
    answer = QMessageBox.question(
        None,
        translator("startup_recovery_title"),
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    try:
        result = recover_database(path, source)
    except (OSError, sqlite3.Error, ValueError) as error:
        QMessageBox.critical(
            None,
            translator("startup_recovery_failed_title"),
            translator("startup_recovery_failed_text", error=str(error)),
        )
        return False
    QMessageBox.information(
        None,
        translator("startup_recovery_complete_title"),
        translator(
            "startup_recovery_complete_text",
            count=result.restored_count,
            preserved=(
                result.quarantined_database.name
                if result.quarantined_database is not None
                else "—"
            ),
        ),
    )
    return True
