"""
chat_controller.py

Orchestrates the (single-window) chat overlay + background generation
worker. Lives entirely on the main/UI thread; only LLM generation
runs on a separate QThread (via ChatWorker).

Implements the product rules:
1. Context sent to the model = all PRIOR assistant responses (never
   prior user messages) + the latest user message.
2. Streamed token by token (via ChatWorker -> chunk signal).
3. ctrl+alt+x (or the close button) ends the conversation (saved to
   history) and closes the whole panel, input row included.
4. Editing a user message mutates it in place and deletes every
   message after it, then regenerates a response for it.
5. Sending / editing is blocked while a generation is in progress.

Additionally:
- ctrl+alt+c focuses the input line without changing what's on screen.
- ctrl+alt+n opens the panel as a brand-new session, WITHOUT taking
  keyboard focus -- ending/saving whatever conversation was already
  open first, exactly like the close button would.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from common import win_native
from core.chat_history_db import ChatHistoryDB
from core.chat_models import Conversation, build_personalization_preamble
from ui.chat_overlay import ChatOverlayWindow
from ui.chat_worker import ChatWorker


class ChatController(QObject):

    # to ChatWorker (runs on chat_thread). context is a flattened
    # prompt string (see _send_new_message / _confirm_edit below).
    generate_requested = Signal(str, str)
    cancel_requested = Signal(str)

    def __init__(self, settings, logger, llm_client, history_db: ChatHistoryDB):
        super().__init__()

        self.settings = settings
        self.logger = logger
        self.history_db = history_db

        self.conversation = Conversation()

        self._busy = False
        self._editing_message_id: Optional[str] = None
        self._current_generation_id: Optional[str] = None

        self._focus_grabbed = False
        self._prev_foreground_hwnd: Optional[int] = None

        # ---- UI (single merged window: panel + attached input row) ----

        self.overlay = ChatOverlayWindow(settings)

        self.overlay.close_requested.connect(self.close_conversation)
        self.overlay.edit_requested.connect(self.start_edit)
        self.overlay.send_requested.connect(self.on_send)
        self.overlay.escape_pressed.connect(self.release_focus_to_previous)
        self.overlay.input_focus_requested.connect(self.focus_input)

        # ---- background worker ----

        self.chat_thread = QThread()
        self.chat_worker = ChatWorker(logger, llm_client)
        self.chat_worker.moveToThread(self.chat_thread)

        self.generate_requested.connect(self.chat_worker.generate)
        self.cancel_requested.connect(self.chat_worker.cancel)

        self.chat_worker.chunk.connect(self._on_chunk)
        self.chat_worker.finished.connect(self._on_finished)
        self.chat_worker.error.connect(self._on_error)

        self.chat_thread.start()

    @property
    def is_busy(self) -> bool:
        return self._busy

    # -----------------------------------------------------
    # Entry points wired to hotkeys in main.py
    # -----------------------------------------------------

    @Slot()
    def focus_input(self) -> None:
        """
        ctrl+alt+c / ctrl+alt+i: shows the panel if it isn't already visible,
        and gives the input line real keyboard focus so the user can type.
        Does NOT start a new session -- an existing conversation (if
        any) stays exactly as it was.
        """

        if not self.overlay.is_open():
            self.overlay.show()

        self._grab_focus()

        self.overlay.focus_input()

    @Slot()
    def open_new_session(self) -> None:
        """
        ctrl+alt+n: opens the panel as a brand-new conversation
        WITHOUT taking keyboard focus. Any existing conversation is
        ended/saved first, exactly as the close button would.
        """

        self.close_conversation()

        self.overlay.show()

    @Slot()
    def close_conversation(self) -> None:
        """
        Rule 3: ends the conversation entirely and closes the whole
        panel (message list + attached input row together).
        """

        if self._busy and self._current_generation_id:
            self.cancel_requested.emit(self._current_generation_id)

        if not self.conversation.is_empty():
            self.history_db.save_conversation(list(self.conversation.messages))

        self.conversation.clear()
        self.overlay.clear_messages()
        self.overlay.clear_input_text()
        self.overlay.hide()

        self._editing_message_id = None
        self._current_generation_id = None

        self.release_focus_to_previous()

        self._set_busy(False)

    # -----------------------------------------------------
    # Sending / editing
    # -----------------------------------------------------

    @Slot(str)
    def on_send(self, text: str) -> None:

        # Rule 5: no sending while a generation is already running.
        if self._busy:
            return

        text = text.strip()

        if not text:
            return

        if self._editing_message_id is not None:
            self._confirm_edit(self._editing_message_id, text)
        else:
            self._send_new_message(text)

        self.overlay.clear_input_text()

    def _send_new_message(self, text: str) -> None:

        user_msg = self.conversation.add_message("user", text)
        self.overlay.add_message(user_msg.id, "user", text)

        # Rule 1: context = all prior ASSISTANT messages + this user msg.
        context = self._build_context(text)

        self._start_generation(context)

    def _confirm_edit(self, message_id: str, new_text: str) -> None:

        idx = self.conversation.index_of(message_id)

        self.overlay.set_editing(None)
        self._editing_message_id = None

        if idx is None:
            # message no longer exists (e.g. conversation was closed
            # mid-edit) -- treat as a fresh message instead of failing.
            self._send_new_message(new_text)
            return

        # Rule 4: mutate the edited message in place...
        self.conversation.messages[idx].text = new_text

        # ...and delete everything below it.
        del self.conversation.messages[idx + 1:]

        self.overlay.update_message_text(message_id, new_text)
        self.overlay.remove_messages_after(message_id)

        # Context naturally excludes the edited message itself (it's
        # role "user") and only picks up assistant turns BEFORE it,
        # since everything after was just deleted.
        context = self._build_context(new_text)

        self._start_generation(context)

    def _build_context(self, user_text: str) -> str:

        personalization = build_personalization_preamble(self.settings)

        context = "".join(
            agent_msg["content"] + "\n"
            for agent_msg in self.conversation.assistant_history()
        ) + "\n"

        return (
            personalization
            + "AGENT_MSGS_CONTEXT:\n"
            + context
            + "\nUSER_MSG:\n"
            + user_text
        )

    @Slot(str)
    def start_edit(self, message_id: str) -> None:

        # Rule 5 extension: don't allow editing mid-generation either.
        if self._busy:
            return

        msg = self.conversation.find(message_id)

        if msg is None or msg.role != "user":
            return

        self._editing_message_id = message_id
        self.overlay.set_editing(message_id)

        self.overlay.set_input_text(msg.text)

        if not self.overlay.is_open():
            self.overlay.show()

        self._grab_focus()
        self.overlay.focus_input()

    # -----------------------------------------------------
    # Generation lifecycle
    # -----------------------------------------------------

    def _start_generation(self, context: str) -> None:

        assistant_msg = self.conversation.add_message("assistant", "")
        self.overlay.add_message(assistant_msg.id, "assistant", "")

        self._current_generation_id = assistant_msg.id
        self._set_busy(True)

        self.generate_requested.emit(assistant_msg.id, context)

    @Slot(str, str)
    def _on_chunk(self, message_id: str, text: str) -> None:

        self.overlay.update_message_text(message_id, text)

    @Slot(str, str)
    def _on_finished(self, message_id: str, text: str) -> None:

        msg = self.conversation.find(message_id)

        if msg is not None:
            msg.text = text

        self.overlay.update_message_text(message_id, text)

        if self._current_generation_id == message_id:
            self._current_generation_id = None
            self._set_busy(False)

    @Slot(str, str)
    def _on_error(self, message_id: str, error_text: str) -> None:

        display = f"[Error] {error_text}"

        msg = self.conversation.find(message_id)

        if msg is not None:
            msg.text = display

        self.overlay.update_message_text(message_id, display)

        if self._current_generation_id == message_id:
            self._current_generation_id = None
            self._set_busy(False)

    def _set_busy(self, value: bool) -> None:

        self._busy = value
        self.overlay.set_busy(value)

    # -----------------------------------------------------

    def reload_llm(self, llm_client) -> None:
        """
        Swaps in a new (already-built) LLM client for chat generation,
        e.g. after the user changes backend/model in the settings UI.
        Mirrors worker.py's reload_llm(). Must not be called while a
        generation is in progress (self._busy) -- the caller (settings
        UI) is responsible for checking that first.
        """

        self.chat_worker.llm = llm_client
        self.logger.info("Chat LLM backend reloaded.")

    # -----------------------------------------------------
    # Focus save / restore
    # -----------------------------------------------------

    def _grab_focus(self) -> None:

        if not self._focus_grabbed:
            self._prev_foreground_hwnd = win_native.get_foreground_window()
            self._focus_grabbed = True

    @Slot()
    def release_focus_to_previous(self) -> None:

        if self._focus_grabbed and self._prev_foreground_hwnd:
            win_native.set_foreground_window(self._prev_foreground_hwnd)

        self._focus_grabbed = False
        self._prev_foreground_hwnd = None

        # Revert to non-activating so a later stray click on the
        # scrollbar/buttons can't re-steal OS focus on its own.
        self.overlay.deactivate()

    # -----------------------------------------------------

    def shutdown(self) -> None:

        self.chat_thread.quit()
        self.chat_thread.wait()