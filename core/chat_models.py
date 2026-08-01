"""
chat_models.py

Plain data model for the chat overlay's conversation. No Qt, no I/O --
kept separate so it's trivially testable and reusable (e.g. by the
future history viewer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import time
from typing import List, Optional


_id_counter = itertools.count(1)


def next_message_id() -> str:
    return f"msg-{next(_id_counter)}-{int(time.time() * 1000)}"


def build_personalization_preamble(settings) -> str:
    """
    Folds the user's Personalization tab fields (About You, System
    Prompt, Things to Avoid) into a short preamble block that gets
    prepended to every request's context -- both typed chat messages
    (chat_controller.py) and OCR-triggered captures (worker.py) route
    through this, so personalization applies everywhere the user's
    words reach the model. Empty fields are omitted entirely rather
    than sent as blank lines.
    """

    p = settings.personalization

    lines = []

    about_you = (p.about_you or "").strip()
    system_prompt = (p.system_prompt or "").strip()
    things_to_avoid = (p.things_to_avoid or "").strip()

    if about_you:
        lines.append(f"About the user: {about_you}")

    if system_prompt:
        lines.append(f"Additional instructions: {system_prompt}")

    if things_to_avoid:
        lines.append(f"Things to avoid: {things_to_avoid}")

    if not lines:
        return ""

    return "PERSONALIZATION:\n" + "\n".join(lines) + "\n\n"


@dataclass(slots=True)
class ChatMessage:

    id: str
    role: str  # "user" | "assistant"
    text: str
    created_at: float = field(default_factory=time.time)


class Conversation:
    """
    In-memory ordered list of ChatMessage with the helpers the
    controller needs. No persistence here -- see chat_history_db.py.
    """

    def __init__(self) -> None:
        self.messages: List[ChatMessage] = []

    # --------------------------------------------------------
    
    def add_message(self, role: str, text: str) -> ChatMessage:

        msg = ChatMessage(id=next_message_id(), role=role, text=text)
        self.messages.append(msg)
        return msg

    # --------------------------------------------------------

    def find(self, message_id: str) -> Optional[ChatMessage]:

        for msg in self.messages:
            if msg.id == message_id:
                return msg

        return None

    def index_of(self, message_id: str) -> Optional[int]:

        for i, msg in enumerate(self.messages):
            if msg.id == message_id:
                return i

        return None

    # --------------------------------------------------------

    def truncate_from(self, message_id: str) -> None:
        """
        Deletes the message with this id AND everything after it.
        """

        idx = self.index_of(message_id)

        if idx is not None:
            del self.messages[idx:]

    # --------------------------------------------------------

    def assistant_history(self) -> List[dict]:
        """
        All assistant responses so far, oldest first, as chat-message
        dicts -- USER turns are deliberately excluded here per the
        product rule: only prior model responses are carried forward
        as context, not the user's own prior questions.
        """

        return [
            {"role": "assistant", "content": m.text}
            for m in self.messages
            if m.role == "assistant" and m.text
        ]

    # --------------------------------------------------------

    def clear(self) -> None:
        self.messages.clear()

    def is_empty(self) -> bool:
        return not self.messages