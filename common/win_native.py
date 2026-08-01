"""
win_native.py

Small, shared ctypes helpers for the native window behaviour every
overlay window in this app needs:

- WS_EX_NOACTIVATE: the window can receive mouse clicks (scrolling,
  buttons, text selection) without ever becoming the OS "foreground"
  window, so it never steals focus from whatever the user was
  working in.
- WDA_EXCLUDEFROMCAPTURE: the window is invisible to screenshots and
  screen shares.
- save/restore foreground window: the ONE case where a window must
  become genuinely focused is direct keyboard text entry (the chat
  input bar) -- Windows routes real keystrokes to the foreground
  window and there's no supported way around that. We minimize the
  disruption by remembering what was focused before we activate the
  input bar, and restoring it the moment the user is done.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


IS_WINDOWS = sys.platform == "win32"


if IS_WINDOWS:

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    GWL_EXSTYLE = -20

    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000

    WDA_NONE = 0x0
    WDA_EXCLUDEFROMCAPTURE = 0x11

    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong

    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_longlong)
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong

    user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    user32.GetForegroundWindow.restype = wintypes.HWND

    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL

    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL


# ============================================================
# Style helpers
# ============================================================

def set_noactivate(hwnd: int, click_through: bool = False) -> None:
    """
    Marks a window as non-activating: it can be shown, clicked, and
    scrolled without ever becoming the foreground window.

    click_through additionally makes the window ignore mouse events
    entirely (used for pure-display panels, not interactive ones).
    """

    if not IS_WINDOWS:
        return

    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)

    exstyle |= WS_EX_LAYERED | WS_EX_NOACTIVATE

    if click_through:
        exstyle |= WS_EX_TRANSPARENT
    else:
        exstyle &= ~WS_EX_TRANSPARENT

    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle)


def clear_noactivate(hwnd: int) -> None:
    """
    Temporarily allows a window to become the foreground window again
    (needed right before it should accept real keyboard input).
    """

    if not IS_WINDOWS:
        return

    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    exstyle &= ~WS_EX_NOACTIVATE
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle)


def exclude_from_capture(hwnd: int) -> bool:
    """
    Hides the window from screenshots / screen-share capture.
    """

    if not IS_WINDOWS:
        return False

    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


# ============================================================
# Focus save / restore
# ============================================================

def get_foreground_window() -> int:

    if not IS_WINDOWS:
        return 0

    return user32.GetForegroundWindow() or 0


def set_foreground_window(hwnd: int) -> bool:

    if not IS_WINDOWS or not hwnd:
        return False

    if not user32.IsWindow(hwnd):
        return False

    return bool(user32.SetForegroundWindow(hwnd))