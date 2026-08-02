"""
Transparent desktop overlay.

Displays text above the desktop without accepting mouse input.

Responsibilities
----------------
- Show text
- Hide text
- Paint overlay
- Update content

The overlay contains NO OCR, LLM, or hotkey logic.
"""


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


# ==========================================================
# Windows Screen Capture Protection
# ==========================================================

if sys.platform == "win32":

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    WDA_NONE = 0x0
    WDA_MONITOR = 0x1
    WDA_EXCLUDEFROMCAPTURE = 0x11
    GWL_EXSTYLE = -20

    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000

    user32.SetWindowDisplayAffinity.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
    )

    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    
    user32.GetWindowLongPtrW.argtypes = (
        wintypes.HWND,
        ctypes.c_int,
    )
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong

    user32.SetWindowLongPtrW.argtypes = (
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_longlong,
    )
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong


class Overlay(QWidget):

    def __init__(self, settings: dict):
        super().__init__()

        self.settings = settings

        self.text = ""

        self._capture_protection_enabled = False

        overlay_cfg = settings.overlay

        self.font_family = overlay_cfg.font_family
        self.font_size = overlay_cfg.font_size

        self.padding = overlay_cfg.padding

        self.opacity = overlay_cfg.opacity

        self.background_alpha = overlay_cfg.background_alpha

        self.text_color = QColor(
            overlay_cfg.text_color
        )

        self.background_color = QColor(
            overlay_cfg.background_color
        )

        self.width_percent = overlay_cfg.width_percent

        self._configure_window()

    # --------------------------------------------------

    def apply_settings(self, settings) -> None:
        """
        Re-reads appearance config live -- called by the settings UI
        after Appearance changes are saved, so this overlay picks up
        the new colors/font/opacity without an app restart.
        """

        self.settings = settings

        overlay_cfg = settings.overlay

        self.font_family = overlay_cfg.font_family
        self.font_size = overlay_cfg.font_size
        self.padding = overlay_cfg.padding
        self.opacity = overlay_cfg.opacity
        self.background_alpha = overlay_cfg.background_alpha
        self.text_color = QColor(overlay_cfg.text_color)
        self.background_color = QColor(overlay_cfg.background_color)
        self.width_percent = overlay_cfg.width_percent

        self.setWindowOpacity(self.opacity)
        self._update_window_geometry()
        self.update()

    def _configure_native_window(self):

        if sys.platform != "win32":
            return

        hwnd = int(self.winId())

        #
        # Click-through
        #

        exstyle = user32.GetWindowLongPtrW(
            hwnd,
            GWL_EXSTYLE
        )

        exstyle |= (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_NOACTIVATE
        )

        user32.SetWindowLongPtrW(
            hwnd,
            GWL_EXSTYLE,
            exstyle
        )

        #
        # Screen capture protection
        #

        user32.SetWindowDisplayAffinity(
            hwnd,
            WDA_EXCLUDEFROMCAPTURE
        )

    # --------------------------------------------------

    def _configure_window(self):

        self.setWindowFlags(

            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            # | Qt.WindowType.BypassWindowManagerHint
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True
        )

        self.setWindowOpacity(self.opacity)
        self._update_window_geometry()

        self.show()

        self.winId()

        self._configure_native_window()

        self.hide()

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

    def _update_window_geometry(self) -> None:
        screen = self._desktop_geometry()

        width = int(screen.width() * self.width_percent)
        height = self.font_size * 12 + self.padding * 2

        x = screen.right() - width - 32
        y = screen.top() + 32

        self.setGeometry(x, y, width, height)

    # --------------------------------------------------

    def _enable_capture_protection(self):

        if sys.platform != "win32":
            return

        if self._capture_protection_enabled:
            return

        hwnd = int(self.winId())

        success = user32.SetWindowDisplayAffinity(
            hwnd,
            WDA_EXCLUDEFROMCAPTURE
        )

        if success:
            self._capture_protection_enabled = True
            print(
                "[Overlay] Screen capture protection enabled."
            )
        else:
            print(
                "[Overlay] Failed to enable screen capture protection:",
                ctypes.get_last_error()
            )

    # --------------------------------------------------

    def show_text(self, text: str):

        self.text = text

        self.update()

        self.show()

        # Force native HWND creation
        self.winId()

        # Enable only once
        self._enable_capture_protection()

    # --------------------------------------------------

    def hide_overlay(self):

        self.hide()

    # --------------------------------------------------


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