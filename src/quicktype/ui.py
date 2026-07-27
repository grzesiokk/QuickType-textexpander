from __future__ import annotations

from collections.abc import Callable
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

from .backup import BackupFormatError, export_backup, import_backup
from .constants import APP_NAME, APP_VERSION, resource_path
from .i18n import Translator
from .models import Snippet, TriggerMode, validate_abbreviation
from .storage import DuplicateAbbreviationError, Storage
from .template_engine import TemplateIssue, inspect_template, render_template

ID_ROLE = Qt.ItemDataRole.UserRole


class EngineSignals(QObject):
    expanded = Signal(object)
    error = Signal(str)


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


class MainWindow(QMainWindow):
    language_change_requested = Signal(str)
    active_change_requested = Signal(bool)
    autostart_change_requested = Signal(bool)
    excluded_processes_change_requested = Signal(object)
    snippets_changed = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        translator: Translator,
        *,
        engine_active: bool,
        autostart: bool,
        excluded_processes: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.storage = storage
        self.t = translator
        self.engine_active = engine_active
        self.autostart = autostart
        self.excluded_processes = set(excluded_processes or set())
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
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 44)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 70)
        self.table.itemSelectionChanged.connect(self._selection_changed)
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

        self.mode_label = QLabel()
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._editor_changed)
        form.addWidget(self.mode_label, 1, 0)
        form.addWidget(self.mode_combo, 1, 1)

        self.enabled_checkbox = QCheckBox()
        self.enabled_checkbox.toggled.connect(self._editor_changed)
        form.addWidget(self.enabled_checkbox, 2, 1)
        self.stats_label = QLabel()
        self.stats_label.setProperty("kind", "muted")
        form.addWidget(self.stats_label, 3, 1)
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
        self.delete_action.setText(self.t("delete"))
        self.import_action.setText(self.t("import"))
        self.export_action.setText(self.t("export"))
        self.active_action.setText(self.t("engine_active"))
        self.settings_action.setText(self.t("settings"))
        self.search_edit.setPlaceholderText(self.t("search_placeholder"))
        self.table.setHorizontalHeaderLabels(
            [
                self.t("active_column"),
                self.t("shortcut_column"),
                self.t("mode_column"),
                self.t("usage_column"),
            ]
        )
        self.empty_title.setText(self.t("empty_title"))
        self.empty_text.setText(self.t("empty_text"))
        self.abbreviation_label.setText(self.t("abbreviation"))
        self.mode_label.setText(self.t("trigger_mode"))
        current_mode = self.mode_combo.currentData()
        with QSignalBlocker(self.mode_combo):
            self.mode_combo.clear()
            self.mode_combo.addItem(self.t("immediate"), TriggerMode.IMMEDIATE.value)
            self.mode_combo.addItem(self.t("delimiter"), TriggerMode.DELIMITER.value)
            mode_index = self.mode_combo.findData(current_mode)
            self.mode_combo.setCurrentIndex(max(0, mode_index))
        self.enabled_checkbox.setText(self.t("enabled"))
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
                enabled = QTableWidgetItem("●" if snippet.enabled else "○")
                enabled.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                enabled.setData(ID_ROLE, snippet.id)
                abbreviation = QTableWidgetItem(snippet.abbreviation)
                abbreviation.setData(ID_ROLE, snippet.id)
                mode = QTableWidgetItem(
                    self.t("immediate")
                    if snippet.trigger_mode == TriggerMode.IMMEDIATE
                    else self.t("delimiter")
                )
                mode.setData(ID_ROLE, snippet.id)
                usage = QTableWidgetItem(str(snippet.usage_count))
                usage.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                usage.setData(ID_ROLE, snippet.id)
                self.table.setItem(row, 0, enabled)
                self.table.setItem(row, 1, abbreviation)
                self.table.setItem(row, 2, mode)
                self.table.setItem(row, 3, usage)
        self.apply_filter(self.search_edit.text())
        if selected_id is not None:
            self._select_id(selected_id)
        self._update_empty_state()

    def apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            snippet_id = item.data(ID_ROLE)
            snippet = next((entry for entry in self.snippets if entry.id == snippet_id), None)
            visible = not needle or (
                snippet is not None
                and (
                    needle in snippet.abbreviation.casefold()
                    or needle in snippet.expansion.casefold()
                )
            )
            self.table.setRowHidden(row, not visible)

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
                    if snippet and self._current_id != snippet_id:
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
                QSignalBlocker(self.mode_combo),
                QSignalBlocker(self.enabled_checkbox),
                QSignalBlocker(self.expansion_edit),
            ):
                self.abbreviation_edit.setText(snippet.abbreviation)
                self.mode_combo.setCurrentIndex(
                    self.mode_combo.findData(snippet.trigger_mode.value)
                )
                self.enabled_checkbox.setChecked(snippet.enabled)
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
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(TriggerMode.DELIMITER.value))
            self.enabled_checkbox.setChecked(True)
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
        snippet = Snippet(
            id=self._current_id,
            abbreviation=abbreviation,
            expansion=self.expansion_edit.toPlainText(),
            trigger_mode=TriggerMode(str(self.mode_combo.currentData())),
            enabled=self.enabled_checkbox.isChecked(),
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
            excluded_processes=self.excluded_processes,
            database_path=self.storage.path,
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
        if dialog.selected_excluded_processes != self.excluded_processes:
            self.excluded_processes_change_requested.emit(
                dialog.selected_excluded_processes
            )

    def set_engine_active(self, active: bool) -> None:
        self.engine_active = active
        with QSignalBlocker(self.active_action):
            self.active_action.setChecked(active)
        self.status_state.setText(self.t("status_active") if active else self.t("status_paused"))

    def set_autostart(self, enabled: bool) -> None:
        self.autostart = enabled

    def set_excluded_processes(self, processes: set[str]) -> None:
        self.excluded_processes = set(processes)

    def refresh_usage(self, snippet: Snippet) -> None:
        self.snippets = [
            snippet if entry.id == snippet.id else entry for entry in self.snippets
        ]
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(ID_ROLE) == snippet.id:
                self.table.item(row, 3).setText(str(snippet.usage_count))
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
