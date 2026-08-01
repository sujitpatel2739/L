"""
chat_overlay.py

ONE top-level window: a scrollable read-only conversation panel with
a single-line composer attached to its bottom edge -- a traditional
chat-window layout, not two separate floating panels.

Default state (however it was opened) is permanently non-activating
(WS_EX_NOACTIVATE): scrolling, clicking Edit, clicking Close all work
without ever taking OS keyboard focus away from whatever app the user
was in. The ONE exception is typing into the input line, which
genuinely needs real OS keyboard focus -- there's no way around that
on Windows. focus_input() temporarily clears NOACTIVATE for that;
ChatController is responsible for remembering what was focused before
that, and restoring it (via deactivate()) on Escape/close.

No rounded corners anywhere (flat rects only) and all backgrounds are
alpha-blended for the "glass" look, per the product spec.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QEvent, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from common import win_native


# ============================================================
# Shared flat/glass styling
# ============================================================

def _panel_qss(bg_hex: str, bg_alpha: float, text_hex: str) -> str:

    r, g, b = _hex_to_rgb(bg_hex)

    return f"""
        QWidget#panel {{
            background-color: rgba({r}, {g}, {b}, {int(bg_alpha * 255)});
            border: none;
        }}
        QLabel {{
            color: {text_hex};
            background: transparent;
            border: none;
        }}
        QPushButton {{
            background-color: rgba(255, 255, 255, 18);
            color: {text_hex};
            border: none;
            padding: 4px 10px;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 40);
        }}
        QPushButton:disabled {{
            color: rgba(255, 255, 255, 90);
            background-color: rgba(255, 255, 255, 8);
        }}
        QLineEdit {{
            background-color: rgba(255, 255, 255, 14);
            border: none;
            padding: 6px 8px;
            color: {text_hex};
        }}
        QLineEdit:disabled {{
            color: rgba(255, 255, 255, 90);
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(255, 255, 255, 60);
            min-height: 24px;
            border-radius: 0px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """


def _hex_to_rgb(hex_color: str):

    h = hex_color.lstrip("#")

    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ============================================================
# Message bubble
# ============================================================

class MessageBubble(QWidget):

    edit_clicked = Signal(str)  # message_id

    def __init__(self, message_id: str, role: str, text: str, cfg, parent=None):
        super().__init__(parent)

        self.message_id = message_id
        self.role = role
        self.cfg = cfg

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("bubble")

        accent = cfg.user_accent_color if role == "user" else cfg.assistant_accent_color

        self.setStyleSheet(f"""
            QWidget#bubble {{
                background-color: rgba(255, 255, 255, 12);
                border-left: 3px solid {accent};
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        role_label = QLabel("You" if role == "user" else "Agent")
        role_label.setStyleSheet(f"color: {accent}; font-weight: 600; font-size: 11px;")
        header.addWidget(role_label)
        header.addStretch(1)

        self.edit_button: Optional[QPushButton] = None

        if role == "user":
            self.edit_button = QPushButton("Edit")
            self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_button.setFixedHeight(20)
            self.edit_button.clicked.connect(
                lambda: self.edit_clicked.emit(self.message_id)
            )
            header.addWidget(self.edit_button)

        outer.addLayout(header)

        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.text_label.setStyleSheet(
            f"color: {cfg.text_color}; font-family: '{cfg.font_family}'; "
            f"font-size: {cfg.font_size}px;"
        )
        outer.addWidget(self.text_label)

    # --------------------------------------------------------

    def set_text(self, text: str) -> None:
        self.text_label.setText(text)
        # Ensure layout recomputes sizes so the bubble grows/shrinks
        self.text_label.adjustSize()
        self.adjustSize()
        self.updateGeometry()

    def apply_theme(self, cfg) -> None:
        """
        Re-applies colors/fonts from a (possibly just-edited) cfg --
        called on already-open bubbles when Appearance settings change,
        so an in-progress conversation restyles live too.
        """

        self.cfg = cfg

        accent = cfg.user_accent_color if self.role == "user" else cfg.assistant_accent_color

        self.setStyleSheet(f"""
            QWidget#bubble {{
                background-color: rgba(255, 255, 255, 12);
                border-left: 3px solid {accent};
            }}
        """)

        self.text_label.setStyleSheet(
            f"color: {cfg.text_color}; font-family: '{cfg.font_family}'; "
            f"font-size: {cfg.font_size}px;"
        )

    def set_edit_enabled(self, enabled: bool) -> None:
        if self.edit_button is not None:
            self.edit_button.setEnabled(enabled)

    def set_editing(self, editing: bool) -> None:
        """
        Visual marker while this message is the one loaded into the
        input bar for editing.
        """

        accent = self.cfg.user_accent_color if self.role == "user" else self.cfg.assistant_accent_color
        border = "2px dashed " + accent if editing else "3px solid " + accent

        self.setStyleSheet(f"""
            QWidget#bubble {{
                background-color: rgba(255, 255, 255, {18 if editing else 12});
                border-left: {border};
            }}
        """)


# ============================================================
# Chat overlay: conversation panel + attached input bar
# ============================================================

class ChatOverlayWindow(QWidget):

    close_requested = Signal()
    edit_requested = Signal(str)  # message_id
    send_requested = Signal(str)  # message text
    escape_pressed = Signal()
    input_focus_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)

        self.cfg = settings.chat
        self._bubbles: Dict[str, MessageBubble] = {}
        self._order: List[str] = []
        self._near_bottom = True

        # Whether the window is currently allowed to take real OS
        # keyboard focus. False = permanently non-activating (default,
        # and restored any time we're done with the input line).
        self._activatable = False

        self._build_ui()
        self._configure_window()

    # --------------------------------------------------------

    def _build_ui(self) -> None:

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("panel")
        self.setStyleSheet(_panel_qss(
            self.cfg.background_color,
            self.cfg.background_alpha,
            self.cfg.text_color,
        ))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header bar ----
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 10, 6)

        title = QLabel("Assistant")
        title.setStyleSheet(f"color: {self.cfg.text_color}; font-weight: 600;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close_requested.emit)
        header_layout.addWidget(close_btn)

        root.addWidget(header)

        # ---- scrollable message list (fills remaining space) ----
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")

        self.messages_container = QWidget()
        self.messages_container.setStyleSheet("background: transparent;")

        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(4, 4, 4, 4)
        self.messages_layout.setSpacing(6)
        self.messages_layout.addStretch(1)

        self.scroll_area.setWidget(self.messages_container)
        root.addWidget(self.scroll_area, 1)

        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed
        )

        # ---- input row, flat-attached to the bottom edge ----
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(255, 255, 255, 28); border: none;")
        root.addWidget(divider)

        input_row = QWidget()
        input_row.setFixedHeight(self.cfg.input_height_px)
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(6)

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("Message the assistant...")
        self.line_edit.setStyleSheet(
            f"font-family: '{self.cfg.font_family}'; font-size: {self.cfg.font_size}px;"
        )
        self.line_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.line_edit.returnPressed.connect(self._on_send_clicked)
        self.line_edit.installEventFilter(self)
        input_layout.addWidget(self.line_edit, 1)

        self.send_button = QPushButton("Send")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._on_send_clicked)
        input_layout.addWidget(self.send_button)

        root.addWidget(input_row)

    # --------------------------------------------------------

    def _configure_window(self) -> None:

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        screen = self.screen().availableGeometry()

        width = int(screen.width() * self.cfg.width_percent)
        height = int(screen.height() * self.cfg.height_percent)

        x = screen.right() - width - self.cfg.margin_px
        y = screen.top() + self.cfg.margin_px

        self.setGeometry(x, y, width, height)

        self.hide()
        self.winId()  # force native handle creation
        self._apply_native_flags(activatable=False)

        QApplication.instance().installEventFilter(self)

    def _apply_native_flags(self, activatable: bool) -> None:

        hwnd = int(self.winId())

        if activatable:
            win_native.clear_noactivate(hwnd)
        else:
            win_native.set_noactivate(hwnd, click_through=False)

        win_native.exclude_from_capture(hwnd)

        self._activatable = activatable

    # --------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.winId()
        # Re-apply whatever activation state we're currently meant to
        # be in -- NOT unconditionally non-activating, since that
        # would clobber an in-progress focus_input() call.
        self._apply_native_flags(self._activatable)

    # --------------------------------------------------------

    def eventFilter(self, obj, event):

        if obj is self.line_edit:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.input_focus_requested.emit()
                return False

            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self.escape_pressed.emit()
                    return True

        if event.type() == QEvent.Type.MouseButtonPress:
            # If the user clicks anywhere in our overlay window, request
            # input focus so the line editor can become active.
            if self.isVisible() and self.geometry().contains(event.globalPosition().toPoint()):
                self.input_focus_requested.emit()

        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        self.input_focus_requested.emit()
        super().mousePressEvent(event)

    def _on_send_clicked(self) -> None:

        text = self.line_edit.text().strip()

        if not text:
            return

        self.send_requested.emit(text)

    # --------------------------------------------------------

    def _on_scroll_changed(self, value: int) -> None:

        bar = self.scroll_area.verticalScrollBar()
        self._near_bottom = value >= bar.maximum() - 24

    def _scroll_to_bottom(self) -> None:

        def _do_scroll():
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, _do_scroll)

    # --------------------------------------------------------
    # Public API used by ChatController
    # --------------------------------------------------------

    def add_message(self, message_id: str, role: str, text: str) -> None:

        was_near_bottom = self._near_bottom

        bubble = MessageBubble(message_id, role, text, self.cfg)
        bubble.edit_clicked.connect(self.edit_requested.emit)

        # insert before the trailing stretch
        insert_index = self.messages_layout.count() - 1
        self.messages_layout.insertWidget(insert_index, bubble)

        self._bubbles[message_id] = bubble
        self._order.append(message_id)

        if was_near_bottom or role == "user":
            self._scroll_to_bottom()

    def update_message_text(self, message_id: str, text: str) -> None:

        bubble = self._bubbles.get(message_id)

        if bubble is None:
            return

        was_near_bottom = self._near_bottom

        bubble.set_text(text)

        if was_near_bottom:
            self._scroll_to_bottom()

    def remove_messages_after(self, message_id: str) -> None:
        """
        Removes every message strictly AFTER message_id (used when an
        edit invalidates everything that came after it). The message
        itself is left in place -- its text is updated separately via
        update_message_text().
        """

        if message_id not in self._order:
            return

        idx = self._order.index(message_id)
        to_remove = self._order[idx + 1:]

        for mid in to_remove:
            bubble = self._bubbles.pop(mid, None)
            if bubble is not None:
                self.messages_layout.removeWidget(bubble)
                bubble.deleteLater()

        self._order = self._order[: idx + 1]

    def clear_messages(self) -> None:

        for bubble in self._bubbles.values():
            self.messages_layout.removeWidget(bubble)
            bubble.deleteLater()

        self._bubbles.clear()
        self._order.clear()

    def set_editing(self, message_id: Optional[str]) -> None:
        """
        Highlights the bubble currently loaded into the input bar for
        editing (None clears the highlight on all bubbles).
        """

        for mid, bubble in self._bubbles.items():
            bubble.set_editing(mid == message_id)

    def set_busy(self, busy: bool) -> None:

        for bubble in self._bubbles.values():
            bubble.set_edit_enabled(not busy)

        self.line_edit.setEnabled(not busy)
        self.send_button.setEnabled(not busy)

    def is_open(self) -> bool:
        return self.isVisible()

    # --------------------------------------------------------
    # Input-line focus handling
    # --------------------------------------------------------

    def focus_input(self) -> None:
        """
        Makes this window the real OS foreground window so typed
        keystrokes actually reach the input line. Caller
        (ChatController) is responsible for remembering/restoring
        whatever had focus before this was called, and for calling
        deactivate() once the user is done typing.
        """

        hwnd = int(self.winId())

        self._apply_native_flags(activatable=True)

        self.show()
        self.raise_()
        self.activateWindow()
        self.line_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        win_native.set_foreground_window(hwnd)
        QTimer.singleShot(0, lambda: self.line_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason))

    def deactivate(self) -> None:
        """
        Reverts to permanently non-activating -- called once the user
        is done with the input line (Escape, or the conversation is
        closed), so a later stray click on the scrollbar/buttons can't
        re-steal OS focus.
        """

        if self.isVisible():
            self._apply_native_flags(activatable=False)
        else:
            self._activatable = False

    def set_input_text(self, text: str) -> None:
        self.line_edit.setText(text)
        self.line_edit.selectAll()

    def clear_input_text(self) -> None:
        self.line_edit.clear()

    def apply_theme(self) -> None:
        """
        Rebuilds and re-applies the stylesheet from the current
        self.cfg (the settings UI mutates it in place, then calls
        this) -- so appearance changes apply live, including to
        bubbles already on screen, without restarting the app or
        losing the in-progress conversation.
        """

        self.setStyleSheet(_panel_qss(
            self.cfg.background_color,
            self.cfg.background_alpha,
            self.cfg.text_color,
        ))

        self.line_edit.setStyleSheet(
            f"font-family: '{self.cfg.font_family}'; font-size: {self.cfg.font_size}px;"
        )

        for bubble in self._bubbles.values():
            bubble.apply_theme(self.cfg)