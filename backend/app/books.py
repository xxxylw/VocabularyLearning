from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

# P1 vocabulary books: every book_words row belongs to exactly one book and
# the study flows (today session, prepare jobs, progress stats) run against
# the *current* book (PRD ch.9). The current book is a pointer stored in the
# settings table — switching only rewrites this pointer and never touches
# reviews / cards / snapshots (切换零改写).
DEFAULT_BOOK_ID = "default-book"
DEFAULT_BOOK_TITLE = "雅思词汇真经"

CURRENT_BOOK_SETTING_KEY = "current_book_id"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_default_book(
    connection: sqlite3.Connection, now: str | None = None
) -> sqlite3.Row:
    timestamp = now or utc_now()
    connection.execute(
        """
        insert or ignore into vocabulary_books (
            id, title, description, source, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (DEFAULT_BOOK_ID, DEFAULT_BOOK_TITLE, None, None, timestamp, timestamp),
    )
    return connection.execute(
        "select * from vocabulary_books where id = ?",
        (DEFAULT_BOOK_ID,),
    ).fetchone()


def read_current_book_pointer(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "select value from settings where key = ?",
        (CURRENT_BOOK_SETTING_KEY,),
    ).fetchone()
    return row["value"] if row is not None else None


def set_current_book_pointer(connection: sqlite3.Connection, book_id: str) -> None:
    connection.execute(
        "insert or replace into settings (key, value) values (?, ?)",
        (CURRENT_BOOK_SETTING_KEY, book_id),
    )


def book_exists(connection: sqlite3.Connection, book_id: str) -> bool:
    row = connection.execute(
        "select 1 from vocabulary_books where id = ? limit 1",
        (book_id,),
    ).fetchone()
    return row is not None


def resolve_current_book(
    connection: sqlite3.Connection,
) -> tuple[sqlite3.Row, bool]:
    """Return (current book row, fallback_happened).

    The pointer lives in ``settings``; when unset the default book is used.
    When the pointer references a missing book (PRD ch.9 异常兜底) it is
    reset to the default book so the app never white-screens or loses data.
    """
    pointer = read_current_book_pointer(connection)
    if pointer is not None:
        row = connection.execute(
            "select * from vocabulary_books where id = ?",
            (pointer,),
        ).fetchone()
        if row is not None:
            return row, False
        set_current_book_pointer(connection, DEFAULT_BOOK_ID)
    return ensure_default_book(connection), pointer is not None


def get_current_book_id(connection: sqlite3.Connection) -> str:
    return str(resolve_current_book(connection)[0]["id"])
