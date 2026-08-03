from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt, QRect, QPoint, Signal, QEvent
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from common import win_native



class SelectionOverlay(QWidget):

    selection_made = Signal(int, int, int, int)
    selection_canceled = Signal()

    def __init__(self, settings: dict):
        super().__init__()

        self.settings = settings
        self._start = QPoint(0, 0)
        self._end = QPoint(0, 0)
        self._selecting = False

        self.setCursor(Qt.CursorShape.CrossCursor)
        self._configure_window()

    # --------------------------------------------------

    def _desktop_geometry(self) -> QRect:
        rect = QRect()
        app = QApplication.instance()

        if app is None:
            return QRect(0, 0, 1920, 1080)

        for screen in app.screens():
            rect = rect.united(screen.geometry())

        return rect

    # --------------------------------------------------

    def _configure_native_window(self):
        hwnd = int(self.winId())
        win_native.set_noactivate(hwnd, click_through=False)
        win_native.exclude_from_capture(hwnd)

    # --------------------------------------------------

    def _configure_window(self):
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowOpacity(1.0)

        self.setGeometry(self._desktop_geometry())
        self.show()
        self.winId()
        self._configure_native_window()
        self.hide()

    # --------------------------------------------------

    def start_selection(self):
        self._start = QPoint(0, 0)
        self._end = QPoint(0, 0)
        self._selecting = False

        self.show()
        self.raise_()
        self.grabKeyboard()
        self.update()

    # --------------------------------------------------

    def _cancel_selection(self):
        self._selecting = False
        self.releaseKeyboard()
        self.hide()
        self.selection_canceled.emit()

    # --------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.globalPosition().toPoint()
            self._end = self._start
            self._selecting = True
            self.grabMouse()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel_selection()

        super().mousePressEvent(event)

    # --------------------------------------------------

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._end = event.globalPosition().toPoint()
            self.update()

        super().mouseMoveEvent(event)

    # --------------------------------------------------

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._end = event.globalPosition().toPoint()
            self._selecting = False
            self.releaseMouse()
            self.releaseKeyboard()
            self.hide()

            rect = QRect(self._start, self._end).normalized()

            if rect.width() > 0 and rect.height() > 0:
                self.selection_made.emit(
                    rect.left(),
                    rect.top(),
                    rect.width(),
                    rect.height(),
                )
            else:
                self.selection_canceled.emit()

        super().mouseReleaseEvent(event)

    # --------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_selection()
            return

        super().keyPressEvent(event)

    # --------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        background_color = QColor(
            self.background_color.red(),
            self.background_color.green(),
            self.background_color.blue(),
            int(self.background_alpha * 255),
        )
        painter.fillRect(self.rect(), background_color)

        rect = QRect(self._start, self._end).normalized()

        if rect.isValid():
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            outline = QPen(QColor(0, 200, 255, 220), 3)
            painter.setPen(outline)
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

            fill_color = QColor(0, 200, 255, 40)
            painter.fillRect(rect, fill_color)

    # --------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.winId()
        self._configure_native_window()

    # --------------------------------------------------

    def hideEvent(self, event):
        super().hideEvent(event)
        self.releaseKeyboard()
        if self.hasMouseTracking():
            self.releaseMouse()

    # --------------------------------------------------

    def event(self, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._cancel_selection()
            return True
        return super().event(event)

    # --------------------------------------------------

    def focusOutEvent(self, event):
        if self._selecting:
            self.grabKeyboard()
        super().focusOutEvent(event)

    # --------------------------------------------------

    def __repr__(self):
        return f"<SelectionOverlay selecting={self._selecting}>"

    # --------------------------------------------------

    def __bool__(self):
        return self.isVisible()

    # --------------------------------------------------

    def cancel(self):
        self._cancel_selection()

    # --------------------------------------------------

    def resizeEvent(self, event):

        super().resizeEvent(event)

        self.update()