"""
tray_icon.py

System tray icon -- today this is the ONLY visible, discoverable entry
point into the app; everything else is invisible overlays triggered by
global hotkeys a first-time user has no way to know about. Left-click
opens Settings; right-click menu has Settings / New Chat / Quit.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayIcon(QObject):

    open_settings_requested = Signal()
    new_chat_requested = Signal()
    quit_requested = Signal()

    def __init__(self, accent_color: str = "#4A9EFF", parent=None):
        super().__init__(parent)

        self.tray = QSystemTrayIcon(self._build_icon(accent_color))
        self.tray.setToolTip("Screen Assistant")

        menu = QMenu()

        open_action = menu.addAction("Open Settings")
        open_action.triggered.connect(self.open_settings_requested.emit)

        new_chat_action = menu.addAction("New Chat")
        new_chat_action.triggered.connect(self.new_chat_requested.emit)

        menu.addSeparator()

        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_requested.emit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

        self.tray.show()

    # --------------------------------------------------------

    def _on_activated(self, reason) -> None:

        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings_requested.emit()

    # --------------------------------------------------------

    @staticmethod
    def _build_icon(accent_color: str) -> QIcon:
        """
        Generated flat square icon -- no external asset file needed
        for V1, and it's consistent with the rest of the app's
        no-rounded-corners visual language.
        """

        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(accent_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(8, 8, 48, 48)
        painter.end()

        return QIcon(pixmap)

    # --------------------------------------------------------

    def set_accent_color(self, accent_color: str) -> None:
        self.tray.setIcon(self._build_icon(accent_color))

    def hide(self) -> None:
        self.tray.hide()