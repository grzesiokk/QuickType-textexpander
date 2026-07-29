from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .auto_backup import list_automatic_backups
from .backup import BackupFormatError, export_backup, import_backup
from .constants import APP_NAME, APP_VERSION, resource_path
from .hotkeys import (
    DEFAULT_QUICK_ACCESS_HOTKEY,
    HOTKEY_SPECS,
    normalize_quick_access_hotkey,
)
from .i18n import Translator
from .models import (
    Snippet,
    TriggerMode,
    next_copy_abbreviation,
    normalize_applications,
    snippet_applies_to_process,
    validate_abbreviation,
    validate_category,
)
from .recovery import restore_backup
from .storage import DuplicateAbbreviationError, Storage
from .template_engine import TemplateIssue, inspect_template, render_template
from .windows_platform import process_name_from_window

ID_ROLE = Qt.ItemDataRole.UserRole
HOTKEY_TRANSLATION_KEYS = {
    "ctrl_alt_space": "hotkey_ctrl_alt_space",
    "ctrl_shift_space": "hotkey_ctrl_shift_space",
    "alt_shift_space": "hotkey_alt_shift_space",
    "disabled": "hotkey_disabled",
}


class EngineSignals(QObject):
    expanded = Signal(object)
    error = Signal(str)
    quick_access = Signal(int)


class SettingsDialog(QDialog):
    def __init__(
        self,
        translator: Translator,
        *,
        language: str,
        engine_active: bool,
        autostart: bool,
        excluded_processes: set[str],
        database_path: Path,
        automatic_backups: bool = True,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.setModal(True)
        self.resize(560, 430)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language_combo = QComboBox()
        self.language_combo.addItem(self.t("polish"), "pl")
        self.language_combo.addItem(self.t("english"), "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(language)))
        form.addRow(self.t("language"), self.language_combo)

        self.active_checkbox = QCheckBox(self.t("engine_active"))
        self.active_checkbox.setChecked(engine_active)
        form.addRow("", self.active_checkbox)

        self.autostart_checkbox = QCheckBox(self.t("autostart"))
        self.autostart_checkbox.setChecked(autostart)
        form.addRow("", self.autostart_checkbox)

        self.automatic_backups_checkbox = QCheckBox(self.t("automatic_backups"))
        self.automatic_backups_checkbox.setChecked(automatic_backups)
        form.addRow("", self.automatic_backups_checkbox)

        self.quick_access_hotkey_combo = QComboBox()
        for hotkey in HOTKEY_SPECS:
            self.quick_access_hotkey_combo.addItem(
                self.t(HOTKEY_TRANSLATION_KEYS[hotkey]),
                hotkey,
            )
        selected_hotkey = normalize_quick_access_hotkey(quick_access_hotkey)
        self.quick_access_hotkey_combo.setCurrentIndex(
            max(0, self.quick_access_hotkey_combo.findData(selected_hotkey))
        )
        form.addRow(
            self.t("quick_access_shortcut"),
            self.quick_access_hotkey_combo,
        )

        self.excluded_label = QLabel(self.t("excluded_apps"))
        self.excluded_edit = QPlainTextEdit()
        self.excluded_edit.setMaximumHeight(105)
        self.excluded_edit.setPlaceholderText(self.t("excluded_apps_help"))
        self.excluded_edit.setPlainText("\n".join(sorted(excluded_processes, key=str.casefold)))
        form.addRow(self.excluded_label, self.excluded_edit)
        layout.addLayout(form)

        warning = QLabel(self.t("warning_terminal"))
        warning.setWordWrap(True)
        warning.setProperty("kind", "warning")
        layout.addWidget(warning)

        quick_access = QLabel(self.t("quick_access_setting"))
        quick_access.setWordWrap(True)
        quick_access.setProperty("kind", "muted")
        layout.addWidget(quick_access)

        path_label = QLabel(self.t("db_location", path=str(database_path)))
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        path_label.setProperty("kind", "muted")
        layout.addWidget(path_label)
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setWindowTitle(self.t("settings"))

    @property
    def selected_language(self) -> str:
        return str(self.language_combo.currentData())

    @property
    def selected_excluded_processes(self) -> set[str]:
        return {
            Path(line.strip()).name
            for line in self.excluded_edit.toPlainText().splitlines()
            if line.strip()
        }

    @property
    def selected_quick_access_hotkey(self) -> str:
        return normalize_quick_access_hotkey(
            str(self.quick_access_hotkey_combo.currentData())
        )


class QuickAccessDialog(QDialog):
    snippet_chosen = Signal(object, int)

    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.t = translator
        self.snippets: list[Snippet] = []
        self._target_window = 0
        self.quick_access_hotkey = normalize_quick_access_hotkey(
            quick_access_hotkey
        )

        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.resize(720, 430)

        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.apply_filter)
        self.search_edit.returnPressed.connect(self.choose_current)
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 38)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 140)
        self.table.itemDoubleClicked.connect(lambda _item: self.choose_current())
        layout.addWidget(self.table, 1)

        self.hint_label = QLabel()
        self.hint_label.setProperty("kind", "muted")
        layout.addWidget(self.hint_label)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("quick_access"))
        self.search_edit.setPlaceholderText(self.t("quick_access_search"))
        self.table.setHorizontalHeaderLabels(
            [
                self.t("favorite_column"),
                self.t("shortcut_column"),
                self.t("category_column"),
                self.t("expansion"),
            ]
        )
        hotkey_label = self.t(
            HOTKEY_TRANSLATION_KEYS[self.quick_access_hotkey]
        )
        self.hint_label.setText(self.t("quick_access_hint", hotkey=hotkey_label))
        if self.snippets:
            self._populate_table()

    def set_hotkey(self, hotkey: str) -> None:
        self.quick_access_hotkey = normalize_quick_access_hotkey(hotkey)
        self.retranslate()

    def show_for_window(self, target_window: int) -> None:
        self._target_window = target_window
        process_name = process_name_from_window(target_window)
        self.snippets = sorted(
            (
                snippet
                for snippet in self.storage.list_snippets()
                if snippet.enabled
                and snippet_applies_to_process(snippet, process_name)
            ),
            key=lambda snippet: (
                not snippet.favorite,
                -snippet.usage_count,
                snippet.abbreviation.casefold(),
            ),
        )
        self._populate_table()
        self.search_edit.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_edit.setFocus()

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for snippet in self.snippets:
            row = self.table.rowCount()
            self.table.insertRow(row)
            favorite = QTableWidgetItem("★" if snippet.favorite else "")
            favorite.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            favorite.setData(ID_ROLE, snippet.id)
            abbreviation = QTableWidgetItem(snippet.abbreviation)
            abbreviation.setData(ID_ROLE, snippet.id)
            category = QTableWidgetItem(
                snippet.category if snippet.category else self.t("uncategorized")
            )
            category.setData(ID_ROLE, snippet.id)
            preview = render_template(snippet.expansion, clipboard_text="").text
            preview_item = QTableWidgetItem(preview.replace("\r", "").replace("\n", " ↵ "))
            preview_item.setData(ID_ROLE, snippet.id)
            self.table.setItem(row, 0, favorite)
            self.table.setItem(row, 1, abbreviation)
            self.table.setItem(row, 2, category)
            self.table.setItem(row, 3, preview_item)
        self._select_first_visible()

    def apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.table.rowCount()):
            snippet_id = self.table.item(row, 0).data(ID_ROLE)
            snippet = next((item for item in self.snippets if item.id == snippet_id), None)
            visible = (
                snippet is not None
                and (
                    not needle
                    or needle in snippet.abbreviation.casefold()
                    or needle in snippet.category.casefold()
                    or needle in snippet.expansion.casefold()
                    or any(
                        needle in application.casefold()
                        for application in snippet.applications
                    )
                )
            )
            self.table.setRowHidden(row, not visible)
        self._select_first_visible()

    def _select_first_visible(self) -> None:
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.selectRow(row)
                return
        self.table.clearSelection()

    def choose_current(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            return
        snippet_id = selected[0].data(ID_ROLE)
        snippet = next((item for item in self.snippets if item.id == snippet_id), None)
        if snippet is None:
            return
        target_window = self._target_window
        self.hide()
        self.snippet_chosen.emit(snippet, target_window)


class BackupRestoreDialog(QDialog):
    def __init__(
        self,
        backup_directory: Path,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.paths = list_automatic_backups(backup_directory)
        self.setModal(True)
        self.resize(680, 390)
        self.setWindowTitle(self.t("restore_backup_title"))

        layout = QVBoxLayout(self)
        description = QLabel(self.t("restore_backup_description"))
        description.setWordWrap(True)
        layout.addWidget(description)

        self.table = QTableWidget(0, 3)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(
            [
                self.t("backup_date_column"),
                self.t("backup_snippets_column"),
                self.t("backup_file_column"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(1, 90)
        for path in self.paths:
            try:
                snippet_count = len(import_backup(path))
                date_text = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except (OSError, BackupFormatError):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            date_item = QTableWidgetItem(date_text)
            date_item.setData(ID_ROLE, str(path))
            self.table.setItem(row, 0, date_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(snippet_count)))
            self.table.setItem(row, 2, QTableWidgetItem(path.name))
        if self.table.rowCount():
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel(self.t("no_automatic_backups"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(self.table.rowCount() == 0)
        layout.addWidget(self.empty_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.restore_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.restore_button.setText(self.t("restore"))
        self.restore_button.setEnabled(self.table.rowCount() > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_path(self) -> Path | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        value = selected[0].data(ID_ROLE)
        return Path(str(value)) if value else None


class CategoryManagerDialog(QDialog):
    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.t = translator
        self.changed = False
        self.setModal(True)
        self.resize(520, 390)
        self.setWindowTitle(self.t("manage_categories"))

        layout = QVBoxLayout(self)
        description = QLabel(self.t("manage_categories_description"))
        description.setWordWrap(True)
        layout.addWidget(description)

        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(
            [self.t("category_column"), self.t("backup_snippets_column")]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(1, 100)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self.rename_selected())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.rename_button = QPushButton(self.t("rename_category"))
        self.rename_button.clicked.connect(self.rename_selected)
        actions.addWidget(self.rename_button)
        self.clear_button = QPushButton(self.t("clear_category"))
        self.clear_button.clicked.connect(self.clear_selected)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload_categories()

    @property
    def selected_category(self) -> str | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        value = selected[0].data(ID_ROLE)
        return str(value) if value else None

    def reload_categories(self, *, select: str | None = None) -> None:
        categories = self.storage.list_categories()
        self.table.setRowCount(0)
        for category, count in categories:
            row = self.table.rowCount()
            self.table.insertRow(row)
            category_item = QTableWidgetItem(category)
            category_item.setData(ID_ROLE, category)
            self.table.setItem(row, 0, category_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(count)))
            if select == category:
                self.table.selectRow(row)
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)
        self._update_buttons()

    def rename_selected(self) -> None:
        category = self.selected_category
        if category is None:
            return
        replacement, accepted = QInputDialog.getText(
            self,
            self.t("rename_category"),
            self.t("new_category_name"),
            text=category,
        )
        if not accepted:
            return
        replacement = replacement.strip()
        issues = validate_category(replacement)
        if not replacement or issues:
            message_key = (
                "required_category"
                if not replacement
                else "long_category"
                if issues[0].code == "too_long"
                else "control_category"
            )
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(message_key),
            )
            return
        changed = self.storage.rename_category(category, replacement)
        if changed:
            self.changed = True
            self.reload_categories(select=replacement)

    def clear_selected(self) -> None:
        category = self.selected_category
        if category is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("clear_category_title"),
            self.t("clear_category_text", category=category),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        changed = self.storage.clear_category(category)
        if changed:
            self.changed = True
            self.reload_categories()

    def _update_buttons(self) -> None:
        selected = self.selected_category is not None
        self.rename_button.setEnabled(selected)
        self.clear_button.setEnabled(selected)


class StatisticsDialog(QDialog):
    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.t = translator
        self.changed = False
        self.setModal(True)
        self.resize(700, 430)
        self.setWindowTitle(self.t("statistics"))

        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setProperty("kind", "emptyTitle")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(
            [
                self.t("shortcut_column"),
                self.t("category_column"),
                self.t("usage_column"),
                self.t("last_used_column"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 160)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel(self.t("statistics_empty"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        actions = QHBoxLayout()
        self.reset_selected_button = QPushButton(self.t("reset_selected_usage"))
        self.reset_selected_button.clicked.connect(self.reset_selected)
        actions.addWidget(self.reset_selected_button)
        self.reset_all_button = QPushButton(self.t("reset_all_usage"))
        self.reset_all_button.clicked.connect(self.reset_all)
        actions.addWidget(self.reset_all_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload_statistics()

    @property
    def selected_snippet_id(self) -> int | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        value = selected[0].data(ID_ROLE)
        return int(value) if value is not None else None

    def reload_statistics(self, *, select_id: int | None = None) -> None:
        snippets = self.storage.list_snippets()
        total = sum(snippet.usage_count for snippet in snippets)
        used = sorted(
            (snippet for snippet in snippets if snippet.usage_count > 0),
            key=lambda snippet: (
                -snippet.usage_count,
                snippet.abbreviation.casefold(),
                snippet.abbreviation,
            ),
        )
        self.summary_label.setText(
            self.t(
                "statistics_summary",
                total=total,
                used=len(used),
                all=len(snippets),
            )
        )
        self.table.setRowCount(0)
        for snippet in used:
            row = self.table.rowCount()
            self.table.insertRow(row)
            abbreviation = QTableWidgetItem(snippet.abbreviation)
            abbreviation.setData(ID_ROLE, snippet.id)
            self.table.setItem(row, 0, abbreviation)
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    snippet.category if snippet.category else self.t("uncategorized")
                ),
            )
            usage = QTableWidgetItem(str(snippet.usage_count))
            usage.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, usage)
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(self._format_last_used(snippet)),
            )
            if snippet.id == select_id:
                self.table.selectRow(row)
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)
        self.empty_label.setVisible(not used)
        self.table.setVisible(bool(used))
        self._update_buttons()

    def reset_selected(self) -> None:
        snippet_id = self.selected_snippet_id
        if snippet_id is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("reset_usage_title"),
            self.t("reset_selected_usage_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.storage.reset_usage(snippet_id):
            self.changed = True
            self.reload_statistics()

    def reset_all(self) -> None:
        answer = QMessageBox.question(
            self,
            self.t("reset_usage_title"),
            self.t("reset_all_usage_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.storage.reset_usage():
            self.changed = True
            self.reload_statistics()

    def _update_buttons(self) -> None:
        self.reset_selected_button.setEnabled(
            self.selected_snippet_id is not None
        )
        self.reset_all_button.setEnabled(self.table.rowCount() > 0)

    def _format_last_used(self, snippet: Snippet) -> str:
        if snippet.last_used_at is None:
            return self.t("never_used")
        if self.t.language == "pl":
            return snippet.last_used_at.strftime("%d.%m.%Y %H:%M")
        return snippet.last_used_at.strftime("%Y-%m-%d %H:%M")


class MainWindow(QMainWindow):
    language_change_requested = Signal(str)
    active_change_requested = Signal(bool)
    autostart_change_requested = Signal(bool)
    automatic_backups_change_requested = Signal(bool)
    excluded_processes_change_requested = Signal(object)
    quick_access_hotkey_change_requested = Signal(str)
    snippets_changed = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        *,
        engine_active: bool,
        autostart: bool,
        automatic_backups: bool = True,
        excluded_processes: set[str] | None = None,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
    ) -> None:
        super().__init__()
        self.storage = storage
        self.t = translator
        self.engine_active = engine_active
        self.autostart = autostart
        self.automatic_backups = automatic_backups
        self.excluded_processes = set(excluded_processes or set())
        self.quick_access_hotkey = normalize_quick_access_hotkey(
            quick_access_hotkey
        )
        self.snippets: list[Snippet] = []
        self._current_id: int | None = None
        self._is_new = False
        self._dirty = False
        self._selection_guard = False
        self._allow_close = False

        self.setMinimumSize(920, 620)
        self.resize(1120, 720)
        self.setWindowIcon(QIcon(str(resource_path("quicktype.svg"))))
        self._build_ui()
        self.retranslate()
        self.reload_snippets()

    def _build_ui(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        self.new_action = QAction(self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_snippet)
        toolbar.addAction(self.new_action)

        self.duplicate_action = QAction(self)
        self.duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        self.duplicate_action.triggered.connect(self.duplicate_current)
        toolbar.addAction(self.duplicate_action)

        self.delete_action = QAction(self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self.delete_current)
        toolbar.addAction(self.delete_action)

        self.import_action = QAction(self)
        self.import_action.triggered.connect(self.import_snippets)
        toolbar.addAction(self.import_action)

        self.export_action = QAction(self)
        self.export_action.triggered.connect(self.export_snippets)
        toolbar.addAction(self.export_action)

        self.restore_action = QAction(self)
        self.restore_action.triggered.connect(self.restore_automatic_backup)
        toolbar.addAction(self.restore_action)

        self.categories_action = QAction(self)
        self.categories_action.triggered.connect(self.open_category_manager)
        toolbar.addAction(self.categories_action)

        self.statistics_action = QAction(self)
        self.statistics_action.triggered.connect(self.open_statistics)
        toolbar.addAction(self.statistics_action)
        toolbar.addSeparator()

        self.active_action = QAction(self)
        self.active_action.setCheckable(True)
        self.active_action.setChecked(self.engine_active)
        self.active_action.toggled.connect(self.active_change_requested.emit)
        toolbar.addAction(self.active_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.settings_action = QAction(self)
        self.settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(self.settings_action)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_editor_panel())
        splitter.setSizes([390, 700])
        self.setCentralWidget(splitter)

        status = QStatusBar()
        self.status_state = QLabel()
        self.status_message = QLabel()
        status.addWidget(self.status_state)
        status.addPermanentWidget(self.status_message, 1)
        self.setStatusBar(status)

        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self.save_current)

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 6, 12)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.apply_filter)
        filters = QHBoxLayout()
        filters.addWidget(self.search_edit, 1)
        self.category_filter = QComboBox()
        self.category_filter.setMinimumWidth(135)
        self.category_filter.currentIndexChanged.connect(
            lambda _index: self.apply_filter(self.search_edit.text())
        )
        filters.addWidget(self.category_filter)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 38)
        self.table.setColumnWidth(1, 44)
        self.table.setColumnWidth(3, 125)
        self.table.setColumnWidth(4, 115)
        self.table.setColumnWidth(5, 65)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._table_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        layout.addWidget(self.table, 1)

        self.empty_frame = QFrame()
        empty_layout = QVBoxLayout(self.empty_frame)
        empty_layout.addStretch(1)
        self.empty_title = QLabel()
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setProperty("kind", "emptyTitle")
        empty_layout.addWidget(self.empty_title)
        self.empty_text = QLabel()
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_text.setProperty("kind", "muted")
        empty_layout.addWidget(self.empty_text)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty_frame, 1)
        return panel

    def _build_editor_panel(self) -> QWidget:
        self.editor_panel = QWidget()
        layout = QVBoxLayout(self.editor_panel)
        layout.setContentsMargins(14, 12, 12, 12)

        form = QGridLayout()
        self.abbreviation_label = QLabel()
        self.abbreviation_edit = QLineEdit()
        self.abbreviation_edit.setMaxLength(64)
        self.abbreviation_edit.textChanged.connect(self._editor_changed)
        form.addWidget(self.abbreviation_label, 0, 0)
        form.addWidget(self.abbreviation_edit, 0, 1)

        self.category_label = QLabel()
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.category_combo.lineEdit() is not None:
            self.category_combo.lineEdit().setMaxLength(64)
        self.category_combo.currentTextChanged.connect(self._editor_changed)
        form.addWidget(self.category_label, 1, 0)
        form.addWidget(self.category_combo, 1, 1)

        self.mode_label = QLabel()
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._editor_changed)
        form.addWidget(self.mode_label, 2, 0)
        form.addWidget(self.mode_combo, 2, 1)

        self.applications_label = QLabel()
        self.applications_edit = QLineEdit()
        self.applications_edit.setClearButtonEnabled(True)
        self.applications_edit.textChanged.connect(self._editor_changed)
        form.addWidget(self.applications_label, 3, 0)
        form.addWidget(self.applications_edit, 3, 1)

        self.enabled_checkbox = QCheckBox()
        self.enabled_checkbox.toggled.connect(self._editor_changed)
        form.addWidget(self.enabled_checkbox, 4, 1)
        self.favorite_checkbox = QCheckBox()
        self.favorite_checkbox.toggled.connect(self._editor_changed)
        form.addWidget(self.favorite_checkbox, 5, 1)
        self.stats_label = QLabel()
        self.stats_label.setProperty("kind", "muted")
        form.addWidget(self.stats_label, 6, 1)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        self.expansion_label = QLabel()
        layout.addWidget(self.expansion_label)
        self.expansion_edit = QPlainTextEdit()
        self.expansion_edit.setTabChangesFocus(False)
        self.expansion_edit.textChanged.connect(self._editor_changed)
        layout.addWidget(self.expansion_edit, 2)

        variable_row = QHBoxLayout()
        self.variables_label = QLabel()
        variable_row.addWidget(self.variables_label)
        self.variable_buttons: dict[str, QPushButton] = {}
        for token in ("date", "time", "clipboard", "cursor"):
            button = QPushButton()
            button.setProperty("token", token)
            button.clicked.connect(lambda _checked=False, name=token: self.insert_variable(name))
            self.variable_buttons[token] = button
            variable_row.addWidget(button)
        variable_row.addStretch(1)
        layout.addLayout(variable_row)

        self.issue_label = QLabel()
        self.issue_label.setWordWrap(True)
        layout.addWidget(self.issue_label)

        self.preview_group = QGroupBox()
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setMaximumHeight(125)
        preview_layout.addWidget(self.preview_edit)
        layout.addWidget(self.preview_group)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.cancel_edit)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton()
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save_current)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        return self.editor_panel

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("app_title"))
        self.new_action.setText(self.t("new"))
        self.duplicate_action.setText(self.t("duplicate"))
        self.delete_action.setText(self.t("delete"))
        self.import_action.setText(self.t("import"))
        self.export_action.setText(self.t("export"))
        self.restore_action.setText(self.t("restore"))
        self.categories_action.setText(self.t("manage_categories"))
        self.statistics_action.setText(self.t("statistics"))
        self.active_action.setText(self.t("engine_active"))
        self.settings_action.setText(self.t("settings"))
        self.search_edit.setPlaceholderText(self.t("search_placeholder"))
        self.table.setHorizontalHeaderLabels(
            [
                self.t("favorite_column"),
                self.t("active_column"),
                self.t("shortcut_column"),
                self.t("category_column"),
                self.t("mode_column"),
                self.t("usage_column"),
            ]
        )
        self.empty_title.setText(self.t("empty_title"))
        self.empty_text.setText(self.t("empty_text"))
        self.abbreviation_label.setText(self.t("abbreviation"))
        self.category_label.setText(self.t("category"))
        self.mode_label.setText(self.t("trigger_mode"))
        self.applications_label.setText(self.t("applications"))
        self.applications_edit.setPlaceholderText(self.t("applications_help"))
        current_mode = self.mode_combo.currentData()
        with QSignalBlocker(self.mode_combo):
            self.mode_combo.clear()
            self.mode_combo.addItem(self.t("immediate"), TriggerMode.IMMEDIATE.value)
            self.mode_combo.addItem(self.t("delimiter"), TriggerMode.DELIMITER.value)
            mode_index = self.mode_combo.findData(current_mode)
            self.mode_combo.setCurrentIndex(max(0, mode_index))
        self._refresh_category_controls()
        self.enabled_checkbox.setText(self.t("enabled"))
        self.favorite_checkbox.setText(self.t("favorite"))
        self.expansion_label.setText(self.t("expansion"))
        self.variables_label.setText(self.t("variables") + ":")
        for token, button in self.variable_buttons.items():
            button.setText(self.t(token))
        self.preview_group.setTitle(self.t("preview"))
        self.cancel_button.setText(self.t("cancel"))
        self.save_button.setText(self.t("save"))
        self.status_state.setText(
            self.t("status_active") if self.engine_active else self.t("status_paused")
        )
        self._populate_table(preserve_selection=True)
        self._update_stats_label(
            next((entry for entry in self.snippets if entry.id == self._current_id), None)
        )
        self.update_preview()

    def reload_snippets(self, *, select_id: int | None = None) -> None:
        self.snippets = self.storage.list_snippets()
        self._refresh_category_controls()
        self._populate_table(preserve_selection=False)
        if select_id is not None:
            self._select_id(select_id)
        elif self.snippets and self._current_id is None and not self._is_new:
            self._select_id(self.snippets[0].id)
        else:
            self._update_empty_state()

    def _populate_table(self, *, preserve_selection: bool) -> None:
        selected_id = self._current_id if preserve_selection else None
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
            for snippet in self.snippets:
                row = self.table.rowCount()
                self.table.insertRow(row)
                favorite = QTableWidgetItem("★" if snippet.favorite else "")
                favorite.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                favorite.setData(ID_ROLE, snippet.id)
                enabled = QTableWidgetItem("●" if snippet.enabled else "○")
                enabled.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                enabled.setData(ID_ROLE, snippet.id)
                abbreviation = QTableWidgetItem(snippet.abbreviation)
                abbreviation.setData(ID_ROLE, snippet.id)
                category = QTableWidgetItem(
                    snippet.category if snippet.category else self.t("uncategorized")
                )
                category.setData(ID_ROLE, snippet.id)
                mode = QTableWidgetItem(
                    self.t("immediate")
                    if snippet.trigger_mode == TriggerMode.IMMEDIATE
                    else self.t("delimiter")
                )
                mode.setData(ID_ROLE, snippet.id)
                usage = QTableWidgetItem(str(snippet.usage_count))
                usage.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                usage.setData(ID_ROLE, snippet.id)
                self.table.setItem(row, 0, favorite)
                self.table.setItem(row, 1, enabled)
                self.table.setItem(row, 2, abbreviation)
                self.table.setItem(row, 3, category)
                self.table.setItem(row, 4, mode)
                self.table.setItem(row, 5, usage)
        self.apply_filter(self.search_edit.text())
        if selected_id is not None:
            self._select_id(selected_id)
        self._update_empty_state()

    def apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        selected_category = self.category_filter.currentData()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            snippet_id = item.data(ID_ROLE)
            snippet = next((entry for entry in self.snippets if entry.id == snippet_id), None)
            matches_text = not needle or (
                snippet is not None
                and (
                    needle in snippet.abbreviation.casefold()
                    or needle in snippet.expansion.casefold()
                    or needle in snippet.category.casefold()
                    or any(
                        needle in application.casefold()
                        for application in snippet.applications
                    )
                )
            )
            matches_category = (
                selected_category is None
                or (snippet is not None and snippet.category == selected_category)
            )
            visible = matches_text and matches_category
            self.table.setRowHidden(row, not visible)

    def _refresh_category_controls(self) -> None:
        categories = sorted(
            {snippet.category for snippet in self.snippets if snippet.category},
            key=str.casefold,
        )
        selected_filter = (
            self.category_filter.currentData()
            if hasattr(self, "category_filter") and self.category_filter.count()
            else None
        )
        with QSignalBlocker(self.category_filter):
            self.category_filter.clear()
            self.category_filter.addItem(self.t("all_categories"), None)
            self.category_filter.addItem(self.t("uncategorized"), "")
            for category in categories:
                self.category_filter.addItem(category, category)
            filter_index = self.category_filter.findData(selected_filter)
            self.category_filter.setCurrentIndex(max(0, filter_index))

        current_category = self.category_combo.currentText()
        with QSignalBlocker(self.category_combo):
            self.category_combo.clear()
            self.category_combo.addItem("")
            self.category_combo.addItems(categories)
            self.category_combo.setEditText(current_category)

    def _selection_changed(self) -> None:
        if self._selection_guard:
            return
        selected = self.table.selectedItems()
        if not selected:
            return
        snippet_id = int(selected[0].data(ID_ROLE))
        if snippet_id == self._current_id and not self._is_new:
            return
        if not self._maybe_resolve_dirty():
            self._select_id(self._current_id)
            return
        snippet = next((entry for entry in self.snippets if entry.id == snippet_id), None)
        if snippet:
            self._load_snippet(snippet)

    def _select_id(self, snippet_id: int | None) -> None:
        if snippet_id is None:
            return
        self._selection_guard = True
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item and item.data(ID_ROLE) == snippet_id:
                    self.table.selectRow(row)
                    snippet = next((entry for entry in self.snippets if entry.id == snippet_id), None)
                    if snippet:
                        self._load_snippet(snippet)
                    break
        finally:
            self._selection_guard = False

    def _load_snippet(self, snippet: Snippet) -> None:
        self._selection_guard = True
        try:
            self._current_id = snippet.id
            self._is_new = False
            with (
                QSignalBlocker(self.abbreviation_edit),
                QSignalBlocker(self.category_combo),
                QSignalBlocker(self.mode_combo),
                QSignalBlocker(self.applications_edit),
                QSignalBlocker(self.enabled_checkbox),
                QSignalBlocker(self.favorite_checkbox),
                QSignalBlocker(self.expansion_edit),
            ):
                self.abbreviation_edit.setText(snippet.abbreviation)
                self.category_combo.setEditText(snippet.category)
                self.mode_combo.setCurrentIndex(
                    self.mode_combo.findData(snippet.trigger_mode.value)
                )
                self.applications_edit.setText(", ".join(snippet.applications))
                self.enabled_checkbox.setChecked(snippet.enabled)
                self.favorite_checkbox.setChecked(snippet.favorite)
                self.expansion_edit.setPlainText(snippet.expansion)
            self.editor_panel.setEnabled(True)
            self._dirty = False
            self._update_stats_label(snippet)
            self.update_preview()
        finally:
            self._selection_guard = False

    def new_snippet(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        self._selection_guard = True
        try:
            self.table.clearSelection()
            self._current_id = None
            self._is_new = True
            self.editor_panel.setEnabled(True)
            self.abbreviation_edit.clear()
            self.category_combo.setEditText("")
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(TriggerMode.DELIMITER.value))
            self.applications_edit.clear()
            self.enabled_checkbox.setChecked(True)
            self.favorite_checkbox.setChecked(False)
            self.expansion_edit.clear()
            self._dirty = False
            self.abbreviation_edit.setFocus()
            self.update_preview()
        finally:
            self._selection_guard = False

    def save_current(self) -> bool:
        if not self._is_new and self._current_id is None:
            return False
        abbreviation = self.abbreviation_edit.text()
        issues = validate_abbreviation(abbreviation)
        if issues:
            key_by_code = {
                "required": "required_abbreviation",
                "whitespace": "whitespace_abbreviation",
                "too_long": "long_abbreviation",
                "control": "control_abbreviation",
            }
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(key_by_code.get(issues[0].code, "required_abbreviation")),
            )
            return False
        category = self.category_combo.currentText().strip()
        category_issues = validate_category(category)
        if category_issues:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(
                    "long_category"
                    if category_issues[0].code == "too_long"
                    else "control_category"
                ),
            )
            return False
        try:
            applications = normalize_applications(
                tuple(
                    item.strip()
                    for item in self.applications_edit.text().replace(";", ",").split(",")
                )
            )
        except ValueError:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t("invalid_applications"),
            )
            return False
        snippet = Snippet(
            id=self._current_id,
            abbreviation=abbreviation,
            expansion=self.expansion_edit.toPlainText(),
            trigger_mode=TriggerMode(str(self.mode_combo.currentData())),
            enabled=self.enabled_checkbox.isChecked(),
            category=category,
            favorite=self.favorite_checkbox.isChecked(),
            applications=applications,
        )
        try:
            saved = self.storage.save_snippet(snippet)
        except DuplicateAbbreviationError:
            QMessageBox.warning(
                self,
                self.t("duplicate_title"),
                self.t("duplicate_text", abbr=abbreviation),
            )
            return False
        except ValueError as error:
            QMessageBox.warning(self, self.t("validation_title"), str(error))
            return False
        self._current_id = saved.id
        self._is_new = False
        self._dirty = False
        self.reload_snippets(select_id=saved.id)
        self.snippets_changed.emit()
        self.status_message.setText(self.t("saved", abbr=saved.abbreviation))
        return True

    def delete_current(self) -> None:
        if self._current_id is None:
            return
        snippet = next((entry for entry in self.snippets if entry.id == self._current_id), None)
        if snippet is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("delete_title"),
            self.t("delete_text", abbr=snippet.abbreviation),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_snippet(self._current_id)
        self._current_id = None
        self._dirty = False
        self._is_new = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(self.t("deleted"))

    def toggle_current_enabled(self) -> None:
        self._toggle_current_flag("enabled")

    def toggle_current_favorite(self) -> None:
        self._toggle_current_flag("favorite")

    def _toggle_current_flag(self, field: str) -> None:
        if not self._maybe_resolve_dirty() or self._current_id is None:
            return
        source = next(
            (entry for entry in self.snippets if entry.id == self._current_id),
            None,
        )
        if source is None:
            return
        value = not bool(getattr(source, field))
        saved = self.storage.save_snippet(replace(source, **{field: value}))
        self.reload_snippets(select_id=saved.id)
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t(
                "snippet_enabled" if field == "enabled" and value
                else "snippet_disabled" if field == "enabled"
                else "favorite_added" if value
                else "favorite_removed",
                abbr=saved.abbreviation,
            )
        )

    def _table_double_clicked(self, row: int, column: int) -> None:
        if column not in (0, 1):
            return
        self.table.selectRow(row)
        if column == 0:
            self.toggle_current_favorite()
        else:
            self.toggle_current_enabled()

    def _show_table_context_menu(self, position: object) -> None:
        item = self.table.itemAt(position)
        if item is None:
            return
        self.table.selectRow(item.row())
        source = next(
            (entry for entry in self.snippets if entry.id == self._current_id),
            None,
        )
        if source is None:
            return
        menu = QMenu(self)
        enabled_action = menu.addAction(
            self.t("disable_snippet") if source.enabled else self.t("enable_snippet")
        )
        favorite_action = menu.addAction(
            self.t("remove_favorite") if source.favorite else self.t("add_favorite")
        )
        menu.addSeparator()
        duplicate_action = menu.addAction(self.t("duplicate"))
        delete_action = menu.addAction(self.t("delete"))
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == enabled_action:
            self.toggle_current_enabled()
        elif selected == favorite_action:
            self.toggle_current_favorite()
        elif selected == duplicate_action:
            self.duplicate_current()
        elif selected == delete_action:
            self.delete_current()

    def duplicate_current(self) -> None:
        if not self._maybe_resolve_dirty() or self._current_id is None:
            return
        source = next(
            (entry for entry in self.snippets if entry.id == self._current_id),
            None,
        )
        if source is None:
            return
        abbreviation = next_copy_abbreviation(
            source.abbreviation,
            {entry.abbreviation for entry in self.snippets},
        )
        duplicate = self.storage.save_snippet(
            Snippet(
                id=None,
                abbreviation=abbreviation,
                expansion=source.expansion,
                trigger_mode=source.trigger_mode,
                enabled=source.enabled,
                category=source.category,
                favorite=source.favorite,
                applications=source.applications,
            )
        )
        self._current_id = duplicate.id
        self._is_new = False
        self._dirty = False
        self.reload_snippets(select_id=duplicate.id)
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t("duplicated", abbr=duplicate.abbreviation)
        )

    def export_snippets(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        default_name = self.storage.path.parent / (
            f"QuickType-backup-{datetime.now():%Y%m%d}.json"
        )
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.t("export_title"),
            str(default_name),
            self.t("backup_filter"),
        )
        if not path:
            return
        try:
            snippets = self.storage.list_snippets()
            export_backup(Path(path), snippets)
        except OSError as error:
            QMessageBox.warning(self, self.t("export_error_title"), str(error))
            return
        self.status_message.setText(
            self.t("export_success", count=len(snippets), path=path)
        )

    def import_snippets(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.t("import_title"),
            str(self.storage.path.parent),
            self.t("backup_filter"),
        )
        if not path:
            return
        choice = QMessageBox.question(
            self,
            self.t("import_choice_title"),
            self.t("import_choice_text"),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return
        try:
            snippets = import_backup(Path(path))
            added, skipped = self.storage.import_snippets(
                snippets,
                replace=choice == QMessageBox.StandardButton.Yes,
            )
        except (OSError, ValueError, BackupFormatError) as error:
            QMessageBox.warning(self, self.t("import_error_title"), str(error))
            return
        self._current_id = None
        self._is_new = False
        self._dirty = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t("import_success", added=added, skipped=skipped)
        )

    def restore_automatic_backup(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        dialog = BackupRestoreDialog(
            self.storage.path.parent / "Backups",
            self.t,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = dialog.selected_path
        if path is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("restore_confirm_title"),
            self.t("restore_confirm_text", file=path.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            count, safety_copy = restore_backup(self.storage, path)
        except (OSError, ValueError, BackupFormatError) as error:
            QMessageBox.warning(self, self.t("restore_error_title"), str(error))
            return
        self._current_id = None
        self._is_new = False
        self._dirty = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t(
                "restore_success",
                count=count,
                safety=safety_copy.name,
            )
        )

    def open_category_manager(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        dialog = CategoryManagerDialog(self.storage, self.t, self)
        dialog.exec()
        if not dialog.changed:
            return
        self._current_id = None
        self._is_new = False
        self._dirty = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(self.t("categories_updated"))

    def open_statistics(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        selected_id = self._current_id
        dialog = StatisticsDialog(self.storage, self.t, self)
        dialog.exec()
        if not dialog.changed:
            return
        self.reload_snippets(select_id=selected_id)
        self.status_message.setText(self.t("statistics_reset"))

    def cancel_edit(self) -> None:
        if self._current_id is not None:
            snippet = next((entry for entry in self.snippets if entry.id == self._current_id), None)
            if snippet:
                self._load_snippet(snippet)
                return
        self._is_new = False
        self._dirty = False
        if self.snippets:
            self._select_id(self.snippets[0].id)
        else:
            self.editor_panel.setEnabled(False)
            self._update_empty_state()

    def insert_variable(self, name: str) -> None:
        cursor = self.expansion_edit.textCursor()
        cursor.insertText("{{" + name + "}}")
        self.expansion_edit.setTextCursor(cursor)
        self.expansion_edit.setFocus()

    def _editor_changed(self, *_args: object) -> None:
        if self._selection_guard:
            return
        self._dirty = True
        self.update_preview()

    def update_preview(self) -> None:
        if not hasattr(self, "preview_edit"):
            return
        template = self.expansion_edit.toPlainText()
        rendered = render_template(template, clipboard_text="")
        self.preview_edit.setPlainText(rendered.text)
        issues = inspect_template(template)
        if issues:
            descriptions = ", ".join(self._describe_issue(issue) for issue in issues)
            self.issue_label.setText(self.t("template_issues", issues=descriptions))
            self.issue_label.setProperty("kind", "error")
        else:
            self.issue_label.setText(self.t("template_ok"))
            self.issue_label.setProperty("kind", "success")
        self.issue_label.style().unpolish(self.issue_label)
        self.issue_label.style().polish(self.issue_label)

    def _describe_issue(self, issue: TemplateIssue) -> str:
        if issue.code == "unknown_token":
            return self.t("unknown_token", token=issue.token)
        if issue.code == "invalid_format":
            return self.t("invalid_format", token=issue.token)
        return self.t(issue.code)

    def _maybe_resolve_dirty(self) -> bool:
        if not self._dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(self.t("unsaved_title"))
        box.setText(self.t("unsaved_text"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return self.save_current()
        if answer == QMessageBox.StandardButton.Discard:
            self._dirty = False
            return True
        return False

    def _update_empty_state(self) -> None:
        empty = not self.snippets
        self.table.setVisible(not empty)
        self.empty_frame.setVisible(empty)
        if empty and not self._is_new:
            self.editor_panel.setEnabled(False)
            self._current_id = None

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.t,
            language=self.t.language,
            engine_active=self.engine_active,
            autostart=self.autostart,
            automatic_backups=self.automatic_backups,
            excluded_processes=self.excluded_processes,
            database_path=self.storage.path,
            quick_access_hotkey=self.quick_access_hotkey,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_language != self.t.language:
            self.language_change_requested.emit(dialog.selected_language)
        if dialog.active_checkbox.isChecked() != self.engine_active:
            self.active_change_requested.emit(dialog.active_checkbox.isChecked())
        if dialog.autostart_checkbox.isChecked() != self.autostart:
            self.autostart_change_requested.emit(dialog.autostart_checkbox.isChecked())
        if dialog.automatic_backups_checkbox.isChecked() != self.automatic_backups:
            self.automatic_backups_change_requested.emit(
                dialog.automatic_backups_checkbox.isChecked()
            )
        if dialog.selected_excluded_processes != self.excluded_processes:
            self.excluded_processes_change_requested.emit(
                dialog.selected_excluded_processes
            )
        if dialog.selected_quick_access_hotkey != self.quick_access_hotkey:
            self.quick_access_hotkey_change_requested.emit(
                dialog.selected_quick_access_hotkey
            )

    def set_engine_active(self, active: bool) -> None:
        self.engine_active = active
        with QSignalBlocker(self.active_action):
            self.active_action.setChecked(active)
        self.status_state.setText(self.t("status_active") if active else self.t("status_paused"))

    def set_autostart(self, enabled: bool) -> None:
        self.autostart = enabled

    def set_automatic_backups(self, enabled: bool) -> None:
        self.automatic_backups = enabled

    def set_excluded_processes(self, processes: set[str]) -> None:
        self.excluded_processes = set(processes)

    def set_quick_access_hotkey(self, hotkey: str) -> None:
        self.quick_access_hotkey = normalize_quick_access_hotkey(hotkey)

    def refresh_usage(self, snippet: Snippet) -> None:
        self.snippets = [
            snippet if entry.id == snippet.id else entry for entry in self.snippets
        ]
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(ID_ROLE) == snippet.id:
                self.table.item(row, 5).setText(str(snippet.usage_count))
                break
        if self._current_id == snippet.id:
            self._update_stats_label(snippet)

    def _update_stats_label(self, snippet: Snippet | None) -> None:
        if snippet is None or snippet.usage_count == 0:
            self.stats_label.setText(self.t("never_used"))
            return
        if snippet.last_used_at is None:
            last = "—"
        elif self.t.language == "pl":
            last = snippet.last_used_at.strftime("%d.%m.%Y %H:%M")
        else:
            last = snippet.last_used_at.strftime("%Y-%m-%d %H:%M")
        self.stats_label.setText(
            self.t("usage_summary", count=snippet.usage_count, last=last)
        )

    def show_and_activate(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def prepare_quit(self) -> bool:
        if not self._maybe_resolve_dirty():
            return False
        self._allow_close = True
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if not self._maybe_resolve_dirty():
            event.ignore()
            return
        self.hide()
        event.ignore()


class TrayController:
    def __init__(
        self,
        translator: Translator,
        *,
        active: bool,
        on_open: Callable[[], None],
        on_active: Callable[[bool], None],
        on_autostart: Callable[[bool], None],
        on_quit: Callable[[], None],
        autostart: bool,
    ) -> None:
        self.t = translator
        self.active = active
        self.autostart = autostart
        self.on_open = on_open
        self.on_active = on_active
        self.on_autostart = on_autostart
        self.on_quit = on_quit
        self.tray = QSystemTrayIcon(QIcon(str(resource_path("quicktype.svg"))))
        self.menu = QMenu()
        self.open_action = QAction()
        self.open_action.triggered.connect(on_open)
        self.menu.addAction(self.open_action)
        self.active_action = QAction()
        self.active_action.setCheckable(True)
        self.active_action.setChecked(active)
        self.active_action.toggled.connect(on_active)
        self.menu.addAction(self.active_action)
        self.autostart_action = QAction()
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart)
        self.autostart_action.toggled.connect(on_autostart)
        self.menu.addAction(self.autostart_action)
        self.menu.addSeparator()
        self.quit_action = QAction()
        self.quit_action.triggered.connect(on_quit)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.retranslate()
        self.tray.show()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.on_open()

    def retranslate(self) -> None:
        self.open_action.setText(self.t("open"))
        self.active_action.setText(self.t("engine_active"))
        self.autostart_action.setText(self.t("autostart"))
        self.quit_action.setText(self.t("quit"))
        self.tray.setToolTip(
            self.t("tray_tooltip_active") if self.active else self.t("tray_tooltip_paused")
        )

    def set_active(self, active: bool) -> None:
        self.active = active
        with QSignalBlocker(self.active_action):
            self.active_action.setChecked(active)
        self.retranslate()

    def set_autostart(self, enabled: bool) -> None:
        self.autostart = enabled
        with QSignalBlocker(self.autostart_action):
            self.autostart_action.setChecked(enabled)

    def show_error(self, text: str) -> None:
        self.tray.showMessage(
            APP_NAME,
            text,
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )


def apply_application_style(application: QApplication) -> None:
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 9))
    application.setStyleSheet(
        """
        QMainWindow, QDialog { background: #f6f7fb; }
        QToolBar { background: white; border: none; border-bottom: 1px solid #e2e5ee; padding: 7px; spacing: 7px; }
        QLineEdit, QPlainTextEdit, QComboBox, QTableWidget {
            background: white; border: 1px solid #d7dbe7; border-radius: 6px; padding: 6px;
            selection-background-color: #6c50e8;
        }
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #6c50e8; }
        QTableWidget { gridline-color: transparent; }
        QHeaderView::section { background: #eceef5; border: none; padding: 7px; font-weight: 600; }
        QPushButton { background: #ffffff; border: 1px solid #d7dbe7; border-radius: 6px; padding: 7px 12px; }
        QPushButton:hover { border-color: #6c50e8; }
        QPushButton:default { background: #6548dc; color: white; border-color: #6548dc; }
        QStatusBar { background: white; border-top: 1px solid #e2e5ee; }
        QLabel[kind="muted"] { color: #676d7e; }
        QLabel[kind="warning"] { color: #9a6400; background: #fff4d6; padding: 8px; border-radius: 5px; }
        QLabel[kind="error"] { color: #b42318; }
        QLabel[kind="success"] { color: #18794e; }
        QLabel[kind="emptyTitle"] { font-size: 17px; font-weight: 600; color: #35394a; }
        """
    )


def show_about(parent: QWidget, translator: Translator) -> None:
    QMessageBox.about(
        parent,
        translator("about"),
        translator("about_text", version=APP_VERSION),
    )
