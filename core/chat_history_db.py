"""
chat_history_db.py

Persists conversations when the chat overlay is closed, so the user
can review past conversations later in the Working History settings
tab. Conversations can be renamed, deleted, and searched from there;
there is still no "continue this conversation" flow (by design, per
the product spec) -- Working History is read-only + manage-only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from core.chat_models import ChatMessage


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT 'Untitled conversation',
    started_at  REAL NOT NULL,
    ended_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      REAL NOT NULL
);
"""

_TITLE_MAX_LEN = 60


def _auto_title(messages: List[ChatMessage]) -> str:
    """
    Default title for a newly-saved conversation: the first user
    message, truncated. Renamed later by the user if they want.
    """

    for m in messages:
        if m.role == "user" and m.text.strip():
            text = " ".join(m.text.strip().split())
            if len(text) > _TITLE_MAX_LEN:
                return text[:_TITLE_MAX_LEN].rstrip() + "..."
            return text

    return "Untitled conversation"


class ChatHistoryDB:

    def __init__(self, logger, db_path: Path):

        self.logger = logger
        self.db_path = db_path

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(SCHEMA)
        self._migrate_add_title_column()
        self._conn.commit()

    # --------------------------------------------------------

    def _migrate_add_title_column(self) -> None:
        """
        Older DB files (from before Working History existed) won't
        have the `title` column -- add it in place rather than
        forcing a fresh DB, so existing history isn't lost.
        """

        cols = [row[1] for row in self._conn.execute("PRAGMA table_info(conversations)")]

        if "title" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN title TEXT NOT NULL DEFAULT 'Untitled conversation'"
            )

    # --------------------------------------------------------

    def save_conversation(self, messages: List[ChatMessage]) -> None:
        """
        Writes one closed conversation as a single row + its messages.
        No-op if the conversation is empty (nothing was ever sent).
        """

        if not messages:
            return

        try:

            started_at = messages[0].created_at
            ended_at = time.time()
            title = _auto_title(messages)

            cursor = self._conn.execute(
                "INSERT INTO conversations (title, started_at, ended_at) VALUES (?, ?, ?)",
                (title, started_at, ended_at),
            )

            conversation_id = cursor.lastrowid

            self._conn.executemany(
                "INSERT INTO messages (conversation_id, role, text, created_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    (conversation_id, m.role, m.text, m.created_at)
                    for m in messages
                ],
            )

            self._conn.commit()

            self.logger.info(
                "Saved chat conversation #%s (%d messages).",
                conversation_id,
                len(messages),
            )

        except Exception:

            self.logger.exception("Failed to save chat conversation.")

    # --------------------------------------------------------
    # Working History tab support
    # --------------------------------------------------------

    def list_conversations(self, search: str = "") -> List[dict]:
        """
        Every saved conversation, most-recent-first. If `search` is
        given, matches against the title OR any message text in that
        conversation (case-insensitive substring match).
        """

        search = (search or "").strip()

        if search:

            like = f"%{search}%"

            rows = self._conn.execute(
                """
                SELECT DISTINCT c.id, c.title, c.started_at, c.ended_at
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.title LIKE ? COLLATE NOCASE
                   OR m.text LIKE ? COLLATE NOCASE
                ORDER BY c.started_at DESC
                """,
                (like, like),
            ).fetchall()

        else:

            rows = self._conn.execute(
                "SELECT id, title, started_at, ended_at FROM conversations "
                "ORDER BY started_at DESC"
            ).fetchall()

        results = []

        for conv_id, title, started_at, ended_at in rows:

            count_row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()

            preview_row = self._conn.execute(
                "SELECT text FROM messages WHERE conversation_id = ? AND role = 'user' "
                "ORDER BY created_at ASC LIMIT 1",
                (conv_id,),
            ).fetchone()

            results.append({
                "id": conv_id,
                "title": title,
                "started_at": started_at,
                "ended_at": ended_at,
                "message_count": count_row[0] if count_row else 0,
                "preview": (preview_row[0] if preview_row else "")[:120],
            })

        return results

    def get_messages(self, conversation_id: int) -> List[dict]:
        """
        Full transcript for one conversation, oldest first.
        """

        rows = self._conn.execute(
            "SELECT role, text, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()

        return [
            {"role": role, "text": text, "created_at": created_at}
            for role, text, created_at in rows
        ]

    def rename_conversation(self, conversation_id: int, new_title: str) -> None:

        new_title = new_title.strip() or "Untitled conversation"

        self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (new_title, conversation_id),
        )
        self._conn.commit()

    def delete_conversation(self, conversation_id: int) -> None:

        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        self._conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        self._conn.commit()

    # --------------------------------------------------------

    def close(self) -> None:

        try:
            self._conn.close()
        except Exception:
            pass