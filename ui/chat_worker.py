"""
chat_worker.py

Runs LLM generation for the chat overlay on a background QThread, so
streaming a response never blocks the UI. Mirrors worker.py's
pattern, but takes a pre-built context string (assistant history +
the new user turn, flattened) instead of doing screen-capture/OCR.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class ChatWorker(QObject):
    """
    Signals
    -------
    chunk(str, str)
        (message_id, accumulated_text_so_far)

    finished(str, str)
        (message_id, final_text)

    error(str, str)
        (message_id, error_text)
    """

    chunk = Signal(str, str)
    finished = Signal(str, str)
    error = Signal(str, str)

    def __init__(self, logger, llm_client):
        super().__init__()

        self.logger = logger
        self.llm = llm_client

        # Cooperative cancellation: set() lets an in-flight generation
        # stop yielding into a conversation that's already been closed
        # or edited-past, without needing to kill the thread.
        self._cancel_ids = set()

    # -----------------------------------------------------

    @Slot(str, str)
    def generate(self, message_id: str, context: str):

        try:

            self.logger.info("Chat: generating response %s...", message_id)

            accumulated = ""

            for piece in self.llm.generate_stream(context):

                if message_id in self._cancel_ids:
                    self.logger.info("Chat: generation %s cancelled.", message_id)
                    return

                accumulated += piece
                self.chunk.emit(message_id, accumulated)

            accumulated = accumulated.strip()

            if not accumulated:
                raise RuntimeError("Model returned an empty response.")

            self.finished.emit(message_id, accumulated)

        except Exception as exc:

            self.logger.exception(exc)

            short_message = f"{type(exc).__name__}: {exc}"

            self.error.emit(message_id, short_message)

        finally:

            self._cancel_ids.discard(message_id)

    # -----------------------------------------------------

    @Slot(str)
    def cancel(self, message_id: str):
        """
        Best-effort: the generator is checked between chunks, so this
        stops UI updates promptly even though the underlying network/
        model call may finish its current token first.
        """

        self._cancel_ids.add(message_id)