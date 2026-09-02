from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

# P1 vocabulary books: the project ships a single default book and every
# study flow (today session, prepare jobs, progress stats) runs
# against it until book switching lands in a later phase. The id is a stable
# constant so migrations can INSERT OR IGNORE and back-fill book_words
# idempotently on every connect().
DEFAULT_BOOK_ID = "default-book"
DEFAULT_BOOK_TITLE = "雅思词汇真经"


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
    return resolve_current_book(connection)


def resolve_current_book(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "select * from vocabulary_books where id = ?",
        (DEFAULT_BOOK_ID,),
    ).fetchone()
    if row is None:
        return ensure_default_book(connection)
    return row


def get_current_book_id(connection: sqlite3.Connection) -> str:
    return str(resolve_current_book(connection)["id"])
