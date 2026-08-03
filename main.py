"""
main.py

Entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QThread, Slot
from PySide6.QtWidgets import QApplication

from ui.chat_controller import ChatController
from core.chat_history_db import ChatHistoryDB
from common.config import load_settings, save_settings, validate
from ui.hotkeys import HotkeyManager
from core.llm import create_llm_client
from core.model_manager import ModelManager
from ui.overlay import Overlay
from ui.selection_overlay import SelectionOverlay
from ui.settings_window import SettingsCallbacks, SettingsWindow
from ui.tray_icon import TrayIcon
from common.utils import setup_logger
from core.worker import Worker


def _default_app_data_dir() -> Path:
    """
    Same convention as ModelManager: a per-user, writable location
    that survives app updates/reinstalls (unlike the install dir,
    which is often read-only under Program Files).
    """

    base = os.getenv("LOCALAPPDATA")

    if base:
        return Path(base) / "ScreenAssistant" / "data"

    return Path.home() / ".screenassistant" / "data"


# ---------------------------------------------------------
class Dispatcher(QObject):

    capture_requested = Signal()
    capture_area_requested = Signal()
    quit_requested = Signal()

    chat_focus_requested = Signal()
    chat_input_requested = Signal()
    chat_close_requested = Signal()
    chat_new_requested = Signal()

    #
    # Sent to the worker thread.
    #
    process_capture = Signal(object)


# ---------------------------------------------------------

class ScreenAssistant(QObject):

    def __init__(self):

        super().__init__()

        #
        # Config
        #

        self.settings = load_settings()
        validate(self.settings)

        #
        # Logger
        #

        self.logger = setup_logger(self.settings)

        self.logger.info("Starting Screen Assistant...")

        #
        # Dispatcher
        #

        self.dispatcher = Dispatcher()

        #
        # Overlay
        #

        self.overlay = Overlay(self.settings)

        self.selection_overlay = SelectionOverlay(self.settings)
        self.selection_overlay.selection_made.connect(
            self._on_selection_made
        )
        self.selection_overlay.selection_canceled.connect(
            self._on_selection_canceled
        )

        self.overlay.hide_overlay()

        self.overlay_visible = False

        #
        # Local model manager -- constructed regardless of which
        # backend is active, since the Local Models settings tab lets
        # the user browse/download/delete models even while running
        # the API backend. Only the ACTIVE model is auto-downloaded on
        # startup, and only when llm.backend == "local".
        #

        self.model_manager = ModelManager(
            self.settings,
            self.logger,
        )

        model_path = None

        if self.settings.llm.backend == "local":

            model_path = self.model_manager.ensure_model(
                progress_callback=self._on_model_progress
            )

        else:

            self.logger.info(
                "Using API backend '%s' (%s).",
                self.settings.llm.api.model,
                self.settings.llm.api.base_url,
            )

        #
        # LLM client -- built ONCE and shared between the screen-
        # capture pipeline and the chat overlay, so a local model is
        # never loaded into memory twice.
        #

        self.llm_client = create_llm_client(
            self.settings,
            self.logger,
            model_path,
        )

        #
        # Worker
        #

        self.worker = Worker(
            self.settings,
            self.logger,
            self.llm_client,
        )

        self.worker_thread = QThread()

        self.worker.moveToThread(
            self.worker_thread
        )
        
        self.dispatcher.process_capture.connect(
            self.worker.process
        )

        self.worker.chunk.connect(
            self.on_chunk
        )

        self.worker.finished.connect(
            self.on_result
        )

        self.worker.error.connect(
            self.on_error
        )
        # OCR extracted text (before LLM generation)
        self.worker.extracted.connect(self._on_ocr_extracted)

        #
        # Chat overlay (minimal chat interface)
        #

        self.chat_history_db = ChatHistoryDB(
            self.logger,
            _default_app_data_dir() / "chat_history.db",
        )

        self.chat_controller = ChatController(
            self.settings,
            self.logger,
            self.llm_client,
            self.chat_history_db,
        )

        #
        # Dispatcher → GUI
        #

        self.dispatcher.capture_requested.connect(
            self.capture_pressed
        )

        self.dispatcher.capture_area_requested.connect(
            self.capture_area_pressed
        )

        self.dispatcher.quit_requested.connect(
            self.quit_pressed
        )

        # ctrl+alt+c: toggle chat overlay visibility (hide/unhide)
        self.dispatcher.chat_focus_requested.connect(
            self.toggle_chat_overlay
        )

        self.dispatcher.chat_input_requested.connect(
            self.chat_controller.focus_input
        )

        self.dispatcher.chat_close_requested.connect(
            self.chat_controller.close_conversation
        )

        self.dispatcher.chat_new_requested.connect(
            self.chat_controller.open_new_session
        )

        #
        # Hotkeys
        #

        self.hotkeys = HotkeyManager(
            self.settings,
            self.logger,
        )

        #
        # IMPORTANT
        #
        # Hotkeys emit signals only.
        #

        self.hotkeys.register(
            self.settings.hotkeys.capture,
            self.dispatcher.capture_requested.emit,
            action="capture",
        )

        self.hotkeys.register(
            self.settings.hotkeys.capture_area,
            self.dispatcher.capture_area_requested.emit,
            action="capture_area",
        )

        self.hotkeys.register(
            self.settings.hotkeys.quit,
            self.dispatcher.quit_requested.emit,
            action="quit",
        )

        self.hotkeys.register(
            self.settings.hotkeys.chat_focus,
            self.dispatcher.chat_focus_requested.emit,
            action="chat_focus",
        )

        self.hotkeys.register(
            self.settings.hotkeys.chat_input,
            self.dispatcher.chat_input_requested.emit,
            action="chat_input",
        )

        self.hotkeys.register(
            self.settings.hotkeys.chat_close,
            self.dispatcher.chat_close_requested.emit,
            action="chat_close",
        )

        self.hotkeys.register(
            self.settings.hotkeys.chat_new,
            self.dispatcher.chat_new_requested.emit,
            action="chat_new",
        )

        self.hotkeys.start()

        self.worker_thread.start()

        #
        # Settings window + tray icon -- the app's discoverable entry
        # point (everything else is invisible overlays + hotkeys).
        #

        self.settings_callbacks = SettingsCallbacks(
            save_settings=self._save_settings,
            reload_llm=self.reload_llm,
            reload_hotkeys=self.reload_hotkeys,
            apply_theme=self.apply_theme_everywhere,
            model_manager=self.model_manager,
            history_db=self.chat_history_db,
            is_busy=self.is_busy,
        )

        self.settings_window = SettingsWindow(
            self.settings,
            self.logger,
            self.settings_callbacks,
        )

        self.chat_controller.conversation_saved.connect(
            self.settings_window.working_history_tab.refresh
        )
        self.chat_controller.conversation_saved.connect(
            self.settings_window.dashboard_tab.refresh_overview
        )

        self.tray = TrayIcon(self.settings.app.accent_color)
        self.tray.open_settings_requested.connect(
            lambda: self.settings_window.open_to(0)
        )
        self.tray.new_chat_requested.connect(
            self.chat_controller.open_new_session
        )
        self.tray.quit_requested.connect(
            self.quit_pressed
        )

    # -------------------------------------------------
    # Callbacks used by settings_window.py -- these are the "apply
    # without restart" hooks each settings tab calls after saving.
    # -------------------------------------------------

    def _save_settings(self) -> None:

        validate(self.settings)
        save_settings(self.settings)
        self.logger.info("Settings saved.")

    def reload_llm(self) -> None:
        """
        Rebuilds the LLM client from the current settings and swaps it
        into both the OCR worker and the chat controller, so a backend
        or model change in the settings UI applies immediately.
        """

        model_path = None

        if self.settings.llm.backend == "local":
            model_path = self.model_manager.ensure_model(
                progress_callback=self._on_model_progress
            )

        self.llm_client = create_llm_client(
            self.settings,
            self.logger,
            model_path,
        )

        self.worker.reload_llm(self.settings, self.llm_client)
        self.chat_controller.reload_llm(self.llm_client)

        self.logger.info("LLM client reloaded (backend='%s').", self.settings.llm.backend)

    def reload_hotkeys(self) -> None:

        self.hotkeys.reload(self.settings)

    def apply_theme_everywhere(self) -> None:

        self.overlay.apply_settings(self.settings)
        self.chat_controller.overlay.apply_theme()
        self.settings_window.apply_theme()
        self.tray.set_accent_color(self.settings.app.accent_color)

    def is_busy(self) -> bool:

        return self.worker.busy or self.chat_controller.is_busy

    # -------------------------------------------------

    def _on_model_progress(self, stage: str, fraction: float):
        """
        Called by ModelManager during the (one-time) model download.

        Today this just prints to the console/log. When a GUI is
        added, swap this out for a signal emit -> a progress dialog,
        without touching model_manager.py at all.
        """

        if stage == "downloading":

            if fraction >= 0:
                percent = int(fraction * 100)
                print(f"\rDownloading model... {percent}%", end="", flush=True)
            else:
                print("\rDownloading model...", end="", flush=True)

        elif stage == "ready":

            print("\rModel ready.                      ")

    # -------------------------------------------------

    @Slot()
    def capture_pressed(self):

        self.logger.info("Capture requested.")

        if self.worker.busy:
            return

        # When capture is pressed, show the chat overlay (no focus)
        # and route the resulting OCR+LLM output into the chat panel
        # instead of the center overlay.

        if not self.chat_controller.overlay.is_open():
            self.chat_controller.overlay.show()

        # Prepare to route worker output into chat
        self._capture_into_chat = True
        self._capture_user_msg_id = None
        self._capture_assistant_msg_id = None

        # Start pipeline.
        self.dispatcher.process_capture.emit(None)

    def capture_area_pressed(self):

        self.logger.info("Area capture requested.")

        if self.worker.busy:
            return

        if not self.chat_controller.overlay.is_open():
            self.chat_controller.overlay.show()

        self._capture_into_chat = True
        self._capture_user_msg_id = None
        self._capture_assistant_msg_id = None

        self.selection_overlay.start_selection()

    def _on_selection_made(self, left: int, top: int, width: int, height: int):

        self.logger.info(
            "Selected region: left=%d top=%d width=%d height=%d",
            left,
            top,
            width,
            height,
        )

        self.dispatcher.process_capture.emit((left, top, width, height))

    def _on_selection_canceled(self):

        self.logger.info("Selection canceled.")

        self._capture_into_chat = False
        self._capture_user_msg_id = None
        self._capture_assistant_msg_id = None

    def toggle_chat_overlay(self) -> None:

        if self.chat_controller.overlay.is_open():
            self.chat_controller.release_focus_to_previous()
            self.chat_controller.overlay.hide()
        else:
            self.chat_controller.overlay.show()

    # -------------------------------------------------
    @Slot(str)
    def on_chunk(self, text):

        #
        # Live-update the overlay as tokens stream in. This runs for
        # both the local and API backends -- generate_stream() gives
        # both the same interface.
        #

        # If we're routing capture into the chat overlay, update the
        # assistant message bubble instead of the center overlay.
        if getattr(self, "_capture_into_chat", False):

            if self._capture_assistant_msg_id is not None:
                # update assistant bubble
                self.chat_controller.overlay.update_message_text(
                    self._capture_assistant_msg_id,
                    text,
                )

            return

        self.overlay.show_text(text)

        self.overlay_visible = True

    # -------------------------------------------------
    @Slot()
    def on_result(self, text):

        #
        # The last chunk already painted this exact text; this just
        # confirms the final state (kept in case a future GUI wants
        # to react to "stream complete" separately from "chunk").
        #

        if getattr(self, "_capture_into_chat", False):

            if self._capture_assistant_msg_id is not None:
                # finalize assistant bubble and conversation
                msg = self.chat_controller.conversation.find(self._capture_assistant_msg_id)
                if msg is not None:
                    msg.text = text

                self.chat_controller.overlay.update_message_text(
                    self._capture_assistant_msg_id,
                    text,
                )

            # reset capture routing state
            self._capture_into_chat = False
            self._capture_user_msg_id = None
            self._capture_assistant_msg_id = None

            # clear busy state on chat controller if set
            try:
                if self.chat_controller._current_generation_id is not None:
                    self.chat_controller._current_generation_id = None
                    self.chat_controller._set_busy(False)
            except Exception:
                pass

            return

        self.overlay.show_text(text)

        self.overlay_visible = True

    # -------------------------------------------------
    @Slot()
    def on_error(self, message):
        if getattr(self, "_capture_into_chat", False):

            if self._capture_assistant_msg_id is not None:
                display = f"[Error] {message}"

                msg = self.chat_controller.conversation.find(self._capture_assistant_msg_id)
                if msg is not None:
                    msg.text = display

                self.chat_controller.overlay.update_message_text(
                    self._capture_assistant_msg_id,
                    display,
                )

            self._capture_into_chat = False
            self._capture_user_msg_id = None
            self._capture_assistant_msg_id = None

            try:
                if self.chat_controller._current_generation_id is not None:
                    self.chat_controller._current_generation_id = None
                    self.chat_controller._set_busy(False)
            except Exception:
                pass

            return

        self.overlay.show_text(
            "ERROR\n\n" + message
        )

        self.overlay_visible = True

    # -------------------------------------------------
    def _on_ocr_extracted(self, extracted_text: str) -> None:

        # Called from worker thread via signal when OCR finishes and
        # before LLM generation starts. If we're routing into chat,
        # add the user message and create a placeholder assistant
        # message to receive streamed tokens.

        if not getattr(self, "_capture_into_chat", False):
            return

        # add user message
        user_msg = self.chat_controller.conversation.add_message("user", extracted_text)
        self.chat_controller.overlay.add_message(user_msg.id, "user", extracted_text)
        self._capture_user_msg_id = user_msg.id

        # add assistant placeholder
        assistant_msg = self.chat_controller.conversation.add_message("assistant", "")
        self.chat_controller.overlay.add_message(assistant_msg.id, "assistant", "")
        self._capture_assistant_msg_id = assistant_msg.id

        # mark generation in-progress on chat controller
        try:
            self.chat_controller._current_generation_id = assistant_msg.id
            self.chat_controller._set_busy(True)
        except Exception:
            pass

    # -------------------------------------------------
    # @Slot()
    def quit_pressed(self):

        self.shutdown()

        QApplication.quit()

    # -------------------------------------------------

    def shutdown(self):

        self.logger.info("Stopping...")

        self.hotkeys.stop()

        self.worker_thread.quit()

        self.worker_thread.wait()

        self.overlay.close()

        self.chat_controller.close_conversation()
        self.chat_controller.shutdown()

        self.chat_history_db.close()

        self.settings_window.shutdown()

        self.tray.hide()


# ---------------------------------------------------------

def main():

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    assistant = ScreenAssistant()

    exit_code = app.exec()

    assistant.shutdown()

    sys.exit(exit_code)


# ---------------------------------------------------------

if __name__ == "__main__":

    main()