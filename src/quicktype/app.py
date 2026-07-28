from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .constants import APP_NAME, APP_VERSION, database_path, resource_path
from .hook import KeyboardHookEngine
from .i18n import Translator
from .models import Snippet
from .single_instance import SingleInstance
from .storage import Storage
from .ui import (
    EngineSignals,
    MainWindow,
    QuickAccessDialog,
    TrayController,
    apply_application_style,
)
from .windows_platform import (
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
        repair_autostart_if_enabled()
        autostart = is_autostart_enabled()

        self.signals = EngineSignals()
        self.engine = KeyboardHookEngine(
            self.storage.list_snippets(),
            on_expansion=self.signals.expanded.emit,
            on_error=self.signals.error.emit,
            on_quick_access=self.signals.quick_access.emit,
            excluded_processes=excluded_processes,
        )
        self.window = MainWindow(
            self.storage,
            self.translator,
            engine_active=active,
            autostart=autostart,
            excluded_processes=excluded_processes,
        )
        self.quick_access = QuickAccessDialog(self.storage, self.translator)
        self.tray = TrayController(
            self.translator,
            active=active,
            on_open=self.window.show_and_activate,
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
        self.window.excluded_processes_change_requested.connect(
            self.set_excluded_processes
        )
        self.window.snippets_changed.connect(self.refresh_snippets)
        self.window.quit_requested.connect(self.quit)
        self.signals.expanded.connect(self._on_expanded)
        self.signals.error.connect(self._on_engine_error)
        self.signals.quick_access.connect(self.quick_access.show_for_window)
        self.quick_access.snippet_chosen.connect(self._quick_access_chosen)
        self.application.aboutToQuit.connect(self.shutdown)

        self.engine.set_active(active)
        self.engine.start()

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
        self.engine.replace_snippets(self.storage.list_snippets())

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

    def _on_expanded(self, snippet: object) -> None:
        abbreviation = getattr(snippet, "abbreviation", "")
        snippet_id = getattr(snippet, "id", None)
        if isinstance(snippet_id, int):
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
