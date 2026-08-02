"""
settings_window.py

The main configuration/settings app: a normal QMainWindow (taskbar,
resizable, standard title bar -- unlike every other window in this app,
which is intentionally frameless/always-on-top). Left nav rail +
stacked content area, one tab per settings section.

Reached via tray_icon.py (left-click, or "Open Settings" in the tray
menu). Closing the window just hides it -- the app keeps running in
the tray, and reopening shows the same instance (so e.g. in-progress
model downloads in the Local Models tab survive the window being
closed and reopened).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QMetaObject, QThread, Qt, Signal, Q_ARG
from PySide6.QtGui import QColor, QFont, QKeySequence
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDialog,
)

import common.config as config_module
from core.model_download_worker import ModelDownloadWorker
from core.model_manager import MODEL_CATALOG, gpu_offload_supported, max_gpu_layers_for


# ============================================================
# Hotkey string <-> QKeySequence conversion
#
# Our hotkey format ("ctrl+alt+space") only ever describes zero-or-more
# modifiers plus ONE final key (hotkeys.py's own parser only keeps the
# last non-modifier token), which maps cleanly onto a single-chord
# QKeySequence.
# ============================================================

_MOD_TO_QT = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Meta"}
_MOD_FROM_QT = {v: k for k, v in _MOD_TO_QT.items()}


def hotkey_to_qkeysequence(hotkey: str) -> QKeySequence:

    parts = [p.strip() for p in hotkey.split("+") if p.strip()]

    qt_parts = []

    for p in parts:
        lower = p.lower()
        if lower in _MOD_TO_QT:
            qt_parts.append(_MOD_TO_QT[lower])
        elif len(p) == 1:
            qt_parts.append(p.upper())
        else:
            qt_parts.append(p.capitalize())

    return QKeySequence("+".join(qt_parts))


def qkeysequence_to_hotkey(seq: QKeySequence) -> str:

    text = seq.toString(QKeySequence.SequenceFormat.PortableText)

    if not text:
        return ""

    parts = text.split("+")

    out = [_MOD_FROM_QT.get(p, p.lower()) for p in parts]

    return "+".join(out)


# ============================================================
# Theming
# ============================================================

def build_stylesheet(theme: str, accent: str) -> str:

    if theme == "light":
        bg, bg2, fg, border = "#F5F5F7", "#FFFFFF", "#1A1A1A", "#D8D8DC"
    else:
        bg, bg2, fg, border = "#1B1B1F", "#232328", "#F2F2F2", "#35353D"

    return f"""
        QMainWindow, QWidget {{
            background-color: {bg};
            color: {fg};
        }}
        QListWidget {{
            background-color: {bg2};
            border: none;
            outline: none;
        }}
        QListWidget::item {{
            padding: 12px 18px;
            border: none;
            background: transparent;
            font-family: 'Segoe UI Semibold', 'Segoe UI';
            font-size: 14px;
            font-weight: 500;
        }}
        QListWidget::item:hover {{
            color: {accent};
        }}
        QListWidget::item:selected {{
            color: {accent};
            background: transparent;
            font-weight: 700;
        }}
        QGroupBox {{
            border: 1px solid {border};
            margin-top: 12px;
            padding-top: 12px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
        }}
        QPushButton {{
            background-color: {bg2};
            border: 1px solid {border};
            padding: 6px 14px;
        }}
        QPushButton:hover {{
            background-color: {accent};
            color: white;
            border-color: {accent};
        }}
        QPushButton:disabled {{
            color: gray;
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QTextEdit {{
            background-color: {bg2};
            border: 1px solid {border};
            padding: 4px 6px;
        }}
        QTableWidget {{
            background-color: {bg2};
            border: 1px solid {border};
            gridline-color: {border};
        }}
        QHeaderView::section {{
            background-color: {bg};
            border: none;
            border-bottom: 1px solid {border};
            padding: 6px;
        }}
        QProgressBar {{
            border: 1px solid {border};
            background-color: {bg2};
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {accent};
        }}
    """


# ============================================================
# Callback bundle passed in from main.py
# ============================================================

class SettingsCallbacks:
    """
    Everything settings_window.py needs from the rest of the app,
    bundled so this module stays decoupled from main.py's internals.
    """

    def __init__(
        self,
        save_settings: Callable[[], None],
        reload_llm: Callable[[], None],
        reload_hotkeys: Callable[[], None],
        apply_theme: Callable[[], None],
        model_manager,
        history_db,
        is_busy: Callable[[], bool],
    ):
        self.save_settings = save_settings
        self.reload_llm = reload_llm
        self.reload_hotkeys = reload_hotkeys
        self.apply_theme = apply_theme
        self.model_manager = model_manager
        self.history_db = history_db
        self.is_busy = is_busy


# ============================================================
# Dashboard tab
# ============================================================

class DashboardTab(QWidget):

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self.welcome_label = QLabel()
        self.welcome_label.setStyleSheet("font-size: 22px; font-weight: 700;")
        root.addWidget(self.welcome_label)
        self._refresh_welcome()

        overview_group = QGroupBox("Overview")
        overview_form = QFormLayout(overview_group)

        self.backend_value = QLabel()
        overview_form.addRow("Active backend", self.backend_value)

        self.model_value = QLabel()
        overview_form.addRow("Active model", self.model_value)

        self.history_value = QLabel()
        overview_form.addRow("Saved conversations", self.history_value)

        root.addWidget(overview_group)
        self.refresh_overview()

        credits_group = QGroupBox("Credits")
        credits_layout = QVBoxLayout(credits_group)

        credits_note = QLabel(
            "Sign in to see your credit balance -- full details live in "
            "Account & Billing."
        )
        credits_note.setWordWrap(True)
        credits_note.setStyleSheet("color: gray;")
        credits_layout.addWidget(credits_note)

        buy_row = QHBoxLayout()
        buy_btn = QPushButton("Buy Credits")
        buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        buy_btn.clicked.connect(self._on_buy_credits)
        buy_row.addWidget(buy_btn)
        buy_row.addStretch(1)
        credits_layout.addLayout(buy_row)

        root.addWidget(credits_group)

        root.addStretch(1)

    # --------------------------------------------------------

    def _refresh_welcome(self) -> None:

        username = (self.settings.personalization.username or "").strip()

        if username:
            self.welcome_label.setText(f"Welcome, {username}!")
        else:
            self.welcome_label.setText("Welcome!")

    def refresh_overview(self) -> None:

        backend = self.settings.llm.backend

        self.backend_value.setText("Local model" if backend == "local" else "API")

        if backend == "local":
            self.model_value.setText(self.settings.llm.local.model_key)
        else:
            self.model_value.setText(self.settings.llm.api.model or "(not set)")

        try:
            count = len(self.callbacks.history_db.list_conversations())
        except Exception:
            count = 0

        self.history_value.setText(str(count))

    # --------------------------------------------------------
    # Called by SettingsWindow when Personalization saves a new username
    # --------------------------------------------------------

    def on_username_changed(self, username: str) -> None:
        self._refresh_welcome()

    # --------------------------------------------------------

    def _on_buy_credits(self) -> None:

        QMessageBox.information(
            self,
            "Buy Credits",
            "Credits and billing require an account backend that hasn't "
            "been built yet (Phase 2). This button will open checkout "
            "once accounts are live -- see the Account & Billing tab.",
        )


# ============================================================
# General tab
# ============================================================

class GeneralTab(QWidget):

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("General")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        behavior_group = QGroupBox("Behavior")
        behavior_form = QFormLayout(behavior_group)

        self.start_with_windows = QCheckBox("Start with Windows")
        self.start_with_windows.setChecked(settings.app.start_with_windows)
        behavior_form.addRow(self.start_with_windows)

        self.check_updates = QCheckBox("Check for updates automatically")
        self.check_updates.setChecked(settings.app.check_for_updates)
        behavior_form.addRow(self.check_updates)

        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level.setCurrentText(settings.logging.level)
        behavior_form.addRow("Log level", self.log_level)

        root.addWidget(behavior_group)

        capture_group = QGroupBox("Screen Capture")
        capture_form = QFormLayout(capture_group)

        self.monitor = QSpinBox()
        self.monitor.setRange(1, 8)
        self.monitor.setValue(settings.capture.monitor)
        capture_form.addRow("Monitor", self.monitor)

        self.crop_top = QDoubleSpinBox()
        self.crop_top.setRange(0, 20)
        self.crop_top.setSuffix(" cm")
        self.crop_top.setValue(settings.capture.crop.top_cm)
        capture_form.addRow("Crop top", self.crop_top)

        self.crop_bottom = QDoubleSpinBox()
        self.crop_bottom.setRange(0, 20)
        self.crop_bottom.setSuffix(" cm")
        self.crop_bottom.setValue(settings.capture.crop.bottom_cm)
        capture_form.addRow("Crop bottom", self.crop_bottom)

        root.addWidget(capture_group)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        self.settings.app.start_with_windows = self.start_with_windows.isChecked()
        self.settings.app.check_for_updates = self.check_updates.isChecked()
        self.settings.logging.level = self.log_level.currentText()
        self.settings.capture.monitor = self.monitor.value()
        self.settings.capture.crop.top_cm = self.crop_top.value()
        self.settings.capture.crop.bottom_cm = self.crop_bottom.value()

        try:
            config_module.validate(self.settings)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        self._apply_start_with_windows(self.settings.app.start_with_windows)

        self.callbacks.save_settings()

        QMessageBox.information(self, "Saved", "General settings saved.")

    # --------------------------------------------------------

    @staticmethod
    def _apply_start_with_windows(enabled: bool) -> None:
        """
        Best-effort registration in the current user's Run key. Never
        raises -- a failure here shouldn't block saving the rest of
        the settings.
        """

        if sys.platform != "win32":
            return

        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "ScreenAssistant"

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:

                if enabled:
                    exe_path = sys.executable
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                    except FileNotFoundError:
                        pass

        except Exception:
            pass


# ============================================================
# Working History tab
# ============================================================

class WorkingHistoryTab(QWidget):

    def __init__(self, history_db, parent=None):
        super().__init__(parent)

        self.history_db = history_db
        self._entries_by_row: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Working History")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        hint = QLabel(
            "Every closed chat conversation is saved here, most recent "
            "first. You can rename or delete them, but not continue them."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search chats (title or message text)...")
        self.search_box.textChanged.connect(self._on_search_changed)
        root.addWidget(self.search_box)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Title", "Date", "Messages", "Actions"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        root.addWidget(self.table, 1)

        self.refresh()

    # --------------------------------------------------------

    def refresh(self, search: str = "") -> None:

        entries = self.history_db.list_conversations(search=search)
        self._entries_by_row = entries

        self.table.setRowCount(0)

        for entry in entries:

            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(entry["title"]))

            date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["started_at"]))
            self.table.setItem(row, 1, QTableWidgetItem(date_str))

            self.table.setItem(row, 2, QTableWidgetItem(str(entry["message_count"])))

            self.table.setCellWidget(row, 3, self._build_actions_widget(entry))

        if not entries:
            self.table.setRowCount(1)
            empty_item = QTableWidgetItem("No conversations yet." if not search else "No matches.")
            self.table.setItem(0, 0, empty_item)
            self.table.setSpan(0, 0, 1, 4)

    def _build_actions_widget(self, entry: dict) -> QWidget:

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        conv_id = entry["id"]

        view_btn = QPushButton("View")
        view_btn.clicked.connect(lambda _, i=conv_id: self._on_view(i))
        layout.addWidget(view_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(lambda _, i=conv_id, t=entry["title"]: self._on_rename(i, t))
        layout.addWidget(rename_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(lambda _, i=conv_id: self._on_delete(i))
        layout.addWidget(delete_btn)

        layout.addStretch(1)

        return container

    # --------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self.refresh(search=text)

    def _on_row_double_clicked(self, row: int, _col: int) -> None:

        if row >= len(self._entries_by_row):
            return

        self._on_view(self._entries_by_row[row]["id"])

    def _on_view(self, conversation_id: int) -> None:

        messages = self.history_db.get_messages(conversation_id)
        dialog = ConversationViewDialog(self, messages)
        dialog.exec()

    def _on_rename(self, conversation_id: int, current_title: str) -> None:

        new_title, ok = QInputDialog.getText(
            self, "Rename conversation", "Title:", text=current_title
        )

        if ok and new_title.strip():
            self.history_db.rename_conversation(conversation_id, new_title.strip())
            self.refresh(search=self.search_box.text())

    def _on_delete(self, conversation_id: int) -> None:

        confirm = QMessageBox.question(
            self, "Delete conversation", "Delete this conversation permanently?"
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.history_db.delete_conversation(conversation_id)
        self.refresh(search=self.search_box.text())


# ============================================================
# Small reusable color-picker button
# ============================================================

class ConversationViewDialog(QDialog):

    def __init__(self, parent, messages: list):
        super().__init__(parent)

        self.setWindowTitle("Conversation")
        self.setModal(True)
        self.setMinimumSize(720, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.text_editor = QTextEdit()
        self.text_editor.setReadOnly(True)
        self.text_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.text_editor.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.text_editor.setPlainText(self._render_messages(messages))
        root.addWidget(self.text_editor, 1)

        buttons_row = QHBoxLayout()
        self.download_btn = QPushButton("Download PDF")
        self.download_btn.clicked.connect(self._save_to_pdf)
        buttons_row.addWidget(self.download_btn)
        buttons_row.addStretch(1)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        buttons_row.addWidget(self.close_btn)
        root.addLayout(buttons_row)

    def _render_messages(self, messages: list) -> str:
        return "\n\n".join(
            f"[{m['role'].upper()}]\n{m['text']}" for m in messages
        )

    def _save_to_pdf(self) -> None:
        downloads = Path.home() / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)

        path = downloads / f"conversation-{int(time.time())}.pdf"

        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(path))
            self.text_editor.document().print_(printer)

            QMessageBox.information(
                self,
                "Saved",
                f"Conversation exported to:\n{path}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))


class ColorPickerButton(QPushButton):

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)

        self.color_hex = color_hex
        self.setFixedWidth(90)
        self._update_swatch()
        self.clicked.connect(self._pick_color)

    def _update_swatch(self) -> None:

        self.setText(self.color_hex)
        text_color = "#000000" if self._is_light(self.color_hex) else "#FFFFFF"
        self.setStyleSheet(
            f"background-color: {self.color_hex}; color: {text_color}; border: 1px solid #777;"
        )

    @staticmethod
    def _is_light(hex_color: str) -> bool:

        h = hex_color.lstrip("#")

        if len(h) != 6:
            return False

        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

        return (0.299 * r + 0.587 * g + 0.114 * b) > 150

    def _pick_color(self) -> None:

        color = QColorDialog.getColor(QColor(self.color_hex), self, "Choose color")

        if color.isValid():
            self.color_hex = color.name()
            self._update_swatch()


# ============================================================
# Appearance tab
# ============================================================

class AppearanceTab(QWidget):

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Appearance")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        theme_group = QGroupBox("Theme")
        theme_form = QFormLayout(theme_group)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(settings.app.theme)
        theme_form.addRow("Theme", self.theme_combo)

        self.accent_picker = ColorPickerButton(settings.app.accent_color)
        theme_form.addRow("Accent color", self.accent_picker)

        root.addWidget(theme_group)

        font_group = QGroupBox("Font (overlay + chat)")
        font_form = QFormLayout(font_group)

        self.font_family = QFontComboBox()
        self.font_family.setCurrentFont(QFont(settings.chat.font_family))
        font_form.addRow("Font family", self.font_family)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)
        self.font_size.setValue(settings.chat.font_size)
        font_form.addRow("Font size", self.font_size)

        self.font_preview = QLabel("The quick brown fox — Aa Bb Cc 123")
        self.font_preview.setStyleSheet("padding: 8px; border: 1px dashed gray;")
        font_form.addRow("Preview", self.font_preview)

        self.font_family.currentFontChanged.connect(self._update_font_preview)
        self.font_size.valueChanged.connect(self._update_font_preview)
        self._update_font_preview()

        root.addWidget(font_group)

        chat_group = QGroupBox("Chat overlay")
        chat_form = QFormLayout(chat_group)

        self.chat_text_color = ColorPickerButton(settings.chat.text_color)
        chat_form.addRow("Text color", self.chat_text_color)

        self.chat_bg_color = ColorPickerButton(settings.chat.background_color)
        chat_form.addRow("Background color", self.chat_bg_color)

        self.chat_bg_alpha = QDoubleSpinBox()
        self.chat_bg_alpha.setRange(0.05, 1.0)
        self.chat_bg_alpha.setSingleStep(0.05)
        self.chat_bg_alpha.setValue(settings.chat.background_alpha)
        chat_form.addRow("Background opacity", self.chat_bg_alpha)

        self.user_accent = ColorPickerButton(settings.chat.user_accent_color)
        chat_form.addRow("Your message accent", self.user_accent)

        self.assistant_accent = ColorPickerButton(settings.chat.assistant_accent_color)
        chat_form.addRow("Assistant message accent", self.assistant_accent)

        root.addWidget(chat_group)

        overlay_group = QGroupBox("Capture-answer overlay")
        overlay_form = QFormLayout(overlay_group)

        self.overlay_opacity = QDoubleSpinBox()
        self.overlay_opacity.setRange(0.1, 1.0)
        self.overlay_opacity.setSingleStep(0.05)
        self.overlay_opacity.setValue(settings.overlay.opacity)
        overlay_form.addRow("Window opacity", self.overlay_opacity)

        self.overlay_width = QDoubleSpinBox()
        self.overlay_width.setRange(0.1, 1.0)
        self.overlay_width.setSingleStep(0.05)
        self.overlay_width.setValue(settings.overlay.width_percent)
        overlay_form.addRow("Width (% of screen)", self.overlay_width)

        root.addWidget(overlay_group)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

    # --------------------------------------------------------

    def _update_font_preview(self, *_args) -> None:

        family = self.font_family.currentFont().family()
        size = self.font_size.value()

        self.font_preview.setFont(QFont(family, size))

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        s = self.settings

        s.app.theme = self.theme_combo.currentText()
        s.app.accent_color = self.accent_picker.color_hex

        s.chat.font_family = self.font_family.currentFont().family()
        s.chat.font_size = self.font_size.value()
        s.overlay.font_family = s.chat.font_family
        s.overlay.font_size = s.chat.font_size

        s.chat.text_color = self.chat_text_color.color_hex
        s.chat.background_color = self.chat_bg_color.color_hex
        s.chat.background_alpha = self.chat_bg_alpha.value()
        s.chat.user_accent_color = self.user_accent.color_hex
        s.chat.assistant_accent_color = self.assistant_accent.color_hex

        s.overlay.opacity = self.overlay_opacity.value()
        s.overlay.width_percent = self.overlay_width.value()

        try:
            config_module.validate(s)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        self.callbacks.save_settings()
        self.callbacks.apply_theme()

        QMessageBox.information(self, "Saved", "Appearance applied.")


# ============================================================
# Model & Backend tab
# ============================================================

class ModelBackendTab(QWidget):

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks
        self._gpu_available = gpu_offload_supported()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Model & Backend")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        backend_group = QGroupBox("Backend")
        backend_form = QFormLayout(backend_group)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["local", "api"])
        self.backend_combo.setCurrentText(settings.llm.backend)
        backend_form.addRow("Run model", self.backend_combo)

        root.addWidget(backend_group)

        local_group = QGroupBox("Local model")
        local_form = QFormLayout(local_group)

        self.model_key_combo = QComboBox()
        for key, info in MODEL_CATALOG.items():
            self.model_key_combo.addItem(f"{key} -- {info['description']}", userData=key)
        idx = self.model_key_combo.findData(settings.llm.local.model_key)
        if idx >= 0:
            self.model_key_combo.setCurrentIndex(idx)
        self.model_key_combo.currentIndexChanged.connect(self._refresh_gpu_layers_combo)
        local_form.addRow("Model", self.model_key_combo)

        self.n_ctx = QSpinBox()
        self.n_ctx.setRange(512, 131072)
        self.n_ctx.setSingleStep(512)
        self.n_ctx.setValue(settings.llm.local.n_ctx)
        local_form.addRow("Context length", self.n_ctx)

        self.n_gpu_layers = QComboBox()
        self.n_gpu_layers.setToolTip(
            "How many of the model's layers to offload to the GPU. "
            "More layers = faster, but uses more VRAM."
        )
        local_form.addRow("GPU layers", self.n_gpu_layers)
        self._refresh_gpu_layers_combo()

        self.n_threads = QSpinBox()
        self.n_threads.setRange(0, 128)
        self.n_threads.setValue(settings.llm.local.n_threads)
        self.n_threads.setToolTip("0 = auto-detect (uses all CPU cores)")
        local_form.addRow("CPU threads", self.n_threads)

        self.local_temperature = QDoubleSpinBox()
        self.local_temperature.setRange(0.0, 2.0)
        self.local_temperature.setSingleStep(0.1)
        self.local_temperature.setValue(settings.llm.local.temperature)
        local_form.addRow("Temperature", self.local_temperature)

        self.local_max_tokens = QSpinBox()
        self.local_max_tokens.setRange(1, 32768)
        self.local_max_tokens.setValue(settings.llm.local.max_tokens)
        local_form.addRow("Max tokens", self.local_max_tokens)

        not_downloaded_hint = QLabel(
            "Models are downloaded from the Local Models tab, not here."
        )
        not_downloaded_hint.setStyleSheet("color: gray; font-size: 11px;")
        local_form.addRow(not_downloaded_hint)

        root.addWidget(local_group)

        api_group = QGroupBox("API backend")
        api_form = QFormLayout(api_group)

        api_hint = QLabel(
            "Connection details are managed automatically by your account "
            "once billing is live -- see Account & Billing."
        )
        api_hint.setWordWrap(True)
        api_hint.setStyleSheet("color: gray; font-size: 11px;")
        api_form.addRow(api_hint)

        self.api_temperature = QDoubleSpinBox()
        self.api_temperature.setRange(0.0, 2.0)
        self.api_temperature.setSingleStep(0.1)
        self.api_temperature.setValue(settings.llm.api.temperature)
        api_form.addRow("Temperature", self.api_temperature)

        self.api_max_tokens = QSpinBox()
        self.api_max_tokens.setRange(1, 32768)
        self.api_max_tokens.setValue(settings.llm.api.max_tokens)
        api_form.addRow("Max tokens", self.api_max_tokens)

        root.addWidget(api_group)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

    # --------------------------------------------------------
    # GPU layers: "None" if no GPU offload is possible on this
    # machine/build; otherwise 1..N (N = this model's total layer
    # count) plus "All".
    # --------------------------------------------------------

    def _refresh_gpu_layers_combo(self) -> None:

        model_key = self.model_key_combo.currentData() or self.settings.llm.local.model_key
        max_layers = max_gpu_layers_for(model_key)

        current_value = self.settings.llm.local.n_gpu_layers

        combo = self.n_gpu_layers
        combo.blockSignals(True)
        combo.clear()

        if not self._gpu_available:
            combo.addItem("None (no GPU detected)", 0)
            combo.setCurrentIndex(0)
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        combo.setEnabled(True)
        combo.addItem("None", 0)

        for i in range(1, max_layers + 1):
            combo.addItem(str(i), i)

        combo.addItem("All", -1)

        if current_value == -1:
            select_index = combo.count() - 1
        elif current_value <= 0:
            select_index = 0
        else:
            select_index = min(current_value, max_layers)

        combo.setCurrentIndex(select_index)
        combo.blockSignals(False)

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        if self.callbacks.is_busy():
            QMessageBox.warning(
                self,
                "Busy",
                "A response is currently being generated. Wait for it to "
                "finish before switching the model or backend.",
            )
            return

        s = self.settings

        new_backend = self.backend_combo.currentText()
        new_model_key = self.model_key_combo.currentData()

        s.llm.backend = new_backend
        s.llm.local.model_key = new_model_key
        s.llm.local.n_ctx = self.n_ctx.value()
        s.llm.local.n_gpu_layers = self.n_gpu_layers.currentData()
        s.llm.local.n_threads = self.n_threads.value()
        s.llm.local.temperature = self.local_temperature.value()
        s.llm.local.max_tokens = self.local_max_tokens.value()

        s.llm.api.temperature = self.api_temperature.value()
        s.llm.api.max_tokens = self.api_max_tokens.value()

        try:
            config_module.validate(s)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        if new_backend == "local" and not self.callbacks.model_manager.is_present_for(new_model_key):
            QMessageBox.warning(
                self,
                "Model not downloaded",
                f"'{new_model_key}' hasn't been downloaded yet. Download it "
                f"from the Local Models tab first, then apply here.",
            )
            return

        self.callbacks.save_settings()

        try:
            self.callbacks.reload_llm()
        except Exception as exc:
            QMessageBox.critical(self, "Failed to switch backend", str(exc))
            return

        QMessageBox.information(self, "Applied", "Model & backend settings applied.")


# ============================================================
# Local Models tab (download manager)
# ============================================================

class LocalModelsTab(QWidget):
    """
    Multiple models can download at the same time -- each in-flight
    download gets its own (QThread, ModelDownloadWorker) pair, so one
    slow/large download never blocks another from starting or
    finishing. Each row shows its own progress bar + Cancel button
    while downloading.
    """

    def __init__(self, settings, logger, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.logger = logger
        self.callbacks = callbacks
        self.model_manager = callbacks.model_manager

        # key -> {"thread": QThread, "worker": ModelDownloadWorker}
        self._active_downloads: dict = {}

        # key -> QProgressBar currently shown for that row (only while downloading)
        self._progress_bars: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Local Models")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        hint = QLabel(
            "Multiple models can download at once. All entries are "
            "Apache-2.0 licensed (safe for commercial use)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Model", "Size", "Status", "Progress", "Actions"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        root.addWidget(self.table, 1)

        self.refresh()

    # --------------------------------------------------------

    def refresh(self) -> None:

        entries = self.model_manager.list_catalog()

        self.table.setRowCount(0)
        self._progress_bars.clear()

        for entry in entries:

            row = self.table.rowCount()
            self.table.insertRow(row)

            key = entry["key"]
            downloading = key in self._active_downloads

            name = key + (" (active)" if entry["active"] else "")
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(f"{entry['approx_size_gb']:.1f} GB"))

            if downloading:
                status = "Downloading..."
            elif entry["downloaded"]:
                status = "Downloaded"
            else:
                status = "Not downloaded"
            self.table.setItem(row, 2, QTableWidgetItem(status))

            if downloading:
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(0)
                self._progress_bars[key] = bar
                self.table.setCellWidget(row, 3, bar)
            else:
                self.table.setCellWidget(row, 3, QWidget())

            self.table.setCellWidget(row, 4, self._build_actions_widget(entry, downloading))

    def _build_actions_widget(self, entry: dict, downloading: bool) -> QWidget:

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        key = entry["key"]

        if downloading:

            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(lambda _, k=key: self._on_cancel(k))
            layout.addWidget(cancel_btn)

        elif entry["downloaded"]:

            if not entry["active"]:
                use_btn = QPushButton("Set Active")
                use_btn.clicked.connect(lambda _, k=key: self._on_set_active(k))
                layout.addWidget(use_btn)

            # Deletion is always available, even for the active model --
            # if it's currently loaded/memory-mapped, the delete will
            # fail with a PermissionError, which we catch and explain.
            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(lambda _, k=key: self._on_delete(k))
            layout.addWidget(delete_btn)

        else:

            download_btn = QPushButton("Download")
            download_btn.clicked.connect(lambda _, k=key: self._on_download(k))
            layout.addWidget(download_btn)

        layout.addStretch(1)

        return container

    # --------------------------------------------------------
    # Download lifecycle -- one worker+thread per concurrent download
    # --------------------------------------------------------

    def _on_download(self, key: str) -> None:

        if key in self._active_downloads:
            return

        thread = QThread()
        worker = ModelDownloadWorker(self.logger, self.model_manager)
        worker.moveToThread(thread)

        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.cancelled.connect(self._on_cancelled)
        worker.error.connect(self._on_error)

        thread.start()

        self._active_downloads[key] = {"thread": thread, "worker": worker}

        QMetaObject.invokeMethod(
            worker, "download", Qt.ConnectionType.QueuedConnection, Q_ARG(str, key)
        )

        self.refresh()

    def _on_cancel(self, key: str) -> None:

        entry = self._active_downloads.get(key)

        if entry is None:
            return

        QMetaObject.invokeMethod(entry["worker"], "cancel", Qt.ConnectionType.QueuedConnection)

    def _on_progress(self, key: str, stage: str, fraction: float) -> None:

        bar = self._progress_bars.get(key)

        if bar is None:
            return

        if stage == "downloading":
            if fraction >= 0:
                bar.setRange(0, 100)
                bar.setValue(int(fraction * 100))
            else:
                bar.setRange(0, 0)  # indeterminate

    def _cleanup_download(self, key: str) -> None:

        entry = self._active_downloads.pop(key, None)

        if entry is None:
            return

        entry["thread"].quit()
        entry["thread"].wait()

    def _on_finished(self, key: str, path: str) -> None:

        self._cleanup_download(key)
        self.refresh()

    def _on_cancelled(self, key: str) -> None:

        self._cleanup_download(key)
        self.refresh()

    def _on_error(self, key: str, message: str) -> None:

        self._cleanup_download(key)

        QMessageBox.critical(self, "Download failed", f"Failed to download '{key}':\n{message}")

        self.refresh()

    # --------------------------------------------------------

    def _on_set_active(self, key: str) -> None:

        self.model_manager.set_active_key(key)
        self.settings.llm.local.model_key = key

        try:
            config_module.validate(self.settings)
            self.callbacks.save_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        if self.settings.llm.backend == "local":

            if self.callbacks.is_busy():
                QMessageBox.information(
                    self,
                    "Set as active",
                    f"'{key}' is now the active model, but a generation is in "
                    f"progress -- it will load next time the app starts or "
                    f"you switch backends from the Model & Backend tab.",
                )
            else:
                try:
                    self.callbacks.reload_llm()
                except Exception as exc:
                    QMessageBox.critical(self, "Failed to switch model", str(exc))

        self.refresh()

    def _on_delete(self, key: str) -> None:

        confirm = QMessageBox.question(
            self,
            "Delete model",
            f"Delete the downloaded '{key}' model file? You can re-download "
            f"it later.",
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self.model_manager.delete_model(key)
        except PermissionError:
            QMessageBox.critical(
                self,
                "Delete failed",
                "The file is currently in use (it's the active, loaded "
                "model). Switch the backend to API or set a different "
                "model active first, then delete this one.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Delete failed", str(exc))

        self.refresh()

    # --------------------------------------------------------

    def shutdown(self) -> None:
        """
        Cancels and cleans up every still-running download on app exit
        (not just the one the user happened to be watching).
        """

        for key in list(self._active_downloads.keys()):

            entry = self._active_downloads[key]

            QMetaObject.invokeMethod(entry["worker"], "cancel", Qt.ConnectionType.QueuedConnection)

            entry["thread"].quit()
            entry["thread"].wait()

        self._active_downloads.clear()


# ============================================================
# Hotkeys tab
# ============================================================

_HOTKEY_LABELS = [
    ("capture", "Capture full screen"),
    ("capture_area", "Capture selected area"),
    ("quit", "Quit app"),
    ("chat_focus", "Show + focus chat"),
    ("chat_input", "Focus chat input"),
    ("chat_close", "Close chat"),
    ("chat_new", "New chat session"),
]

_CONFLICT_STYLE = "border: 1px solid #E5484D;"


class HotkeysTab(QWidget):

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks
        self._editors: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Hotkeys")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        hint = QLabel("Click a field and press the new key combination.")
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        self.conflict_hint = QLabel("")
        self.conflict_hint.setStyleSheet("color: #E5484D; font-size: 11px;")
        self.conflict_hint.setWordWrap(True)
        root.addWidget(self.conflict_hint)

        form_group = QGroupBox("Shortcuts")
        form = QFormLayout(form_group)

        for field, label in _HOTKEY_LABELS:

            current = getattr(settings.hotkeys, field)

            editor = QKeySequenceEdit(hotkey_to_qkeysequence(current))
            editor.setMaximumSequenceLength(1)
            editor.keySequenceChanged.connect(self._check_conflicts)

            self._editors[field] = editor
            form.addRow(label, editor)

        root.addWidget(form_group)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

        self._check_conflicts()

    # --------------------------------------------------------
    # Live conflict detection -- highlights every field involved in a
    # duplicate as the user records new shortcuts, in addition to the
    # hard block on Apply below.
    # --------------------------------------------------------

    def _find_conflicts(self) -> dict:
        """
        Returns {hotkey_str: [field, field, ...]} for every hotkey
        string currently assigned to more than one action.
        """

        by_hotkey: dict = {}

        for field, editor in self._editors.items():

            hotkey_str = qkeysequence_to_hotkey(editor.keySequence())

            if not hotkey_str:
                continue

            by_hotkey.setdefault(hotkey_str, []).append(field)

        return {k: v for k, v in by_hotkey.items() if len(v) > 1}

    def _check_conflicts(self) -> None:

        conflicts = self._find_conflicts()
        conflicting_fields = {field for fields in conflicts.values() for field in fields}

        for field, editor in self._editors.items():
            editor.setStyleSheet(_CONFLICT_STYLE if field in conflicting_fields else "")

        if conflicts:
            parts = [f"'{hk}' is used by {len(fields)} shortcuts" for hk, fields in conflicts.items()]
            self.conflict_hint.setText("Conflicting shortcuts: " + "; ".join(parts))
        else:
            self.conflict_hint.setText("")

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        new_values = {}

        for field, editor in self._editors.items():

            hotkey_str = qkeysequence_to_hotkey(editor.keySequence())

            if not hotkey_str:
                QMessageBox.warning(self, "Missing shortcut", f"'{field}' has no key combination set.")
                return

            new_values[field] = hotkey_str

        conflicts = self._find_conflicts()

        if conflicts:
            details = "\n".join(f"'{hk}' -> {', '.join(fields)}" for hk, fields in conflicts.items())
            QMessageBox.warning(
                self,
                "Conflicting shortcuts",
                f"Each shortcut must be unique. Fix these before applying:\n\n{details}",
            )
            return

        for field, hotkey_str in new_values.items():
            setattr(self.settings.hotkeys, field, hotkey_str)

        self.callbacks.save_settings()

        try:
            self.callbacks.reload_hotkeys()
        except Exception as exc:
            QMessageBox.critical(self, "Failed to apply hotkeys", str(exc))
            return

        QMessageBox.information(self, "Applied", "Hotkeys updated.")


# ============================================================
# Personalization tab
# ============================================================

class PersonalizationTab(QWidget):

    username_changed = Signal(str)

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Personalization")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        profile_group = QGroupBox("Profile")
        profile_form = QFormLayout(profile_group)

        self.username = QLineEdit(settings.personalization.username)
        self.username.setPlaceholderText("How should the app greet you?")
        profile_form.addRow("Username", self.username)

        self.about_you = QTextEdit(settings.personalization.about_you)
        self.about_you.setFixedHeight(80)
        self.about_you.setPlaceholderText("e.g. Senior backend developer, prefers Python, works mostly in VS Code.")
        profile_form.addRow("About you", self.about_you)

        root.addWidget(profile_group)

        behavior_group = QGroupBox("Model behavior")
        behavior_form = QFormLayout(behavior_group)

        self.system_prompt = QTextEdit(settings.personalization.system_prompt)
        self.system_prompt.setFixedHeight(80)
        self.system_prompt.setPlaceholderText("e.g. Be concise. Prefer code over explanations.")
        behavior_form.addRow("System prompt", self.system_prompt)

        self.things_to_avoid = QTextEdit(settings.personalization.things_to_avoid)
        self.things_to_avoid.setFixedHeight(80)
        self.things_to_avoid.setPlaceholderText("e.g. Don't suggest paid tools. Don't use emojis.")
        behavior_form.addRow("Things to avoid", self.things_to_avoid)

        hint = QLabel(
            "These are sent to the model with every message, alongside "
            "the rest of the conversation."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        behavior_form.addRow(hint)

        root.addWidget(behavior_group)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        s = self.settings

        s.personalization.username = self.username.text().strip()
        s.personalization.about_you = self.about_you.toPlainText().strip()
        s.personalization.system_prompt = self.system_prompt.toPlainText().strip()
        s.personalization.things_to_avoid = self.things_to_avoid.toPlainText().strip()

        try:
            config_module.validate(s)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        self.callbacks.save_settings()

        self.username_changed.emit(s.personalization.username)

        QMessageBox.information(self, "Saved", "Personalization saved.")


# ============================================================
# Account & Billing tab
# ============================================================

class AccountBillingTab(QWidget):

    history_changed = Signal()

    def __init__(self, settings, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.callbacks = callbacks

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Account & Billing")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        message = QLabel(
            "Accounts and billing require a backend service that hasn't "
            "been built yet (Phase 2). The fields below are wired up and "
            "ready to show live data as soon as that's connected."
        )
        message.setWordWrap(True)
        message.setStyleSheet("color: gray;")
        root.addWidget(message)

        usage_group = QGroupBox("Credit usage")
        usage_form = QFormLayout(usage_group)

        self.credits_used_label = QLabel("—")
        usage_form.addRow("Credits used", self.credits_used_label)

        self.credits_remaining_label = QLabel("—")
        usage_form.addRow("Credits remaining", self.credits_remaining_label)

        root.addWidget(usage_group)

        limit_group = QGroupBox("Spend limit")
        limit_form = QFormLayout(limit_group)

        self.credit_limit = QDoubleSpinBox()
        self.credit_limit.setRange(0, 1_000_000)
        self.credit_limit.setDecimals(0)
        self.credit_limit.setSpecialValueText("No limit")
        self.credit_limit.setValue(settings.billing.credit_limit_per_prompt)
        limit_form.addRow("Max credits per prompt", self.credit_limit)

        limit_hint = QLabel(
            "Applied locally as a soft warning today; once billing is "
            "live this is also enforced server-side."
        )
        limit_hint.setWordWrap(True)
        limit_hint.setStyleSheet("color: gray; font-size: 11px;")
        limit_form.addRow(limit_hint)

        root.addWidget(limit_group)

        delete_row = QHBoxLayout()
        self.delete_all_btn = QPushButton("Delete all Working history")
        self.delete_all_btn.setStyleSheet(
            "color: #B00020; border: 1px solid #B00020;"
        )
        self.delete_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_all_btn.clicked.connect(self._on_delete_all_history)
        delete_row.addWidget(self.delete_all_btn)
        delete_row.addStretch(1)
        root.addLayout(delete_row)

        root.addStretch(1)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self.apply_btn)
        root.addLayout(apply_row)

    # --------------------------------------------------------

    def _on_apply(self) -> None:

        self.settings.billing.credit_limit_per_prompt = self.credit_limit.value()

        try:
            config_module.validate(self.settings)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        self.callbacks.save_settings()

        QMessageBox.information(self, "Saved", "Billing preferences saved.")

    def _on_delete_all_history(self) -> None:

        confirm = QMessageBox.question(
            self,
            "Delete all working history",
            "Delete every saved conversation permanently? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.callbacks.history_db.delete_all_conversations()
        self.history_changed.emit()
        QMessageBox.information(
            self,
            "Deleted",
            "All saved working history has been removed permanently.",
        )


# ============================================================
# About tab
# ============================================================

APP_VERSION = "0.1.0"


class AboutTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("Screen Assistant")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}")
        version.setStyleSheet("color: gray;")
        root.addWidget(version)

        license_label = QLabel(
            "Local model: Qwen2.5-Coder-Instruct (Apache-2.0)\n"
            "Built with PySide6, llama.cpp, RapidOCR."
        )
        license_label.setWordWrap(True)
        root.addWidget(license_label)

        root.addStretch(1)


# ============================================================
# Main window: nav rail + stacked tabs
# ============================================================

class SettingsWindow(QMainWindow):

    def __init__(self, settings, logger, callbacks: SettingsCallbacks, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.logger = logger
        self.callbacks = callbacks

        self.setWindowTitle("Screen Assistant -- Settings")
        self.resize(920, 640)

        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        root.addWidget(self.nav)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.dashboard_tab = DashboardTab(settings, callbacks)
        self.general_tab = GeneralTab(settings, callbacks)
        self.appearance_tab = AppearanceTab(settings, callbacks)
        self.working_history_tab = WorkingHistoryTab(callbacks.history_db)
        self.model_backend_tab = ModelBackendTab(settings, callbacks)
        self.local_models_tab = LocalModelsTab(settings, logger, callbacks)
        self.personalization_tab = PersonalizationTab(settings, callbacks)
        self.account_tab = AccountBillingTab(settings, callbacks)
        self.hotkeys_tab = HotkeysTab(settings, callbacks)
        self.about_tab = AboutTab()

        self.account_tab.history_changed.connect(self.working_history_tab.refresh)

        # Order per product spec. "General" and "Appearance" weren't
        # named in that ordering explicitly -- placed right after
        # Dashboard as the natural "quick settings" spot; flag if you
        # want them elsewhere.
        sections = [
            ("Dashboard", self.dashboard_tab),
            ("General", self.general_tab),
            ("Appearance", self.appearance_tab),
            ("Working History", self.working_history_tab),
            ("Model & Backend", self.model_backend_tab),
            ("Local Models", self.local_models_tab),
            ("Personalization", self.personalization_tab),
            ("Account & Billing", self.account_tab),
            ("Hotkeys", self.hotkeys_tab),
            ("About", self.about_tab),
        ]

        for label, widget in sections:
            QListWidgetItem(label, self.nav)
            self.stack.addWidget(widget)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        # Changing the username in Personalization reflects on Dashboard.
        self.personalization_tab.username_changed.connect(
            self.dashboard_tab.on_username_changed
        )

        self.apply_theme()

    # --------------------------------------------------------

    def _on_nav_changed(self, index: int) -> None:
        """
        Keep Dashboard's overview numbers (active backend/model, saved
        conversation count) fresh whenever the user comes back to it,
        without needing a manual refresh button.
        """

        if self.stack.widget(index) is self.dashboard_tab:
            self.dashboard_tab.refresh_overview()

    # --------------------------------------------------------

    def apply_theme(self) -> None:

        self.setStyleSheet(build_stylesheet(self.settings.app.theme, self.settings.app.accent_color))

    # --------------------------------------------------------

    def open_to(self, section_index: int = 0) -> None:

        self.nav.setCurrentRow(section_index)
        self.show()
        self.raise_()
        self.activateWindow()

    # --------------------------------------------------------

    def closeEvent(self, event) -> None:
        """
        Closing the window just hides it -- the app keeps running in
        the tray (background downloads, hotkeys, chat, etc. all keep
        working), and reopening via the tray shows this same instance.
        """

        event.ignore()
        self.hide()

    # --------------------------------------------------------

    def shutdown(self) -> None:
        """
        Real cleanup, called once on full app exit (not on window
        close -- see closeEvent above).
        """

        self.local_models_tab.shutdown()