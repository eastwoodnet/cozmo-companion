"""Persistent conversation memory backed by SQLite.

Stores every interaction (user messages, Cozmo replies, voice commands and
events) so Cozmo remembers past conversations across sessions. The most
recent exchanges are injected into the LLM system prompt as context.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional, Union

log = logging.getLogger("companion.memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    kind    TEXT    NOT NULL,
    text    TEXT    NOT NULL,
    lang    TEXT,
    emotion TEXT
);
CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions(ts);
"""

# Kinds stored in the table.
KIND_USER = "user"
KIND_COZMO = "cozmo"
KIND_COMMAND = "command"
KIND_EVENT = "event"

MAX_TEXT_LEN = 500


class Memory:
    """Thread-safe SQLite store for interactions."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
        log.info("Memory database at %s", self._path)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        kind: str,
        text: str,
        lang: Optional[str] = None,
        emotion: Optional[str] = None,
    ) -> None:
        """Store an interaction. Silently ignores empty text."""
        text = (text or "").strip()[:MAX_TEXT_LEN]
        if not text:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO interactions (ts, kind, text, lang, emotion) VALUES (?, ?, ?, ?, ?)",
                    (time.time(), kind, text, lang, emotion),
                )
                self._conn.commit()
        except sqlite3.Error as e:
            log.warning("Could not store interaction: %s", e)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def recent(self, limit: int = 20, kinds: Optional[tuple] = None) -> List[dict]:
        """Return the newest interactions in chronological order."""
        with self._lock:
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                rows = self._conn.execute(
                    f"SELECT ts, kind, text, lang, emotion FROM interactions "
                    f"WHERE kind IN ({placeholders}) ORDER BY ts DESC LIMIT ?",
                    (*kinds, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ts, kind, text, lang, emotion FROM interactions "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {"ts": r[0], "kind": r[1], "text": r[2], "lang": r[3], "emotion": r[4]}
            for r in reversed(rows)
        ]

    def conversation_context(self, turns: int = 6) -> str:
        """Format the last user/cozmo exchanges for the LLM system prompt."""
        rows = self.recent(limit=turns * 2, kinds=(KIND_USER, KIND_COZMO))
        lines = []
        for row in rows:
            speaker = "Human" if row["kind"] == KIND_USER else "Cozmo"
            lines.append(f"{speaker}: {row['text']}")
        return "\n".join(lines)

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0])

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM interactions")
            self._conn.commit()

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except sqlite3.Error:
            pass
