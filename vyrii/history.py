"""SQLite-backed chat history with auto-export to markdown files."""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

_DB_PATH  = Path.home() / ".vyrii" / "history.db"
_CTX_DIR  = Path.home() / ".vyrii" / "ctx"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    _CTX_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT    NOT NULL,
            created_at REAL    NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            created_at REAL    NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        )
    """)
    c.commit()
    return c


def _safe_title(title: str) -> str:
    s = title.replace("\n", " ").replace("\r", " ")
    s = re.sub(r'[\x00-\x1f\\/:*?"<>|]', "_", s)
    return s[:60].strip() or "chat"


def _md_path(chat_id: int, title: str) -> Path:
    return _CTX_DIR / f"{chat_id}_{_safe_title(title)}.md"


def _export_chat_md(chat_id: int) -> None:
    with _conn() as c:
        row = c.execute(
            "SELECT title, created_at FROM chats WHERE id=?", (chat_id,)
        ).fetchone()
        if not row:
            return
        title, created_at = row
        msgs = c.execute(
            "SELECT role, content, created_at FROM messages WHERE chat_id=? ORDER BY created_at",
            (chat_id,),
        ).fetchall()

    lines = [f"# {title}\n"]
    for role, content, ts in msgs:
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        lines.append(f"**{dt}**\n\n**{role}:** {content}\n")

    _md_path(chat_id, title).write_text("\n---\n\n".join(lines), encoding="utf-8")


def list_chats() -> list[tuple[int, str, float]]:
    with _conn() as c:
        return c.execute(
            "SELECT id, title, created_at FROM chats ORDER BY created_at DESC"
        ).fetchall()


def create_chat(title: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO chats (title, created_at) VALUES (?, ?)",
            (title, time.time()),
        )
        return cur.lastrowid


def add_message(chat_id: int, role: str, content: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, time.time()),
        )
    _export_chat_md(chat_id)


def get_messages(chat_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE chat_id=? ORDER BY created_at",
            (chat_id,),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


def delete_chat(chat_id: int) -> None:
    with _conn() as c:
        row = c.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
        title = row[0] if row else ""
        c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        c.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    md = _md_path(chat_id, title)
    if md.exists():
        md.unlink()


def search_chats(query: str) -> list[tuple[int, str, float]]:
    """Return chats that contain `query` in any message (case-insensitive)."""
    if not query.strip():
        return list_chats()
    with _conn() as c:
        return c.execute(
            """SELECT DISTINCT ch.id, ch.title, ch.created_at
               FROM chats ch
               JOIN messages m ON m.chat_id = ch.id
               WHERE m.content LIKE ?
               ORDER BY ch.created_at DESC""",
            (f"%{query.strip()}%",),
        ).fetchall()


def auto_title(content: str) -> str:
    return content[:50].strip().replace("\n", " ") or "New chat"
