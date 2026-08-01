"""
hotkeys.py

Global Hotkey Manager

Architecture
------------
Main Thread
    |
    | start()
    |
Hotkey Thread
    |
    | RegisterHotKey()
    | RegisterHotKey()
    |
    | GetMessage()
    |
    | WM_HOTKEY
    |
    | callback()
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes


# ============================================================
# Win32
# ============================================================

user32 = ctypes.windll.user32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008


MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


VK = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}


# ============================================================

class HotkeyManager:

    def __init__(self, settings, logger):

        self.settings = settings
        self.logger = logger

        self.callbacks = {}

        # action name (e.g. "capture", "chat_focus") -> callback, so
        # reload() can re-register the SAME callbacks against whatever
        # new key combo settings.hotkeys.<action> now holds, without
        # the caller having to re-supply them.
        self._actions = {}

        self.thread = None
        self.thread_id = None

        self.running = False

    # ========================================================

    def register(self, hotkey: str, callback, action: str = None):

        hotkey_id = len(self.callbacks) + 1

        modifiers, vk = self._parse_hotkey(hotkey)

        self.callbacks[hotkey_id] = (
            modifiers,
            vk,
            callback,
            hotkey,
        )

        if action is not None:
            self._actions[action] = callback

    # ========================================================

    def reload(self, settings) -> None:
        """
        Re-registers every hotkey that was originally registered WITH
        an action name, using the (possibly just-changed) key combo
        from settings.hotkeys.<action>. Used by the settings UI after
        the user remaps a shortcut -- no restart needed.
        """

        was_running = self.running

        if was_running:
            self.stop()

        self.settings = settings

        new_callbacks = {}

        for i, (action, callback) in enumerate(self._actions.items(), start=1):

            hotkey_str = getattr(settings.hotkeys, action)

            modifiers, vk = self._parse_hotkey(hotkey_str)

            new_callbacks[i] = (modifiers, vk, callback, hotkey_str)

        self.callbacks = new_callbacks

        if was_running:
            self.start()

    # ========================================================

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
        )

        self.thread.start()

    # ========================================================

    def stop(self):

        if not self.running:
            return

        self.running = False

        if self.thread_id is not None:

            user32.PostThreadMessageW(
                self.thread_id,
                WM_QUIT,
                0,
                0,
            )

        if self.thread:

            self.thread.join()

    # ========================================================

    def _thread_main(self):

        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        #
        # Register all hotkeys HERE
        #

        for hotkey_id, (
            modifiers,
            vk,
            callback,
            name,
        ) in self.callbacks.items():

            ok = user32.RegisterHotKey(
                None,
                hotkey_id,
                modifiers,
                vk,
            )

            if not ok:

                raise RuntimeError(
                    f"Failed to register {name}"
                )

            self.logger.info(
                "Registered hotkey: %s",
                name,
            )

        msg = wintypes.MSG()

        self.logger.info("Hotkey thread running.")

        #
        # Message loop
        #

        while self.running:

            result = user32.GetMessageW(
                ctypes.byref(msg),
                None,
                0,
                0,
            )

            if result == 0:
                break

            if result == -1:

                self.logger.error(
                    "GetMessage failed."
                )

                break

            if msg.message == WM_HOTKEY:

                hotkey_id = msg.wParam

                info = self.callbacks.get(
                    hotkey_id
                )

                if info is not None:

                    callback = info[2]

                    try:

                        callback()

                    except Exception:

                        self.logger.exception(
                            "Hotkey callback failed."
                        )

            user32.TranslateMessage(
                ctypes.byref(msg)
            )

            user32.DispatchMessageW(
                ctypes.byref(msg)
            )

        #
        # Cleanup
        #

        for hotkey_id in self.callbacks:

            user32.UnregisterHotKey(
                None,
                hotkey_id,
            )

        self.logger.info(
            "Hotkeys released."
        )

    # ========================================================

    @staticmethod
    def _parse_hotkey(hotkey: str):

        parts = hotkey.lower().split("+")

        modifiers = 0
        key = None

        for part in parts:

            part = part.strip()

            if part in MODIFIERS:

                modifiers |= MODIFIERS[part]

            else:

                key = part

        if key is None:

            raise ValueError(
                f"Invalid hotkey: {hotkey}"
            )

        vk = HotkeyManager._virtual_key(key)

        return modifiers, vk

    # ========================================================

    @staticmethod
    def _virtual_key(key):

        if len(key) == 1:

            return ord(key.upper())

        if key.startswith("f"):

            number = int(key[1:])

            if 1 <= number <= 24:

                return 0x70 + number - 1

        if key in VK:

            return VK[key]

        raise ValueError(
            f"Unsupported key: {key}"
        )