from pathlib import Path
import os
import sqlite3

from app.auth import ensure_super_account
from app.books import DEFAULT_BOOK_ID, ensure_default_book
from app.scheduling_migration import migrate_cards_sm2
from app.user_isolation_migration import migrate_user_isolation


def db_path() -> Path:
    configured_path = os.environ.get("VOCAB_DB_PATH")
    if configured_path:
        return Path(configured_path)
    return Path("./data/vocabulary.sqlite")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))

    # Vocabulary books migration (P1). Legacy databases already contain
    # book_words without the book_id column and "CREATE TABLE IF NOT EXISTS"
    # is a no-op for them, so add the column explicitly. This must happen
    # before idx_book_words_book_sequence is created below (and that index
    # therefore lives here instead of schema.sql).
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(book_words)")
    }
    if "book_id" not in columns:
        connection.execute(
            "ALTER TABLE book_words "
            "ADD COLUMN book_id text null references vocabulary_books(id)"
        )

    # Word-list layer annotation (PRD ch.10 考研英语红宝书 import). Same
    # legacy-DB pattern as book_id above: "CREATE TABLE IF NOT EXISTS" is
    # a no-op for databases that already have book_words without the column.
    if "layer" not in columns:
        connection.execute("ALTER TABLE book_words ADD COLUMN layer text null")

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_book_words_book_sequence
        ON book_words (book_id, sequence_index)
        """
    )

    # Verify/reset token table comment (C-05): 1h expiry, single use
    # (used_at), stored hashed like sessions.
    # C-01a (2026-09-05): the table now carries 6-digit email codes —
    # token_hash holds a salted scrypt hash of the code and `attempts`
    # counts wrong submissions. Legacy databases created before this
    # change lack the column (CREATE TABLE IF NOT EXISTS is a no-op for
    # them), so add it explicitly here.
    token_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(email_tokens)")
    }
    if "attempts" not in token_columns:
        connection.execute(
            "ALTER TABLE email_tokens ADD COLUMN attempts integer not null default 0"
        )

    # Default book (雅思词汇真经) + back-fill: idempotent on every connect.
    # INSERT OR IGNORE keeps the row stable; the UPDATE only touches rows
    # that were never assigned to a book.
    ensure_default_book(connection)
    connection.execute(
        "UPDATE book_words SET book_id = ? WHERE book_id IS NULL",
        (DEFAULT_BOOK_ID,),
    )

    # v2 cloud (C-04): idempotently provision the super account (email +
    # password from VOCAB_SUPER_EMAIL / VOCAB_SUPER_PASSWORD). INSERT OR
    # IGNORE keeps an already-rotated password untouched.
    ensure_super_account(connection)

    # v2 cloud batch 2 (C-05): per-user data isolation. Legacy databases
    # gain user_id columns / rebuilt queue tables with rows attributed to
    # the super account; fresh databases are already in the new shape via
    # schema.sql, so this is a no-op for them. Must run after
    # ensure_super_account (legacy rows are attributed to super).
    migrate_user_isolation(connection)

    # SM-2 scheduling migration (P0-4): adds ef / interval_days to cards
    # and back-fills each legacy card's interval from its stage. No-op
    # after the first successful run (settings flag), idempotent and
    # chunked so an interrupted run resumes from its cursor.
    migrate_cards_sm2(connection)
    connection.commit()
