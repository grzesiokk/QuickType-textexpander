from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import regex
from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QModelIndex,
    QObject,
    QSignalBlocker,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .backup import BackupFormatError, export_backup
from .backup_catalog import (
    BackupKind,
    delete_backup_file,
    list_backup_entries,
)
from .builtin_libraries import (
    DEFINITIONS_BY_ID,
    LIBRARY_DEFINITIONS,
    BuiltinCatalog,
    BuiltinItem,
    BuiltinLibraryId,
    BuiltinLibrarySettings,
    BuiltinLibrarySettingsError,
)
from .constants import APP_NAME, APP_VERSION, resource_path
from .diagnostics import collect_diagnostic_report
from .hotkeys import (
    CLIPBOARD_CAPTURE_HOTKEY_SPECS,
    DEFAULT_CLIPBOARD_CAPTURE_HOTKEY,
    DEFAULT_QUICK_ACCESS_HOTKEY,
    HOTKEY_SPECS,
    normalize_clipboard_capture_hotkey,
    normalize_quick_access_hotkey,
)
from .i18n import Translator
from .importing import (
    ImportAnalysis,
    ImportMode,
    analyze_import,
    apply_import,
)
from .maintenance import (
    collect_data_summary,
    create_manual_backup,
    format_file_size,
)
from .models import (
    Snippet,
    SnippetKind,
    TriggerMode,
    next_copy_abbreviation,
    normalize_applications,
    normalize_search_terms,
    validate_category,
    validate_snippet_trigger,
)
from .recovery import (
    RestoreAnalysis,
    RestoreChange,
    RestoreChangeKind,
    analyze_restore,
    restore_backup,
)
from .search import SearchEntry, SearchIndex, normalize_search_text
from .storage import DuplicateAbbreviationError, Storage
from .template_engine import (
    FormField,
    FormFieldKind,
    TemplateIssue,
    escape_field_part,
    inspect_template,
    render_template,
)
from .windows_platform import process_name_from_window

ID_ROLE = Qt.ItemDataRole.UserRole
BACKUP_KIND_ROLE = Qt.ItemDataRole.UserRole + 1
HOTKEY_TRANSLATION_KEYS = {
    "ctrl_alt_space": "hotkey_ctrl_alt_space",
    "ctrl_shift_space": "hotkey_ctrl_shift_space",
    "alt_shift_space": "hotkey_alt_shift_space",
    "disabled": "hotkey_disabled",
}
CLIPBOARD_HOTKEY_TRANSLATION_KEYS = {
    "ctrl_alt_n": "hotkey_ctrl_alt_n",
    "alt_shift_n": "hotkey_alt_shift_n",
    "disabled": "hotkey_disabled",
}
SUPPORTED_THEMES = ("light", "dark", "high_contrast")


def normalize_theme(value: object) -> str:
    theme = str(value or "light")
    return theme if theme in SUPPORTED_THEMES else "light"


class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except ValueError:
            return super().__lt__(other)


class EngineSignals(QObject):
    expanded = Signal(object)
    error = Signal(str)
    quick_access = Signal(int)
    clipboard_capture = Signal()
    form_requested = Signal(object, int)


class ExpansionFormDialog(QDialog):
    def __init__(
        self,
        translator: Translator,
        fields: tuple[FormField, ...],
        *,
        remembered_values: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.fields = fields
        self.remembered_values = remembered_values or {}
        self.widgets: dict[str, QWidget] = {}
        self.setWindowTitle(self.t("form_title"))
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        description = QLabel(self.t("form_description"))
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        for field in fields:
            remembered = self.remembered_values.get(
                field.identifier,
                field.default,
            )
            if field.kind == FormFieldKind.INPUT:
                widget: QWidget = QLineEdit(remembered)
            elif field.kind == FormFieldKind.CHOICE:
                combo = QComboBox()
                combo.addItems(field.options)
                index = combo.findText(remembered)
                combo.setCurrentIndex(max(0, index))
                widget = combo
            else:
                checkbox = QCheckBox()
                checkbox.setChecked(remembered != field.unchecked_text)
                widget = checkbox
            widget.setAccessibleName(field.label)
            self.widgets[field.identifier] = widget
            form.addRow(field.label, widget)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        fields = {field.identifier: field for field in self.fields}
        for identifier, widget in self.widgets.items():
            field = fields[identifier]
            if isinstance(widget, QLineEdit):
                values[identifier] = widget.text()
            elif isinstance(widget, QComboBox):
                values[identifier] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                values[identifier] = (
                    field.checked_text
                    if widget.isChecked()
                    else field.unchecked_text
                )
        return values


class TemplateAssistantDialog(QDialog):
    TYPES = ("input", "choice", "check", "var", "calc", "snippet")

    def __init__(
        self,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.setWindowTitle(self.t("template_assistant_title"))
        self.setMinimumWidth(470)
        layout = QFormLayout(self)

        self.type_combo = QComboBox()
        for token_type in self.TYPES:
            self.type_combo.addItem(
                self.t(f"template_type_{token_type}"),
                token_type,
            )
        self.type_combo.currentIndexChanged.connect(self._update_help)
        layout.addRow(self.t("template_field_type"), self.type_combo)

        self.identifier_edit = QLineEdit()
        layout.addRow(self.t("template_identifier"), self.identifier_edit)
        self.label_edit = QLineEdit()
        layout.addRow(self.t("template_field_label"), self.label_edit)
        self.values_edit = QLineEdit()
        layout.addRow(self.t("template_field_values"), self.values_edit)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setProperty("kind", "muted")
        layout.addRow("", self.help_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self._update_help()

    @property
    def token(self) -> str:
        token_type = str(self.type_combo.currentData())
        identifier = escape_field_part(self.identifier_edit.text().strip())
        label = escape_field_part(self.label_edit.text().strip())
        raw_values = self.values_edit.text()
        if token_type == "input":
            return (
                "{{input:"
                + identifier
                + "|"
                + label
                + "|"
                + escape_field_part(raw_values)
                + "}}"
            )
        if token_type == "choice":
            options = [
                escape_field_part(value.strip())
                for value in raw_values.replace(";", ",").split(",")
                if value.strip()
            ]
            return "{{choice:" + "|".join((identifier, label, *options)) + "}}"
        if token_type == "check":
            values = [value.strip() for value in raw_values.split("|", 1)]
            checked = escape_field_part(values[0] if values else "")
            unchecked = escape_field_part(values[1] if len(values) > 1 else "")
            return "{{check:" + "|".join((identifier, label, checked, unchecked)) + "}}"
        if token_type == "var":
            return "{{var:" + identifier + "}}"
        if token_type == "calc":
            return "{{calc:" + self.identifier_edit.text().strip() + "}}"
        return "{{snippet:" + self.identifier_edit.text().strip() + "}}"

    def _accept_if_valid(self) -> None:
        token_type = str(self.type_combo.currentData())
        if not self.identifier_edit.text().strip():
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t("template_identifier_required"),
            )
            return
        if token_type == "choice" and not any(
            value.strip()
            for value in self.values_edit.text().replace(";", ",").split(",")
        ):
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t("template_choice_required"),
            )
            return
        self.accept()

    def _update_help(self) -> None:
        token_type = str(self.type_combo.currentData())
        self.help_label.setText(self.t(f"template_help_{token_type}"))
        self.label_edit.setEnabled(token_type in {"input", "choice", "check"})
        self.values_edit.setEnabled(token_type in {"input", "choice", "check"})


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
        backup_retention: int = 20,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        clipboard_capture_hotkey: str = DEFAULT_CLIPBOARD_CAPTURE_HOTKEY,
        theme: str = "light",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.setModal(True)
        self.resize(580, 470)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language_combo = QComboBox()
        self.language_combo.addItem(self.t("polish"), "pl")
        self.language_combo.addItem(self.t("english"), "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(language)))
        form.addRow(self.t("language"), self.language_combo)
        self.theme_combo = QComboBox()
        for theme_name in SUPPORTED_THEMES:
            self.theme_combo.addItem(
                self.t(f"theme_{theme_name}"),
                theme_name,
            )
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(normalize_theme(theme)))
        )
        form.addRow(self.t("theme"), self.theme_combo)

        self.active_checkbox = QCheckBox(self.t("engine_active"))
        self.active_checkbox.setChecked(engine_active)
        form.addRow("", self.active_checkbox)

        self.autostart_checkbox = QCheckBox(self.t("autostart"))
        self.autostart_checkbox.setChecked(autostart)
        form.addRow("", self.autostart_checkbox)

        self.automatic_backups_checkbox = QCheckBox(self.t("automatic_backups"))
        self.automatic_backups_checkbox.setChecked(automatic_backups)
        form.addRow("", self.automatic_backups_checkbox)
        self.backup_retention_spin = QSpinBox()
        self.backup_retention_spin.setRange(1, 200)
        self.backup_retention_spin.setValue(backup_retention)
        self.backup_retention_spin.setEnabled(automatic_backups)
        self.automatic_backups_checkbox.toggled.connect(
            self.backup_retention_spin.setEnabled
        )
        form.addRow(
            self.t("backup_retention"),
            self.backup_retention_spin,
        )

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

        self.clipboard_capture_hotkey_combo = QComboBox()
        for hotkey in CLIPBOARD_CAPTURE_HOTKEY_SPECS:
            self.clipboard_capture_hotkey_combo.addItem(
                self.t(CLIPBOARD_HOTKEY_TRANSLATION_KEYS[hotkey]),
                hotkey,
            )
        selected_capture_hotkey = normalize_clipboard_capture_hotkey(
            clipboard_capture_hotkey
        )
        self.clipboard_capture_hotkey_combo.setCurrentIndex(
            max(
                0,
                self.clipboard_capture_hotkey_combo.findData(
                    selected_capture_hotkey
                ),
            )
        )
        form.addRow(
            self.t("clipboard_capture_shortcut"),
            self.clipboard_capture_hotkey_combo,
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

    @property
    def selected_clipboard_capture_hotkey(self) -> str:
        return normalize_clipboard_capture_hotkey(
            str(self.clipboard_capture_hotkey_combo.currentData())
        )

    @property
    def selected_backup_retention(self) -> int:
        return self.backup_retention_spin.value()

    @property
    def selected_theme(self) -> str:
        return normalize_theme(self.theme_combo.currentData())


class QuickAccessModel(QAbstractTableModel):
    def __init__(
        self,
        translator: Translator,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.entries: list[SearchEntry] = []

    def set_entries(self, entries: list[SearchEntry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.entries)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else 4

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                ("★ " if entry.favorite else "") + entry.title,
                self.t(f"search_source_{entry.source}"),
                entry.abbreviation,
                entry.preview,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.preview
        if role == ID_ROLE:
            return entry.key
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        headers = (
            self.t("name_column"),
            self.t("source_column"),
            self.t("shortcut_column"),
            self.t("preview"),
        )
        return headers[section] if 0 <= section < len(headers) else None


class QuickAccessDialog(QDialog):
    snippet_chosen = Signal(object, int)

    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        catalog: BuiltinCatalog | None = None,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.t = translator
        self.catalog = catalog
        self.index = SearchIndex(())
        self.results: list[SearchEntry] = []
        self._target_window = 0
        self.quick_access_hotkey = normalize_quick_access_hotkey(
            quick_access_hotkey
        )

        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.resize(860, 480)

        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.apply_filter)
        self.search_edit.returnPressed.connect(self.choose_current)
        layout.addWidget(self.search_edit)

        self.table = QTableView()
        self.model = QuickAccessModel(self.t, self.table)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 175)
        self.table.doubleClicked.connect(lambda _index: self.choose_current())
        layout.addWidget(self.table, 1)

        self.hint_label = QLabel()
        self.hint_label.setProperty("kind", "muted")
        layout.addWidget(self.hint_label)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("quick_access"))
        self.search_edit.setPlaceholderText(self.t("quick_access_search"))
        self.model.headerDataChanged.emit(
            Qt.Orientation.Horizontal,
            0,
            self.model.columnCount() - 1,
        )
        hotkey_label = self.t(
            HOTKEY_TRANSLATION_KEYS[self.quick_access_hotkey]
        )
        self.hint_label.setText(self.t("quick_access_hint", hotkey=hotkey_label))
        if self.results:
            self.apply_filter(self.search_edit.text())

    def set_hotkey(self, hotkey: str) -> None:
        self.quick_access_hotkey = normalize_quick_access_hotkey(hotkey)
        self.retranslate()

    def show_for_window(self, target_window: int) -> None:
        self._target_window = target_window
        self.index = SearchIndex.build(
            self.storage.list_snippets(),
            self.catalog,
            process_name=process_name_from_window(target_window),
        )
        self.search_edit.clear()
        self.apply_filter("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_edit.setFocus()

    def apply_filter(self, text: str) -> None:
        self.results = self.index.search(text)
        self.model.set_entries(self.results)
        if self.results:
            self.table.selectRow(0)
        else:
            self.table.clearSelection()

    def choose_current(self) -> None:
        current = self.table.currentIndex()
        if not current.isValid() or not 0 <= current.row() < len(self.results):
            return
        target_window = self._target_window
        snippet = self.results[current.row()].snippet
        self.hide()
        self.snippet_chosen.emit(snippet, target_window)


class BuiltinLibraryManagerDialog(QDialog):
    settings_changed = Signal()
    snippets_changed = Signal()

    def __init__(
        self,
        catalog: BuiltinCatalog,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.storage = catalog.storage
        self.t = translator
        self._items: list[BuiltinItem] = []
        self.setModal(True)
        self.resize(960, 650)

        layout = QVBoxLayout(self)
        library_row = QHBoxLayout()
        self.library_combo = QComboBox()
        for definition in LIBRARY_DEFINITIONS:
            self.library_combo.addItem(
                self.t(definition.name_key),
                definition.library_id.value,
            )
        self.library_combo.currentIndexChanged.connect(self._load_library)
        library_row.addWidget(self.library_combo, 1)
        self.item_count_label = QLabel()
        self.item_count_label.setProperty("kind", "muted")
        library_row.addWidget(self.item_count_label)
        layout.addLayout(library_row)

        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setProperty("kind", "muted")
        layout.addWidget(self.description_label)

        settings_row = QHBoxLayout()
        self.enabled_checkbox = QCheckBox(self.t("library_enabled"))
        settings_row.addWidget(self.enabled_checkbox)
        settings_row.addWidget(QLabel(self.t("library_profile")))
        self.profile_combo = QComboBox()
        settings_row.addWidget(self.profile_combo)
        settings_row.addWidget(QLabel(self.t("library_prefix")))
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setMaximumWidth(180)
        settings_row.addWidget(self.prefix_edit)
        self.save_settings_button = QPushButton(self.t("library_save_settings"))
        self.save_settings_button.clicked.connect(self.save_settings)
        settings_row.addWidget(self.save_settings_button)
        settings_row.addStretch(1)
        layout.addLayout(settings_row)

        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(self.t("library_search"))
        self.search_edit.textChanged.connect(self._populate_items)
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(
            (
                self.t("active_column"),
                self.t("name_column"),
                self.t("shortcut_column"),
                self.t("preview"),
            )
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.Stretch,
        )
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 220)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.disable_button = QPushButton(self.t("library_disable_item"))
        self.disable_button.clicked.connect(
            lambda: self._set_selected_item_enabled(False)
        )
        actions.addWidget(self.disable_button)
        self.enable_button = QPushButton(self.t("library_enable_item"))
        self.enable_button.clicked.connect(
            lambda: self._set_selected_item_enabled(True)
        )
        actions.addWidget(self.enable_button)
        self.copy_button = QPushButton(self.t("library_copy_item"))
        self.copy_button.clicked.connect(self.copy_selected_item)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        close_button = QPushButton(self.t("close"))
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.setWindowTitle(self.t("libraries"))
        self._load_library()

    @property
    def current_library_id(self) -> BuiltinLibraryId:
        return BuiltinLibraryId(str(self.library_combo.currentData()))

    def _load_library(self) -> None:
        library_id = self.current_library_id
        definition = DEFINITIONS_BY_ID[library_id]
        settings = self.catalog.settings(library_id)
        self.description_label.setText(self.t(definition.description_key))
        self.item_count_label.setText(
            self.t(
                "library_item_count",
                count=self.catalog.item_count(library_id),
            )
        )
        with QSignalBlocker(self.profile_combo):
            self.profile_combo.clear()
            for profile in definition.profiles:
                self.profile_combo.addItem(
                    self.t(f"library_profile_{profile}"),
                    profile,
                )
            self.profile_combo.setCurrentIndex(
                max(0, self.profile_combo.findData(settings.profile))
            )
        with QSignalBlocker(self.enabled_checkbox):
            self.enabled_checkbox.setChecked(settings.enabled)
        with QSignalBlocker(self.prefix_edit):
            self.prefix_edit.setText(settings.prefix)
        self.prefix_edit.setEnabled(definition.prefix_editable)
        has_items = library_id != BuiltinLibraryId.CALCULATOR
        self.search_edit.setEnabled(has_items)
        self.table.setEnabled(has_items)
        self.disable_button.setEnabled(has_items)
        self.enable_button.setEnabled(has_items)
        self.copy_button.setEnabled(has_items)
        self._populate_items(self.search_edit.text())

    def save_settings(self) -> None:
        library_id = self.current_library_id
        try:
            self.catalog.set_settings(
                library_id,
                BuiltinLibrarySettings(
                    enabled=self.enabled_checkbox.isChecked(),
                    profile=str(self.profile_combo.currentData()),
                    prefix=self.prefix_edit.text(),
                ),
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(
                    "library_settings_error",
                    error=self.t(
                        f"library_error_{error.code}"
                        if isinstance(error, BuiltinLibrarySettingsError)
                        else "library_error_unknown"
                    ),
                ),
            )
            return
        self.settings_changed.emit()
        self._populate_items(self.search_edit.text())

    def _populate_items(self, text: str) -> None:
        library_id = self.current_library_id
        if library_id == BuiltinLibraryId.CALCULATOR:
            self._items = []
            self.table.setRowCount(0)
            return
        terms = tuple(normalize_search_text(text).split())
        disabled = self.storage.list_disabled_builtin_items(library_id.value)
        matches: list[BuiltinItem] = []
        for item in self.catalog.items(library_id):
            haystack = item.search_text or normalize_search_text(
                " ".join((item.title, item.slug, item.expansion, *item.keywords))
            )
            if terms and not all(term in haystack for term in terms):
                continue
            matches.append(item)
            if len(matches) >= 300:
                break
        self._items = matches
        self.table.setRowCount(len(matches))
        for row, item in enumerate(matches):
            enabled = QTableWidgetItem("●" if item.item_id not in disabled else "○")
            enabled.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            enabled.setData(ID_ROLE, item.item_id)
            self.table.setItem(row, 0, enabled)
            self.table.setItem(row, 1, QTableWidgetItem(item.title))
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(self.catalog.trigger_for_item(item)),
            )
            self.table.setItem(row, 3, QTableWidgetItem(item.expansion))
        if matches:
            self.table.selectRow(0)

    def _selected_item(self) -> BuiltinItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _set_selected_item_enabled(self, enabled: bool) -> None:
        item = self._selected_item()
        if item is None:
            return
        self.catalog.set_item_enabled(item, enabled=enabled)
        self.settings_changed.emit()
        self._populate_items(self.search_edit.text())

    def copy_selected_item(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        snippet = self.catalog.copy_as_snippet(item)
        existing = {
            entry.abbreviation
            for entry in self.storage.list_snippets()
        }
        if snippet.abbreviation in existing:
            snippet = replace(
                snippet,
                abbreviation=next_copy_abbreviation(
                    snippet.abbreviation,
                    existing,
                ),
            )
        saved = self.storage.save_snippet(snippet)
        self.snippets_changed.emit()
        QMessageBox.information(
            self,
            self.t("libraries"),
            self.t("library_item_copied", abbr=saved.abbreviation),
        )


class BackupRestoreDialog(QDialog):
    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator
        self.storage = storage
        self.backup_directory = storage.path.parent / "Backups"
        self.entries = []
        self._analysis_cache: dict[Path, RestoreAnalysis] = {}
        self._selected_analysis: RestoreAnalysis | None = None
        self.setModal(True)
        self.resize(1040, 720)
        self.setWindowTitle(self.t("restore_backup_title"))

        layout = QVBoxLayout(self)
        description = QLabel(self.t("restore_backup_description"))
        description.setWordWrap(True)
        layout.addWidget(description)

        filters = QHBoxLayout()
        filters.addWidget(QLabel(self.t("backup_type_filter")))
        self.type_filter = QComboBox()
        self.type_filter.addItem(self.t("backup_type_all"), None)
        for kind in BackupKind:
            self.type_filter.addItem(
                self.t(f"backup_type_{kind.value}"),
                kind.value,
            )
        self.type_filter.currentIndexChanged.connect(
            self.apply_type_filter
        )
        filters.addWidget(self.type_filter)
        filters.addWidget(QLabel(self.t("restore_change_filter")))
        self.change_filter = QComboBox()
        self.change_filter.addItem(self.t("restore_change_all"), None)
        for kind in RestoreChangeKind:
            self.change_filter.addItem(
                self.t(f"restore_change_{kind.value}"),
                kind.value,
            )
        self.change_filter.currentIndexChanged.connect(
            self.apply_change_filter
        )
        filters.addWidget(self.change_filter)
        self.change_search = QLineEdit()
        self.change_search.setClearButtonEnabled(True)
        self.change_search.setPlaceholderText(
            self.t("restore_change_search")
        )
        self.change_search.textChanged.connect(self.apply_change_filter)
        filters.addWidget(self.change_search, 1)
        filters.addStretch(1)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(
            [
                self.t("backup_date_column"),
                self.t("backup_type_column"),
                self.t("backup_snippets_column"),
                self.t("backup_file_column"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(1, 145)
        self.table.setColumnWidth(2, 90)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel(self.t("no_backups"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(self.table.rowCount() == 0)
        layout.addWidget(self.empty_label)

        self.impact_label = QLabel()
        self.impact_label.setWordWrap(True)
        self.impact_label.setProperty("kind", "muted")
        layout.addWidget(self.impact_label)

        self.change_table = QTableWidget(0, 5)
        self.change_table.setHorizontalHeaderLabels(
            [
                self.t("restore_action_column"),
                self.t("shortcut_column"),
                self.t("restore_fields_column"),
                self.t("restore_current_column"),
                self.t("restore_backup_column"),
            ]
        )
        self.change_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.change_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.change_table.verticalHeader().hide()
        self.change_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.change_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.change_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.change_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.change_table.setColumnWidth(2, 210)
        layout.addWidget(self.change_table, 1)

        self.no_changes_label = QLabel(self.t("restore_no_changes"))
        self.no_changes_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_changes_label.setVisible(False)
        layout.addWidget(self.no_changes_label)

        self.action_label = QLabel()
        self.action_label.setWordWrap(True)
        self.action_label.setProperty("kind", "muted")
        layout.addWidget(self.action_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.refresh_button = buttons.addButton(
            self.t("refresh_backups"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.open_folder_button = buttons.addButton(
            self.t("open_backup_folder"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.copy_report_button = buttons.addButton(
            self.t("copy_restore_report"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.delete_backup_button = buttons.addButton(
            self.t("delete_backup"),
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        self.restore_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.restore_button.setText(self.t("restore"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            self.t("cancel")
        )
        self.table.itemSelectionChanged.connect(self._update_selection)
        self.refresh_button.clicked.connect(self.refresh_entries)
        self.open_folder_button.clicked.connect(self.open_backup_folder)
        self.copy_report_button.clicked.connect(self.copy_restore_report)
        self.delete_backup_button.clicked.connect(self.delete_selected_backup)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_entries(show_status=False)

    @property
    def selected_path(self) -> Path | None:
        row = self.table.currentRow()
        if row < 0 or self.table.isRowHidden(row):
            return None
        value = self.table.item(row, 0).data(ID_ROLE)
        return Path(str(value)) if value else None

    @property
    def selected_analysis(self) -> RestoreAnalysis | None:
        return self._selected_analysis

    def apply_type_filter(self) -> None:
        preferred_path = self.selected_path
        selected_kind = self.type_filter.currentData()
        first_visible: int | None = None
        preferred_row: int | None = None
        for row in range(self.table.rowCount()):
            kind = self.table.item(row, 0).data(BACKUP_KIND_ROLE)
            visible = selected_kind is None or kind == selected_kind
            self.table.setRowHidden(row, not visible)
            if visible and first_visible is None:
                first_visible = row
            row_path = Path(str(self.table.item(row, 0).data(ID_ROLE)))
            if visible and row_path == preferred_path:
                preferred_row = row
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            target_row = (
                preferred_row
                if preferred_row is not None
                else first_visible
            )
            if target_row is not None:
                self.table.selectRow(target_row)
        self.empty_label.setVisible(first_visible is None)
        self._update_selection()

    def apply_change_filter(self) -> None:
        self._populate_change_table(self._selected_analysis)

    def refresh_entries(self, *, show_status: bool = True) -> None:
        preferred_path = self.selected_path
        self._analysis_cache.clear()
        self.entries = list_backup_entries(self.backup_directory)
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
            for entry in self.entries:
                row = self.table.rowCount()
                self.table.insertRow(row)
                date_item = QTableWidgetItem(
                    entry.modified_at.strftime("%Y-%m-%d %H:%M:%S")
                )
                date_item.setData(ID_ROLE, str(entry.path))
                date_item.setData(BACKUP_KIND_ROLE, entry.kind.value)
                self.table.setItem(row, 0, date_item)
                self.table.setItem(
                    row,
                    1,
                    QTableWidgetItem(
                        self.t(f"backup_type_{entry.kind.value}")
                    ),
                )
                self.table.setItem(
                    row,
                    2,
                    QTableWidgetItem(str(entry.snippet_count)),
                )
                self.table.setItem(
                    row,
                    3,
                    QTableWidgetItem(entry.path.name),
                )
                if entry.path == preferred_path:
                    self.table.selectRow(row)
        self.apply_type_filter()
        if show_status:
            self.action_label.setText(self.t("backups_refreshed"))
            self._set_label_kind(self.action_label, "success")

    def _update_selection(self) -> None:
        path = self.selected_path
        self._selected_analysis = None
        self.action_label.clear()
        self._set_label_kind(self.action_label, "muted")
        if path is None:
            self.impact_label.clear()
            self._populate_change_table(None)
            self.restore_button.setEnabled(False)
            self.delete_backup_button.setEnabled(False)
            self.copy_report_button.setEnabled(False)
            return
        try:
            analysis = self._analysis_cache.get(path)
            if analysis is None:
                analysis = analyze_restore(self.storage, path)
                self._analysis_cache[path] = analysis
        except (OSError, ValueError, BackupFormatError):
            self.impact_label.setText(self.t("restore_analysis_error"))
            self._set_impact_kind("error")
            self.restore_button.setEnabled(False)
            self.delete_backup_button.setEnabled(True)
            self.copy_report_button.setEnabled(False)
            self._populate_change_table(None)
            return
        self._selected_analysis = analysis
        self.impact_label.setText(
            self.t(
                "restore_impact",
                added=analysis.added,
                updated=analysis.updated,
                removed=analysis.removed,
                unchanged=analysis.unchanged,
            )
        )
        self._set_impact_kind("warning" if analysis.removed else "muted")
        self._populate_change_table(analysis)
        self.restore_button.setEnabled(True)
        self.delete_backup_button.setEnabled(True)
        self.copy_report_button.setEnabled(True)

    def _set_impact_kind(self, kind: str) -> None:
        self._set_label_kind(self.impact_label, kind)

    @staticmethod
    def _set_label_kind(label: QLabel, kind: str) -> None:
        label.setProperty("kind", kind)
        label.style().unpolish(label)
        label.style().polish(label)

    def _populate_change_table(
        self,
        analysis: RestoreAnalysis | None,
    ) -> None:
        selected_kind = self.change_filter.currentData()
        needle = self.change_search.text().strip().casefold()
        self.change_table.setRowCount(0)
        if analysis is None:
            self.no_changes_label.setVisible(False)
            return
        for change in analysis.changes:
            if selected_kind is not None and change.kind.value != selected_kind:
                continue
            searchable = " ".join(
                (
                    change.abbreviation,
                    change.current.expansion if change.current else "",
                    change.incoming.expansion if change.incoming else "",
                    " ".join(
                        self.t(f"restore_field_{field}")
                        for field in change.changed_fields
                    ),
                )
            ).casefold()
            if needle and needle not in searchable:
                continue
            self._append_change_row(change)
        self.no_changes_label.setVisible(self.change_table.rowCount() == 0)

    def _append_change_row(self, change: RestoreChange) -> None:
        row = self.change_table.rowCount()
        self.change_table.insertRow(row)
        current_text = change.current.expansion if change.current else None
        incoming_text = change.incoming.expansion if change.incoming else None
        fields = ", ".join(
            self.t(f"restore_field_{field}")
            for field in change.changed_fields
        )
        self.change_table.setItem(
            row,
            0,
            QTableWidgetItem(self.t(f"restore_change_{change.kind.value}")),
        )
        self.change_table.setItem(
            row,
            1,
            QTableWidgetItem(change.abbreviation),
        )
        self.change_table.setItem(
            row,
            2,
            QTableWidgetItem(fields or "—"),
        )
        current_item = QTableWidgetItem(self._expansion_preview(current_text))
        incoming_item = QTableWidgetItem(self._expansion_preview(incoming_text))
        current_item.setToolTip(current_text or "")
        incoming_item.setToolTip(incoming_text or "")
        self.change_table.setItem(row, 3, current_item)
        self.change_table.setItem(row, 4, incoming_item)

    @staticmethod
    def _expansion_preview(value: str | None, limit: int = 160) -> str:
        if value is None:
            return "—"
        preview = value.replace("\r\n", "\n").replace("\r", "\n")
        preview = " ↵ ".join(preview.split("\n"))
        if len(preview) <= limit:
            return preview
        return preview[: limit - 1] + "…"

    def copy_restore_report(self) -> None:
        analysis = self._selected_analysis
        if analysis is None:
            return
        lines = [
            self.t(
                "restore_report_header",
                file=analysis.source.name,
            ),
            self.t(
                "restore_impact",
                added=analysis.added,
                updated=analysis.updated,
                removed=analysis.removed,
                unchanged=analysis.unchanged,
            ),
            "",
        ]
        for change in analysis.changes:
            fields = ", ".join(
                self.t(f"restore_field_{field}")
                for field in change.changed_fields
            )
            lines.append(
                self.t(
                    "restore_report_line",
                    action=self.t(f"restore_change_{change.kind.value}"),
                    abbreviation=change.abbreviation,
                    fields=f" — {fields}" if fields else "",
                )
            )
        QApplication.clipboard().setText("\n".join(lines))
        self.action_label.setText(self.t("restore_report_copied"))
        self._set_label_kind(self.action_label, "success")

    def open_backup_folder(self) -> None:
        try:
            self.backup_directory.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.backup_directory))
            )
        except OSError as error:
            opened = False
            details = str(error)
        else:
            details = str(self.backup_directory)
        if opened:
            self.action_label.setText(
                self.t("backup_folder_opened", path=details)
            )
            self._set_label_kind(self.action_label, "success")
        else:
            self.action_label.setText(
                self.t("open_backup_folder_error", error=details)
            )
            self._set_label_kind(self.action_label, "error")

    def delete_selected_backup(self) -> None:
        path = self.selected_path
        if path is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("delete_backup_title"),
            self.t("delete_backup_text", file=path.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_backup_file(self.backup_directory, path)
        except (OSError, ValueError) as error:
            self.action_label.setText(
                self.t("delete_backup_error", error=str(error))
            )
            self._set_label_kind(self.action_label, "error")
            return
        self.refresh_entries(show_status=False)
        self.action_label.setText(
            self.t("backup_deleted", file=path.name)
        )
        self._set_label_kind(self.action_label, "success")


class ImportPreviewDialog(QDialog):
    def __init__(
        self,
        analysis: ImportAnalysis,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.analysis = analysis
        self.t = translator
        self.setModal(True)
        self.resize(820, 460)
        self.setWindowTitle(self.t("import_preview_title"))

        layout = QVBoxLayout(self)
        file_label = QLabel(
            self.t("import_preview_file", file=analysis.source.name)
        )
        file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(file_label)

        summary = QLabel(
            self.t(
                "import_preview_summary",
                incoming=analysis.incoming_count,
                new=analysis.new_count,
                conflicts=len(analysis.conflicts),
            )
        )
        summary.setWordWrap(True)
        summary.setProperty("kind", "emptyTitle")
        layout.addWidget(summary)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(
            self.t("import_merge_mode"),
            ImportMode.MERGE.value,
        )
        self.mode_combo.addItem(
            self.t("import_update_mode"),
            ImportMode.UPDATE.value,
        )
        self.mode_combo.addItem(
            self.t("import_replace_mode"),
            ImportMode.REPLACE.value,
        )
        layout.addWidget(self.mode_combo)

        conflict_label = QLabel(self.t("import_conflicts"))
        conflict_label.setVisible(bool(analysis.conflicts))
        layout.addWidget(conflict_label)
        self.conflict_table = QTableWidget(0, 3)
        self.conflict_table.setHorizontalHeaderLabels(
            [
                self.t("shortcut_column"),
                self.t("import_current_expansion"),
                self.t("import_incoming_expansion"),
            ]
        )
        self.conflict_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.conflict_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.conflict_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.conflict_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.conflict_table.verticalHeader().hide()
        for conflict in analysis.conflicts:
            row = self.conflict_table.rowCount()
            self.conflict_table.insertRow(row)
            abbreviation_item = QTableWidgetItem(conflict.abbreviation)
            current_item = QTableWidgetItem(
                self._expansion_preview(conflict.current.expansion)
            )
            incoming_item = QTableWidgetItem(
                self._expansion_preview(conflict.incoming.expansion)
            )
            current_item.setToolTip(conflict.current.expansion)
            incoming_item.setToolTip(conflict.incoming.expansion)
            self.conflict_table.setItem(row, 0, abbreviation_item)
            self.conflict_table.setItem(row, 1, current_item)
            self.conflict_table.setItem(row, 2, incoming_item)
        self.conflict_table.setVisible(bool(analysis.conflicts))
        layout.addWidget(self.conflict_table, 1)

        no_conflicts = QLabel(self.t("import_no_conflicts"))
        no_conflicts.setVisible(not analysis.conflicts)
        no_conflicts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(no_conflicts)

        warning = QLabel(self.t("import_safety_copy_info"))
        warning.setWordWrap(True)
        warning.setProperty("kind", "warning")
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            self.t("import")
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def import_mode(self) -> ImportMode:
        return ImportMode(str(self.mode_combo.currentData()))

    @staticmethod
    def _expansion_preview(value: str, limit: int = 120) -> str:
        preview = value.replace("\r\n", "\n").replace("\r", "\n")
        preview = " ↵ ".join(preview.split("\n"))
        if len(preview) <= limit:
            return preview
        return preview[: limit - 1] + "…"


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


class DataMaintenanceDialog(QDialog):
    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.t = translator
        self.setModal(True)
        self.resize(620, 330)
        self.setWindowTitle(self.t("data_maintenance"))

        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty("kind", "emptyTitle")
        layout.addWidget(self.summary_label)

        path_label = QLabel(
            self.t("data_folder_path", path=str(self.storage.path.parent))
        )
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        path_label.setProperty("kind", "muted")
        layout.addWidget(path_label)

        actions = QGridLayout()
        self.backup_button = QPushButton(self.t("create_backup_now"))
        self.backup_button.clicked.connect(self.create_backup)
        actions.addWidget(self.backup_button, 0, 0)
        self.check_button = QPushButton(self.t("check_database"))
        self.check_button.clicked.connect(self.check_database)
        actions.addWidget(self.check_button, 0, 1)
        self.open_folder_button = QPushButton(self.t("open_data_folder"))
        self.open_folder_button.clicked.connect(self.open_data_folder)
        actions.addWidget(self.open_folder_button, 1, 0, 1, 2)
        self.copy_diagnostics_button = QPushButton(
            self.t("copy_diagnostics")
        )
        self.copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        actions.addWidget(self.copy_diagnostics_button, 2, 0, 1, 2)
        layout.addLayout(actions)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_summary()

    def refresh_summary(self) -> None:
        summary = collect_data_summary(self.storage)
        self.summary_label.setText(
            self.t(
                "data_summary",
                snippets=summary.snippet_count,
                backups=summary.backup_count,
                size=format_file_size(summary.database_bytes),
            )
        )

    def create_backup(self) -> None:
        try:
            path = create_manual_backup(self.storage)
        except OSError as error:
            self.result_label.setText(
                self.t("manual_backup_error", error=str(error))
            )
            return
        self.result_label.setText(
            self.t("manual_backup_created", file=path.name)
        )
        self.refresh_summary()

    def check_database(self) -> None:
        try:
            valid, details = self.storage.check_integrity()
        except sqlite3.Error as error:
            valid, details = False, str(error)
        self.result_label.setText(
            self.t(
                "database_check_ok" if valid else "database_check_failed",
                details=details,
            )
        )

    def copy_diagnostics(self) -> None:
        report = collect_diagnostic_report(self.storage)
        QApplication.clipboard().setText(report.as_text())
        self.result_label.setText(self.t("diagnostics_copied"))

    def open_data_folder(self) -> None:
        try:
            self.storage.path.parent.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.storage.path.parent))
            )
        except OSError as error:
            opened = False
            details = str(error)
        else:
            details = str(self.storage.path.parent)
        if not opened:
            self.result_label.setText(
                self.t("open_data_folder_error", error=details)
            )


class MainWindow(QMainWindow):
    language_change_requested = Signal(str)
    active_change_requested = Signal(bool)
    autostart_change_requested = Signal(bool)
    automatic_backups_change_requested = Signal(bool)
    backup_retention_change_requested = Signal(int)
    theme_change_requested = Signal(str)
    excluded_processes_change_requested = Signal(object)
    quick_access_hotkey_change_requested = Signal(str)
    clipboard_capture_hotkey_change_requested = Signal(str)
    snippets_changed = Signal()
    builtin_libraries_changed = Signal()
    quick_search_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        *,
        catalog: BuiltinCatalog | None = None,
        engine_active: bool,
        autostart: bool,
        automatic_backups: bool = True,
        backup_retention: int = 20,
        excluded_processes: set[str] | None = None,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        clipboard_capture_hotkey: str = DEFAULT_CLIPBOARD_CAPTURE_HOTKEY,
        theme: str = "light",
    ) -> None:
        super().__init__()
        self.storage = storage
        self.t = translator
        self.catalog = catalog or BuiltinCatalog(storage)
        self.engine_active = engine_active
        self.autostart = autostart
        self.automatic_backups = automatic_backups
        self.backup_retention = backup_retention
        self.theme = normalize_theme(theme)
        self.excluded_processes = set(excluded_processes or set())
        self.quick_access_hotkey = normalize_quick_access_hotkey(
            quick_access_hotkey
        )
        self.clipboard_capture_hotkey = (
            normalize_clipboard_capture_hotkey(
                clipboard_capture_hotkey
            )
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
        self._restore_ui_state()

    def _build_ui(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        self.new_action = QAction(self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_snippet)
        self.new_from_clipboard_action = QAction(self)
        self.new_from_clipboard_action.setShortcut(
            QKeySequence("Ctrl+Shift+N")
        )
        self.new_from_clipboard_action.triggered.connect(
            self.new_snippet_from_clipboard
        )
        self.new_menu = QMenu(self)
        self.new_menu.addAction(self.new_action)
        self.new_menu.addAction(self.new_from_clipboard_action)
        self.new_button = QToolButton()
        self.new_button.setDefaultAction(self.new_action)
        self.new_button.setMenu(self.new_menu)
        self.new_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        self.new_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        toolbar.addWidget(self.new_button)

        self.duplicate_action = QAction(self)
        self.duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        self.duplicate_action.triggered.connect(self.duplicate_current)
        toolbar.addAction(self.duplicate_action)

        self.delete_action = QAction(self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self.delete_selected)
        toolbar.addAction(self.delete_action)

        self.quick_search_action = QAction(self)
        self.quick_search_action.triggered.connect(self.quick_search_requested.emit)
        toolbar.addAction(self.quick_search_action)

        self.bulk_enable_action = QAction(self)
        self.bulk_enable_action.setShortcut(QKeySequence("Ctrl+Alt+E"))
        self.bulk_enable_action.triggered.connect(
            lambda: self.bulk_set_enabled(True)
        )
        self.bulk_disable_action = QAction(self)
        self.bulk_disable_action.setShortcut(QKeySequence("Ctrl+Alt+D"))
        self.bulk_disable_action.triggered.connect(
            lambda: self.bulk_set_enabled(False)
        )
        self.bulk_favorite_action = QAction(self)
        self.bulk_favorite_action.setShortcut(QKeySequence("Ctrl+Alt+F"))
        self.bulk_favorite_action.triggered.connect(
            lambda: self.bulk_set_favorite(True)
        )
        self.bulk_unfavorite_action = QAction(self)
        self.bulk_unfavorite_action.triggered.connect(
            lambda: self.bulk_set_favorite(False)
        )
        self.bulk_category_action = QAction(self)
        self.bulk_category_action.setShortcut(QKeySequence("Ctrl+Alt+C"))
        self.bulk_category_action.triggered.connect(self.bulk_change_category)
        self.bulk_export_action = QAction(self)
        self.bulk_export_action.setShortcut(QKeySequence("Ctrl+Alt+X"))
        self.bulk_export_action.triggered.connect(
            self.export_selected_snippets
        )
        self.bulk_delete_action = QAction(self)
        self.bulk_delete_action.triggered.connect(self.delete_selected)
        self.bulk_menu = QMenu(self)
        self.bulk_menu.addAction(self.bulk_enable_action)
        self.bulk_menu.addAction(self.bulk_disable_action)
        self.bulk_menu.addSeparator()
        self.bulk_menu.addAction(self.bulk_favorite_action)
        self.bulk_menu.addAction(self.bulk_unfavorite_action)
        self.bulk_menu.addAction(self.bulk_category_action)
        self.bulk_menu.addSeparator()
        self.bulk_menu.addAction(self.bulk_export_action)
        self.bulk_menu.addAction(self.bulk_delete_action)
        self.bulk_button = QToolButton()
        self.bulk_button.setMenu(self.bulk_menu)
        self.bulk_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.bulk_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )

        self.import_action = QAction(self)
        self.import_action.triggered.connect(self.import_snippets)

        self.export_action = QAction(self)
        self.export_action.triggered.connect(self.export_snippets)
        self.export_filtered_action = QAction(self)
        self.export_filtered_action.triggered.connect(
            self.export_filtered_snippets
        )
        self.export_menu = QMenu(self)
        self.export_menu.addAction(self.export_action)
        self.export_menu.addAction(self.export_filtered_action)
        self.export_button = QToolButton()
        self.export_button.setDefaultAction(self.export_action)
        self.export_button.setMenu(self.export_menu)
        self.export_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        self.export_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )

        self.restore_action = QAction(self)
        self.restore_action.triggered.connect(self.restore_automatic_backup)

        self.data_action = QAction(self)
        self.data_action.triggered.connect(self.open_data_maintenance)

        self.categories_action = QAction(self)
        self.categories_action.triggered.connect(self.open_category_manager)

        self.statistics_action = QAction(self)
        self.statistics_action.triggered.connect(self.open_statistics)
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

        self.libraries_action = QAction(self)
        self.libraries_action.triggered.connect(self.open_builtin_libraries)
        self.library_toggle_actions: dict[BuiltinLibraryId, QAction] = {}
        for definition in LIBRARY_DEFINITIONS:
            action = QAction(self)
            action.setCheckable(True)
            action.setChecked(
                self.catalog.settings(definition.library_id).enabled
            )
            action.toggled.connect(
                lambda enabled, library_id=definition.library_id: (
                    self._set_library_enabled(library_id, enabled)
                )
            )
            self.library_toggle_actions[definition.library_id] = action

        self.quit_action = QAction(self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.licenses_action = QAction(self)
        self.licenses_action.triggered.connect(self.open_data_licenses)
        self.about_action = QAction(self)
        self.about_action.triggered.connect(self.show_about)
        self._build_menu_bar()

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(self._build_list_panel())
        self.main_splitter.addWidget(self._build_editor_panel())
        self.main_splitter.setSizes([390, 700])
        self.setCentralWidget(self.main_splitter)

        status = QStatusBar()
        self.status_state = QLabel()
        self.status_message = QLabel()
        status.addWidget(self.status_state)
        status.addPermanentWidget(self.status_message, 1)
        self.setStatusBar(status)

        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self.save_current)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self.focus_search)
        clear_filters_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        clear_filters_shortcut.activated.connect(self.clear_filters)
        copy_preview_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        copy_preview_shortcut.activated.connect(self.copy_preview)
        QWidget.setTabOrder(self.search_edit, self.category_filter)
        QWidget.setTabOrder(self.category_filter, self.table)
        QWidget.setTabOrder(self.table, self.abbreviation_edit)
        QWidget.setTabOrder(self.abbreviation_edit, self.category_combo)
        QWidget.setTabOrder(self.category_combo, self.kind_combo)
        QWidget.setTabOrder(self.kind_combo, self.mode_combo)
        QWidget.setTabOrder(self.mode_combo, self.applications_edit)
        QWidget.setTabOrder(self.applications_edit, self.description_edit)
        QWidget.setTabOrder(self.description_edit, self.search_terms_edit)
        QWidget.setTabOrder(self.search_terms_edit, self.priority_spin)
        QWidget.setTabOrder(self.priority_spin, self.enabled_checkbox)
        QWidget.setTabOrder(self.enabled_checkbox, self.favorite_checkbox)
        QWidget.setTabOrder(self.favorite_checkbox, self.expansion_edit)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        self.file_menu = menu_bar.addMenu("")
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.export_action)
        self.file_menu.addAction(self.export_filtered_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.restore_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)

        self.snippets_menu = menu_bar.addMenu("")
        self.snippets_menu.addAction(self.new_action)
        self.snippets_menu.addAction(self.new_from_clipboard_action)
        self.snippets_menu.addAction(self.duplicate_action)
        self.snippets_menu.addAction(self.delete_action)
        self.snippets_menu.addSeparator()
        self.snippets_menu.addMenu(self.bulk_menu)
        self.snippets_menu.addAction(self.categories_action)

        self.libraries_menu = menu_bar.addMenu("")
        self.libraries_menu.addAction(self.libraries_action)
        self.libraries_menu.addSeparator()
        for definition in LIBRARY_DEFINITIONS:
            self.libraries_menu.addAction(
                self.library_toggle_actions[definition.library_id]
            )

        self.tools_menu = menu_bar.addMenu("")
        self.tools_menu.addAction(self.quick_search_action)
        self.tools_menu.addAction(self.statistics_action)
        self.tools_menu.addAction(self.data_action)

        menu_bar.addAction(self.settings_action)

        self.help_menu = menu_bar.addMenu("")
        self.help_menu.addAction(self.licenses_action)
        self.help_menu.addAction(self.about_action)

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
        self.filter_count_label = QLabel()
        self.filter_count_label.setProperty("kind", "muted")
        layout.addWidget(self.filter_count_label)

        self.table = QTableWidget(0, 6)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.SortOrder.AscendingOrder)
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

        self.kind_label = QLabel()
        self.kind_combo = QComboBox()
        self.kind_combo.currentIndexChanged.connect(self._snippet_kind_changed)
        form.addWidget(self.kind_label, 2, 0)
        form.addWidget(self.kind_combo, 2, 1)

        self.mode_label = QLabel()
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._editor_changed)
        form.addWidget(self.mode_label, 3, 0)
        form.addWidget(self.mode_combo, 3, 1)

        self.applications_label = QLabel()
        self.applications_edit = QLineEdit()
        self.applications_edit.setClearButtonEnabled(True)
        self.applications_edit.textChanged.connect(self._editor_changed)
        form.addWidget(self.applications_label, 4, 0)
        form.addWidget(self.applications_edit, 4, 1)

        self.description_label = QLabel()
        self.description_edit = QLineEdit()
        self.description_edit.setMaxLength(500)
        self.description_edit.textChanged.connect(self._editor_changed)
        form.addWidget(self.description_label, 5, 0)
        form.addWidget(self.description_edit, 5, 1)

        self.search_terms_label = QLabel()
        self.search_terms_edit = QLineEdit()
        self.search_terms_edit.textChanged.connect(self._editor_changed)
        form.addWidget(self.search_terms_label, 6, 0)
        form.addWidget(self.search_terms_edit, 6, 1)

        self.priority_label = QLabel()
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-1000, 1000)
        self.priority_spin.valueChanged.connect(self._editor_changed)
        form.addWidget(self.priority_label, 7, 0)
        form.addWidget(self.priority_spin, 7, 1)

        self.enabled_checkbox = QCheckBox()
        self.enabled_checkbox.toggled.connect(self._editor_changed)
        form.addWidget(self.enabled_checkbox, 8, 1)
        self.favorite_checkbox = QCheckBox()
        self.favorite_checkbox.toggled.connect(self._editor_changed)
        form.addWidget(self.favorite_checkbox, 9, 1)
        self.stats_label = QLabel()
        self.stats_label.setProperty("kind", "muted")
        form.addWidget(self.stats_label, 10, 1)
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
        self.template_assistant_button = QPushButton()
        self.template_assistant_button.clicked.connect(
            self.open_template_assistant
        )
        variable_row.addWidget(self.template_assistant_button)
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
        preview_actions = QHBoxLayout()
        preview_actions.addStretch(1)
        self.copy_preview_button = QPushButton()
        self.copy_preview_button.clicked.connect(self.copy_preview)
        preview_actions.addWidget(self.copy_preview_button)
        preview_layout.addLayout(preview_actions)
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
        self.new_from_clipboard_action.setText(
            self.t("new_from_clipboard")
        )
        self.duplicate_action.setText(self.t("duplicate"))
        self.delete_action.setText(self.t("delete"))
        self.quick_search_action.setText(self.t("quick_access"))
        self.bulk_button.setText(self.t("bulk_actions"))
        self.bulk_enable_action.setText(self.t("bulk_enable"))
        self.bulk_disable_action.setText(self.t("bulk_disable"))
        self.bulk_favorite_action.setText(self.t("bulk_favorite"))
        self.bulk_unfavorite_action.setText(self.t("bulk_unfavorite"))
        self.bulk_category_action.setText(self.t("bulk_category"))
        self.bulk_export_action.setText(self.t("bulk_export"))
        self.bulk_delete_action.setText(self.t("bulk_delete"))
        self.import_action.setText(self.t("import"))
        self.export_action.setText(self.t("export"))
        self.export_filtered_action.setText(self.t("export_filtered"))
        self.restore_action.setText(self.t("backups"))
        self.data_action.setText(self.t("data_maintenance"))
        self.categories_action.setText(self.t("manage_categories"))
        self.statistics_action.setText(self.t("statistics"))
        self.active_action.setText(self.t("engine_active"))
        self.settings_action.setText(self.t("settings"))
        self.libraries_action.setText(self.t("library_manager"))
        self.quit_action.setText(self.t("quit"))
        self.licenses_action.setText(self.t("data_licenses"))
        self.about_action.setText(self.t("about"))
        self.file_menu.setTitle(self.t("menu_file"))
        self.snippets_menu.setTitle(self.t("menu_snippets"))
        self.libraries_menu.setTitle(self.t("libraries"))
        self.tools_menu.setTitle(self.t("menu_tools"))
        self.help_menu.setTitle(self.t("menu_help"))
        for definition in LIBRARY_DEFINITIONS:
            self.library_toggle_actions[definition.library_id].setText(
                self.t(definition.name_key)
            )
        self.search_edit.setPlaceholderText(self.t("search_placeholder"))
        self.search_edit.setToolTip(self.t("search_tooltip"))
        self.search_edit.setAccessibleName(self.t("search_accessible"))
        self.category_filter.setAccessibleName(
            self.t("category_filter_accessible")
        )
        self.table.setAccessibleName(self.t("snippet_table_accessible"))
        self.expansion_edit.setAccessibleName(
            self.t("expansion_accessible")
        )
        self.bulk_button.setAccessibleName(self.t("bulk_actions"))
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
        self.kind_label.setText(self.t("snippet_kind"))
        self.mode_label.setText(self.t("trigger_mode"))
        self.applications_label.setText(self.t("applications"))
        self.applications_edit.setPlaceholderText(self.t("applications_help"))
        self.description_label.setText(self.t("description"))
        self.description_edit.setPlaceholderText(self.t("description_help"))
        self.search_terms_label.setText(self.t("search_terms"))
        self.search_terms_edit.setPlaceholderText(self.t("search_terms_help"))
        self.priority_label.setText(self.t("regex_priority"))
        current_kind = self.kind_combo.currentData()
        with QSignalBlocker(self.kind_combo):
            self.kind_combo.clear()
            self.kind_combo.addItem(
                self.t("snippet_kind_literal"),
                SnippetKind.LITERAL.value,
            )
            self.kind_combo.addItem(
                self.t("snippet_kind_regex"),
                SnippetKind.REGEX.value,
            )
            kind_index = self.kind_combo.findData(current_kind)
            self.kind_combo.setCurrentIndex(max(0, kind_index))
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
        self.template_assistant_button.setText(self.t("template_assistant"))
        self.preview_group.setTitle(self.t("preview"))
        self.copy_preview_button.setText(self.t("copy_rendered"))
        self.copy_preview_button.setToolTip(self.t("copy_rendered_tooltip"))
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
        self._update_kind_controls()

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
        sorting_enabled = self.table.isSortingEnabled()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
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
                usage = NumericTableWidgetItem(str(snippet.usage_count))
                usage.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                usage.setData(ID_ROLE, snippet.id)
                self.table.setItem(row, 0, favorite)
                self.table.setItem(row, 1, enabled)
                self.table.setItem(row, 2, abbreviation)
                self.table.setItem(row, 3, category)
                self.table.setItem(row, 4, mode)
                self.table.setItem(row, 5, usage)
        self.table.setSortingEnabled(sorting_enabled)
        if sorting_enabled and sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        self.apply_filter(self.search_edit.text())
        if selected_id is not None:
            self._select_id(selected_id)
        self._update_empty_state()

    def apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        selected_category = self.category_filter.currentData()
        visible_count = 0
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
                    or needle in snippet.description.casefold()
                    or any(
                        needle in term.casefold()
                        for term in snippet.search_terms
                    )
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
            if visible:
                visible_count += 1
        self.filter_count_label.setText(
            self.t(
                "filter_count",
                visible=visible_count,
                total=len(self.snippets),
            )
        )

    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def clear_filters(self) -> None:
        had_filter = bool(self.search_edit.text()) or (
            self.category_filter.currentData() is not None
        )
        if not had_filter:
            return
        with (
            QSignalBlocker(self.search_edit),
            QSignalBlocker(self.category_filter),
        ):
            self.search_edit.clear()
            self.category_filter.setCurrentIndex(0)
        self.apply_filter("")
        self.focus_search()
        self.status_message.setText(self.t("filters_cleared"))

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
                QSignalBlocker(self.kind_combo),
                QSignalBlocker(self.mode_combo),
                QSignalBlocker(self.applications_edit),
                QSignalBlocker(self.description_edit),
                QSignalBlocker(self.search_terms_edit),
                QSignalBlocker(self.priority_spin),
                QSignalBlocker(self.enabled_checkbox),
                QSignalBlocker(self.favorite_checkbox),
                QSignalBlocker(self.expansion_edit),
            ):
                self.abbreviation_edit.setText(snippet.abbreviation)
                self.category_combo.setEditText(snippet.category)
                self.kind_combo.setCurrentIndex(
                    self.kind_combo.findData(snippet.kind.value)
                )
                self.mode_combo.setCurrentIndex(
                    self.mode_combo.findData(snippet.trigger_mode.value)
                )
                self.applications_edit.setText(", ".join(snippet.applications))
                self.description_edit.setText(snippet.description)
                self.search_terms_edit.setText(", ".join(snippet.search_terms))
                self.priority_spin.setValue(snippet.priority)
                self.enabled_checkbox.setChecked(snippet.enabled)
                self.favorite_checkbox.setChecked(snippet.favorite)
                self.expansion_edit.setPlainText(snippet.expansion)
            self.editor_panel.setEnabled(True)
            self._dirty = False
            self._update_stats_label(snippet)
            self._update_kind_controls()
            self.update_preview()
        finally:
            self._selection_guard = False

    def new_snippet(self) -> None:
        self._begin_new_snippet("")

    def new_snippet_from_clipboard(self) -> None:
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            self.status_message.setText(self.t("clipboard_has_no_text"))
            return
        if self._begin_new_snippet(clipboard_text):
            self.status_message.setText(
                self.t(
                    "clipboard_snippet_ready",
                    count=len(clipboard_text),
                )
            )

    def _begin_new_snippet(self, expansion: str) -> bool:
        if not self._maybe_resolve_dirty():
            return False
        self._selection_guard = True
        try:
            self.table.clearSelection()
            self._current_id = None
            self._is_new = True
            self.editor_panel.setEnabled(True)
            self.abbreviation_edit.clear()
            self.category_combo.setEditText("")
            self.kind_combo.setCurrentIndex(
                self.kind_combo.findData(SnippetKind.LITERAL.value)
            )
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(TriggerMode.DELIMITER.value))
            self.applications_edit.clear()
            self.description_edit.clear()
            self.search_terms_edit.clear()
            self.priority_spin.setValue(0)
            self.enabled_checkbox.setChecked(True)
            self.favorite_checkbox.setChecked(False)
            self.expansion_edit.setPlainText(expansion)
            self._dirty = bool(expansion)
            self.abbreviation_edit.setFocus()
            self.update_preview()
            self._update_kind_controls()
        finally:
            self._selection_guard = False
        return True

    def save_current(self) -> bool:
        if not self._is_new and self._current_id is None:
            return False
        abbreviation = self.abbreviation_edit.text()
        kind = SnippetKind(str(self.kind_combo.currentData()))
        issues = validate_snippet_trigger(abbreviation, kind)
        if issues:
            key_by_code = {
                "required": "required_abbreviation",
                "whitespace": "whitespace_abbreviation",
                "too_long": "long_abbreviation",
                "control": "control_abbreviation",
                "invalid_regex": "invalid_regex",
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
        try:
            search_terms = normalize_search_terms(
                tuple(
                    item.strip()
                    for item in self.search_terms_edit.text()
                    .replace(";", ",")
                    .split(",")
                )
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                str(error),
            )
            return False
        template_issues = self._current_template_issues()
        if template_issues:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(
                    "template_issues",
                    issues=", ".join(
                        self._describe_issue(issue)
                        for issue in template_issues
                    ),
                ),
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
            kind=kind,
            description=self.description_edit.text().strip(),
            search_terms=search_terms,
            priority=self.priority_spin.value(),
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

    def selected_snippet_ids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows(2)
        identifiers: list[int] = []
        for index in sorted(rows, key=lambda item: item.row()):
            value = index.data(ID_ROLE)
            if isinstance(value, int):
                identifiers.append(value)
        return identifiers

    def selected_snippets(self) -> list[Snippet]:
        identifiers = set(self.selected_snippet_ids())
        return [
            snippet
            for snippet in self.snippets
            if snippet.id in identifiers
        ]

    def bulk_set_enabled(self, enabled: bool) -> None:
        self._apply_bulk_update(enabled=enabled)

    def bulk_set_favorite(self, favorite: bool) -> None:
        self._apply_bulk_update(favorite=favorite)

    def bulk_change_category(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        identifiers = self.selected_snippet_ids()
        if not identifiers:
            return
        category, accepted = QInputDialog.getText(
            self,
            self.t("bulk_category_title"),
            self.t("bulk_category_prompt", count=len(identifiers)),
        )
        if not accepted:
            return
        try:
            changed = self.storage.update_snippets(
                identifiers,
                category=category,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                str(error),
            )
            return
        self._finish_bulk_change(changed)

    def _apply_bulk_update(
        self,
        *,
        enabled: bool | None = None,
        favorite: bool | None = None,
    ) -> None:
        if not self._maybe_resolve_dirty():
            return
        identifiers = self.selected_snippet_ids()
        if not identifiers:
            return
        changed = self.storage.update_snippets(
            identifiers,
            enabled=enabled,
            favorite=favorite,
        )
        self._finish_bulk_change(changed)

    def _finish_bulk_change(self, changed: int) -> None:
        self._current_id = None
        self._dirty = False
        self._is_new = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t("bulk_updated", count=changed)
        )

    def export_selected_snippets(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        snippets = self.selected_snippets()
        if not snippets:
            return
        self._export_snippet_collection(
            snippets,
            f"QuickType-selected-{datetime.now():%Y%m%d}.json",
        )

    def delete_selected(self) -> None:
        identifiers = self.selected_snippet_ids()
        if len(identifiers) <= 1:
            self.delete_current()
            return
        if not self._maybe_resolve_dirty():
            return
        answer = QMessageBox.question(
            self,
            self.t("bulk_delete_title"),
            self.t("bulk_delete_text", count=len(identifiers)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.delete_snippets(identifiers)
        self._current_id = None
        self._dirty = False
        self._is_new = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t("bulk_deleted", count=deleted)
        )

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
        selected_rows = {
            index.row()
            for index in self.table.selectionModel().selectedRows()
        }
        if item.row() not in selected_rows:
            self.table.selectRow(item.row())
        if len(self.selected_snippet_ids()) > 1:
            self.bulk_menu.exec(self.table.viewport().mapToGlobal(position))
            return
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
        copy_action = menu.addAction(self.t("copy_rendered"))
        duplicate_action = menu.addAction(self.t("duplicate"))
        delete_action = menu.addAction(self.t("delete"))
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == enabled_action:
            self.toggle_current_enabled()
        elif selected == favorite_action:
            self.toggle_current_favorite()
        elif selected == copy_action:
            self.copy_snippet_rendered(source)
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
        self._export_snippet_collection(
            self.storage.list_snippets(),
            f"QuickType-backup-{datetime.now():%Y%m%d}.json",
        )

    def export_filtered_snippets(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        snippets = self.filtered_snippets()
        if not snippets:
            QMessageBox.information(
                self,
                self.t("export_filtered"),
                self.t("no_filtered_snippets"),
            )
            return
        self._export_snippet_collection(
            snippets,
            f"QuickType-filtered-{datetime.now():%Y%m%d}.json",
        )

    def filtered_snippets(self) -> list[Snippet]:
        by_id = {snippet.id: snippet for snippet in self.snippets}
        result: list[Snippet] = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 2)
            snippet = by_id.get(item.data(ID_ROLE)) if item else None
            if snippet is not None:
                result.append(snippet)
        return result

    def _export_snippet_collection(
        self,
        snippets: list[Snippet],
        default_filename: str,
    ) -> None:
        default_name = self.storage.path.parent / default_filename
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.t("export_title"),
            str(default_name),
            self.t("backup_filter"),
        )
        if not path:
            return
        try:
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
        try:
            analysis = analyze_import(self.storage, Path(path))
        except (OSError, ValueError, BackupFormatError) as error:
            QMessageBox.warning(self, self.t("import_error_title"), str(error))
            return
        dialog = ImportPreviewDialog(analysis, self.t, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = apply_import(
                self.storage,
                analysis,
                mode=dialog.import_mode,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, self.t("import_error_title"), str(error))
            return
        self._current_id = None
        self._is_new = False
        self._dirty = False
        self.reload_snippets()
        self.snippets_changed.emit()
        self.status_message.setText(
            self.t(
                "import_success_with_safety",
                added=result.added,
                updated=result.updated,
                skipped=result.skipped,
                safety=result.safety_copy.name,
            )
        )

    def restore_automatic_backup(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        dialog = BackupRestoreDialog(
            self.storage,
            self.t,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = dialog.selected_path
        analysis = dialog.selected_analysis
        if path is None or analysis is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("restore_confirm_title"),
            self.t(
                "restore_confirm_text_detailed",
                file=path.name,
                added=analysis.added,
                updated=analysis.updated,
                removed=analysis.removed,
                unchanged=analysis.unchanged,
            ),
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

    def open_builtin_libraries(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        dialog = BuiltinLibraryManagerDialog(self.catalog, self.t, self)
        dialog.settings_changed.connect(self._builtin_settings_changed)
        dialog.snippets_changed.connect(self._builtin_snippet_copied)
        dialog.exec()

    def _builtin_settings_changed(self) -> None:
        for library_id, action in self.library_toggle_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self.catalog.settings(library_id).enabled)
        self.builtin_libraries_changed.emit()
        self.status_message.setText(self.t("library_settings_saved"))

    def _builtin_snippet_copied(self) -> None:
        self.reload_snippets()
        self.snippets_changed.emit()

    def _set_library_enabled(
        self,
        library_id: BuiltinLibraryId,
        enabled: bool,
    ) -> None:
        current = self.catalog.settings(library_id)
        try:
            self.catalog.set_settings(
                library_id,
                BuiltinLibrarySettings(
                    enabled=enabled,
                    profile=current.profile,
                    prefix=current.prefix,
                ),
            )
        except ValueError as error:
            with QSignalBlocker(self.library_toggle_actions[library_id]):
                self.library_toggle_actions[library_id].setChecked(
                    current.enabled
                )
            QMessageBox.warning(
                self,
                self.t("validation_title"),
                self.t(
                    "library_settings_error",
                    error=self.t(
                        f"library_error_{error.code}"
                        if isinstance(error, BuiltinLibrarySettingsError)
                        else "library_error_unknown"
                    ),
                ),
            )
            return
        self.builtin_libraries_changed.emit()
        self.status_message.setText(self.t("library_settings_saved"))

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

    def open_data_maintenance(self) -> None:
        if not self._maybe_resolve_dirty():
            return
        DataMaintenanceDialog(self.storage, self.t, self).exec()

    def open_data_licenses(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t("data_licenses"))
        dialog.resize(760, 590)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        try:
            text.setPlainText(
                resource_path("DATA_LICENSES.md").read_text(encoding="utf-8")
            )
        except OSError as error:
            text.setPlainText(str(error))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            self.t("about"),
            self.t("about_text", version=APP_VERSION),
        )

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

    def open_template_assistant(self) -> None:
        dialog = TemplateAssistantDialog(self.t, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cursor = self.expansion_edit.textCursor()
        cursor.insertText(dialog.token)
        self.expansion_edit.setTextCursor(cursor)
        self.expansion_edit.setFocus()

    def copy_preview(self) -> None:
        if not self.editor_panel.isEnabled():
            return
        self._copy_rendered_text(
            self.expansion_edit.toPlainText(),
            self.t("preview_copied"),
        )

    def copy_current_rendered(self) -> None:
        if self._current_id is None:
            return
        snippet = next(
            (entry for entry in self.snippets if entry.id == self._current_id),
            None,
        )
        if snippet is not None:
            self.copy_snippet_rendered(snippet)

    def copy_snippet_rendered(self, snippet: Snippet) -> None:
        self._copy_rendered_text(
            snippet.expansion,
            self.t("snippet_copied", abbr=snippet.abbreviation),
        )

    def _copy_rendered_text(self, template: str, message: str) -> None:
        clipboard = QApplication.clipboard()
        rendered = render_template(
            template,
            clipboard_text=clipboard.text(),
        )
        clipboard.setText(rendered.text)
        self.status_message.setText(message)

    def _editor_changed(self, *_args: object) -> None:
        if self._selection_guard:
            return
        self._dirty = True
        self.update_preview()

    def _snippet_kind_changed(self, *_args: object) -> None:
        self._update_kind_controls()
        self._editor_changed()

    def _update_kind_controls(self) -> None:
        if not hasattr(self, "kind_combo"):
            return
        is_regex = self.kind_combo.currentData() == SnippetKind.REGEX.value
        self.abbreviation_edit.setMaxLength(512 if is_regex else 64)
        self.abbreviation_label.setText(
            self.t("regex_pattern") if is_regex else self.t("abbreviation")
        )
        self.priority_label.setVisible(is_regex)
        self.priority_spin.setVisible(is_regex)

    def update_preview(self) -> None:
        if not hasattr(self, "preview_edit"):
            return
        template = self.expansion_edit.toPlainText()
        provider = self._editor_snippet_provider()
        match_groups = self._editor_match_groups()
        rendered = render_template(
            template,
            clipboard_text="",
            snippet_provider=provider,
            match_groups=match_groups,
        )
        self.preview_edit.setPlainText(rendered.text)
        issues = inspect_template(
            template,
            snippet_provider=provider,
            match_groups=match_groups,
        )
        if issues:
            descriptions = ", ".join(self._describe_issue(issue) for issue in issues)
            self.issue_label.setText(self.t("template_issues", issues=descriptions))
            self.issue_label.setProperty("kind", "error")
        else:
            self.issue_label.setText(self.t("template_ok"))
            self.issue_label.setProperty("kind", "success")
        self.issue_label.style().unpolish(self.issue_label)
        self.issue_label.style().polish(self.issue_label)

    def _current_template_issues(self) -> tuple[TemplateIssue, ...]:
        return inspect_template(
            self.expansion_edit.toPlainText(),
            snippet_provider=self._editor_snippet_provider(),
            match_groups=self._editor_match_groups(),
        )

    def _editor_snippet_provider(self) -> Callable[[str], str | None]:
        current_abbreviation = self.abbreviation_edit.text()
        current_expansion = self.expansion_edit.toPlainText()
        snippets = {
            snippet.abbreviation: snippet.expansion
            for snippet in self.snippets
            if snippet.enabled
        }
        if current_abbreviation:
            snippets[current_abbreviation] = current_expansion
        return snippets.get

    def _editor_match_groups(self) -> dict[str, str]:
        if self.kind_combo.currentData() != SnippetKind.REGEX.value:
            return {}
        try:
            pattern = regex.compile(
                self.abbreviation_edit.text(),
                flags=regex.VERSION1,
            )
        except regex.error:
            return {}
        groups = {
            str(index): ""
            for index in range(0, pattern.groups + 1)
        }
        groups.update({name: "" for name in pattern.groupindex})
        return groups

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
            backup_retention=self.backup_retention,
            theme=self.theme,
            excluded_processes=self.excluded_processes,
            database_path=self.storage.path,
            quick_access_hotkey=self.quick_access_hotkey,
            clipboard_capture_hotkey=self.clipboard_capture_hotkey,
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
        if dialog.selected_backup_retention != self.backup_retention:
            self.backup_retention_change_requested.emit(
                dialog.selected_backup_retention
            )
        if dialog.selected_theme != self.theme:
            self.theme_change_requested.emit(dialog.selected_theme)
        if dialog.selected_excluded_processes != self.excluded_processes:
            self.excluded_processes_change_requested.emit(
                dialog.selected_excluded_processes
            )
        if dialog.selected_quick_access_hotkey != self.quick_access_hotkey:
            self.quick_access_hotkey_change_requested.emit(
                dialog.selected_quick_access_hotkey
            )
        if (
            dialog.selected_clipboard_capture_hotkey
            != self.clipboard_capture_hotkey
        ):
            self.clipboard_capture_hotkey_change_requested.emit(
                dialog.selected_clipboard_capture_hotkey
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

    def set_backup_retention(self, retention: int) -> None:
        self.backup_retention = retention

    def set_theme(self, theme: str) -> None:
        self.theme = normalize_theme(theme)

    def set_excluded_processes(self, processes: set[str]) -> None:
        self.excluded_processes = set(processes)

    def set_quick_access_hotkey(self, hotkey: str) -> None:
        self.quick_access_hotkey = normalize_quick_access_hotkey(hotkey)

    def set_clipboard_capture_hotkey(self, hotkey: str) -> None:
        self.clipboard_capture_hotkey = (
            normalize_clipboard_capture_hotkey(hotkey)
        )

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

    def _save_ui_state(self) -> None:
        values = {
            "ui_main_geometry": self.saveGeometry(),
            "ui_main_header": self.table.horizontalHeader().saveState(),
            "ui_main_splitter": self.main_splitter.saveState(),
        }
        for key, value in values.items():
            self.storage.set_setting(
                key,
                bytes(value.toBase64()).decode("ascii"),
            )

    def _restore_ui_state(self) -> None:
        values = (
            ("ui_main_geometry", self.restoreGeometry),
            (
                "ui_main_header",
                self.table.horizontalHeader().restoreState,
            ),
            ("ui_main_splitter", self.main_splitter.restoreState),
        )
        for key, restore in values:
            encoded = self.storage.get_setting(key)
            if not encoded:
                continue
            restore(QByteArray.fromBase64(encoded.encode("ascii")))

    def prepare_quit(self) -> bool:
        if not self._maybe_resolve_dirty():
            return False
        self._save_ui_state()
        self._allow_close = True
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            self._save_ui_state()
            event.accept()
            return
        if not self._maybe_resolve_dirty():
            event.ignore()
            return
        self._save_ui_state()
        self.hide()
        event.ignore()


class TrayController:
    def __init__(
        self,
        translator: Translator,
        *,
        active: bool,
        on_open: Callable[[], None],
        on_new_from_clipboard: Callable[[], None],
        on_active: Callable[[bool], None],
        on_autostart: Callable[[bool], None],
        on_quit: Callable[[], None],
        autostart: bool,
    ) -> None:
        self.t = translator
        self.active = active
        self.autostart = autostart
        self.on_open = on_open
        self.on_new_from_clipboard = on_new_from_clipboard
        self.on_active = on_active
        self.on_autostart = on_autostart
        self.on_quit = on_quit
        self.tray = QSystemTrayIcon(QIcon(str(resource_path("quicktype.svg"))))
        self.menu = QMenu()
        self.open_action = QAction()
        self.open_action.triggered.connect(on_open)
        self.menu.addAction(self.open_action)
        self.new_from_clipboard_action = QAction()
        self.new_from_clipboard_action.triggered.connect(
            on_new_from_clipboard
        )
        self.menu.addAction(self.new_from_clipboard_action)
        self.menu.addSeparator()
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
        self.new_from_clipboard_action.setText(
            self.t("new_from_clipboard")
        )
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


def apply_application_style(
    application: QApplication,
    theme: str = "light",
) -> None:
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 9))
    selected = normalize_theme(theme)
    if selected == "dark":
        colors = {
            "background": "#1e2027",
            "panel": "#272a33",
            "input": "#30343f",
            "header": "#383d49",
            "border": "#505767",
            "text": "#f2f4f8",
            "muted": "#b5bdcb",
            "accent": "#9b87ff",
            "accent_text": "#ffffff",
            "warning_text": "#ffd57a",
            "warning_bg": "#4a3a16",
            "error": "#ff8d86",
            "success": "#72d6a5",
        }
    elif selected == "high_contrast":
        colors = {
            "background": "#000000",
            "panel": "#000000",
            "input": "#000000",
            "header": "#000000",
            "border": "#ffffff",
            "text": "#ffffff",
            "muted": "#ffffff",
            "accent": "#ffff00",
            "accent_text": "#000000",
            "warning_text": "#ffff00",
            "warning_bg": "#000000",
            "error": "#ff8080",
            "success": "#80ff80",
        }
    else:
        colors = {
            "background": "#f6f7fb",
            "panel": "#ffffff",
            "input": "#ffffff",
            "header": "#eceef5",
            "border": "#d7dbe7",
            "text": "#252735",
            "muted": "#676d7e",
            "accent": "#6548dc",
            "accent_text": "#ffffff",
            "warning_text": "#9a6400",
            "warning_bg": "#fff4d6",
            "error": "#b42318",
            "success": "#18794e",
        }
    application.setStyleSheet(
        f"""
        QWidget {{
            color: {colors["text"]};
        }}
        QMainWindow, QDialog {{
            background: {colors["background"]};
        }}
        QToolBar, QStatusBar, QMenu {{
            background: {colors["panel"]};
            border-color: {colors["border"]};
        }}
        QToolBar {{
            border: none;
            border-bottom: 1px solid {colors["border"]};
            padding: 7px;
            spacing: 7px;
        }}
        QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {{
            background: {colors["input"]};
            border: 1px solid {colors["border"]};
            border-radius: 6px;
            padding: 6px;
            selection-background-color: {colors["accent"]};
            selection-color: {colors["accent_text"]};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
        QSpinBox:focus, QTableWidget:focus {{
            border: 2px solid {colors["accent"]};
        }}
        QTableWidget {{
            gridline-color: transparent;
        }}
        QHeaderView::section {{
            background: {colors["header"]};
            border: none;
            padding: 7px;
            font-weight: 600;
        }}
        QPushButton, QToolButton {{
            background: {colors["panel"]};
            border: 1px solid {colors["border"]};
            border-radius: 6px;
            padding: 7px 12px;
        }}
        QPushButton:hover, QToolButton:hover {{
            border-color: {colors["accent"]};
        }}
        QPushButton:default {{
            background: {colors["accent"]};
            color: {colors["accent_text"]};
            border-color: {colors["accent"]};
        }}
        QStatusBar {{
            border-top: 1px solid {colors["border"]};
        }}
        QLabel[kind="muted"] {{ color: {colors["muted"]}; }}
        QLabel[kind="warning"] {{
            color: {colors["warning_text"]};
            background: {colors["warning_bg"]};
            padding: 8px;
            border-radius: 5px;
        }}
        QLabel[kind="error"] {{ color: {colors["error"]}; }}
        QLabel[kind="success"] {{ color: {colors["success"]}; }}
        QLabel[kind="emptyTitle"] {{
            font-size: 17px;
            font-weight: 600;
            color: {colors["text"]};
        }}
        """
    )


def show_about(parent: QWidget, translator: Translator) -> None:
    QMessageBox.about(
        parent,
        translator("about"),
        translator("about_text", version=APP_VERSION),
    )
