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

# PRD ch.10 (内置第二本词书): the second built-in book. The id is a stable
# contract shared with the import pipeline, the builtin packaging checks and
# the frontend cover palette (红色系程序化封面).
RED_BOOK_ID = "kaoyan-hongbaoshu-2027"
RED_BOOK_TITLE = "考研英语红宝书"

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


def upsert_book(
    connection: sqlite3.Connection,
    book_id: str,
    *,
    title: str,
    description: str | None = None,
    source: str | None = None,
    now: str | None = None,
) -> sqlite3.Row:
    """Insert or refresh a vocabulary_books row (idempotent).

    Used by the word-list import pipeline for built-in books beyond the
    default one (PRD ch.10: the 考研英语红宝书 row + its import stay one
    transaction). created_at is kept stable across re-runs; title /
    description / source are refreshed so re-imports can update the word
    counts embedded in the description.
    """
    timestamp = now or utc_now()
    existing = connection.execute(
        "select * from vocabulary_books where id = ?",
        (book_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            insert into vocabulary_books (
                id, title, description, source, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (book_id, title, description, source, timestamp, timestamp),
        )
    else:
        connection.execute(
            """
            update vocabulary_books
            set title = ?, description = ?, source = ?, updated_at = ?
            where id = ?
            """,
            (title, description, source, timestamp, book_id),
        )
    return connection.execute(
        "select * from vocabulary_books where id = ?",
        (book_id,),
    ).fetchone()


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
