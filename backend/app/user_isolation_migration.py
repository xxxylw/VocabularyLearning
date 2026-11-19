"""v2 cloud batch 2 (C-05): per-user data isolation migration.

Fresh databases already get the isolated shape from schema.sql; this
module upgrades *legacy* databases in place, following the established
``app.db.migrate`` pattern (PRAGMA table_info probe + idempotent ALTER /
rebuild, safe to re-run on every connect):

- cards / reviews gain a ``user_id`` column (FK to users); existing rows
  are attributed to the super account (the only account that could have
  produced study data before batch 2).
- today_queue / today_queue_snapshots are *rebuilt* because their unique
  constraints live in the table DDL and must gain user_id — SQLite
  cannot ALTER a table-level UNIQUE constraint.
- The per-user unique indexes (idx_cards_entry, idx_today_queue_card)
  are (re)created here rather than in schema.sql because legacy tables
  only have user_id after the steps above.
- The global ``settings.current_book_id`` pointer moves into
  ``user_settings`` for the super account; system-level flags (SM-2
  backfill cursor/done) stay in settings.

Everything is guarded by column probes, so a second run is a no-op
(idempotent). A before/after row-count report is printed whenever a
transformation actually ran (C-05 acceptance: 迁移输出报告).
"""

from __future__ import annotations

import sqlite3

_CURRENT_BOOK_KEY = "current_book_id"

_TODAY_QUEUE_DDL = """
CREATE TABLE today_queue_migrated (
    id text primary key,
    user_id text not null references users(id),
    book_id text not null,
    study_date text not null,
    position integer not null,
    card_id text not null,
    queue_type text not null check (queue_type in ('new', 'review')),
    created_at text not null,
    unique (user_id, book_id, study_date, position)
)
"""

_TODAY_QUEUE_SNAPSHOTS_DDL = """
CREATE TABLE today_queue_snapshots_migrated (
    user_id text not null references users(id),
    book_id text not null,
    study_date text not null,
    created_at text not null,
    primary key (user_id, book_id, study_date)
)
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"select count(*) from {table}").fetchone()[0]


def _super_user_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "select id from users where is_super = 1 order by created_at limit 1"
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "user isolation migration requires the super account to exist "
            "(ensure_super_account must run first)"
        )
    return row[0]


def _index_sql(connection: sqlite3.Connection, index_name: str) -> str | None:
    row = connection.execute(
        "select sql from sqlite_master where type = 'index' and name = ?",
        (index_name,),
    ).fetchone()
    return row[0] if row is not None else None


def migrate_user_isolation(connection: sqlite3.Connection) -> None:
    """Idempotently upgrade a legacy database to per-user isolation."""

    report: list[str] = []

    cards_columns = _columns(connection, "cards")
    reviews_columns = _columns(connection, "reviews")
    queue_columns = _columns(connection, "today_queue")
    snapshot_columns = _columns(connection, "today_queue_snapshots")
    needs_transform = (
        "user_id" not in cards_columns
        or "user_id" not in reviews_columns
        or "user_id" not in queue_columns
        or "user_id" not in snapshot_columns
    )

    if needs_transform:
        super_id = _super_user_id(connection)

        if "user_id" not in cards_columns:
            before = _count(connection, "cards")
            connection.execute(
                "ALTER TABLE cards ADD COLUMN user_id text null references users(id)"
            )
            connection.execute(
                "UPDATE cards SET user_id = ? WHERE user_id IS NULL", (super_id,)
            )
            report.append(f"cards: +user_id column, {before} rows attributed to super")

        if "user_id" not in reviews_columns:
            before = _count(connection, "reviews")
            connection.execute(
                "ALTER TABLE reviews ADD COLUMN user_id text null references users(id)"
            )
            connection.execute(
                "UPDATE reviews SET user_id = ? WHERE user_id IS NULL", (super_id,)
            )
            report.append(
                f"reviews: +user_id column, {before} rows attributed to super"
            )

        if "user_id" not in queue_columns:
            before = _count(connection, "today_queue")
            connection.execute(_TODAY_QUEUE_DDL)
            connection.execute(
                """
                INSERT INTO today_queue_migrated (
                    id, user_id, book_id, study_date, position,
                    card_id, queue_type, created_at
                )
                SELECT id, ?, book_id, study_date, position,
                       card_id, queue_type, created_at
                FROM today_queue
                """,
                (super_id,),
            )
            connection.execute("DROP TABLE today_queue")
            connection.execute(
                "ALTER TABLE today_queue_migrated RENAME TO today_queue"
            )
            after = _count(connection, "today_queue")
            if after != before:
                raise RuntimeError(
                    "today_queue rebuild lost rows: "
                    f"before={before} after={after}"
                )
            report.append(
                f"today_queue: rebuilt with user_id, {before} rows preserved "
                "(attributed to super)"
            )

        if "user_id" not in snapshot_columns:
            before = _count(connection, "today_queue_snapshots")
            connection.execute(_TODAY_QUEUE_SNAPSHOTS_DDL)
            connection.execute(
                """
                INSERT INTO today_queue_snapshots_migrated (
                    user_id, book_id, study_date, created_at
                )
                SELECT ?, book_id, study_date, created_at
                FROM today_queue_snapshots
                """,
                (super_id,),
            )
            connection.execute("DROP TABLE today_queue_snapshots")
            connection.execute(
                "ALTER TABLE today_queue_snapshots_migrated "
                "RENAME TO today_queue_snapshots"
            )
            after = _count(connection, "today_queue_snapshots")
            if after != before:
                raise RuntimeError(
                    "today_queue_snapshots rebuild lost rows: "
                    f"before={before} after={after}"
                )
            report.append(
                f"today_queue_snapshots: rebuilt with user_id, {before} rows "
                "preserved (attributed to super)"
            )

    # The current-book pointer is per-user from batch 2 on. A legacy
    # global pointer is handed to the super account (the only account
    # that could have set it); fresh databases simply have nothing to
    # move. Runs on every connect so the move is exactly-once.
    pointer_row = connection.execute(
        "select value from settings where key = ?", (_CURRENT_BOOK_KEY,)
    ).fetchone()
    if pointer_row is not None:
        super_id = _super_user_id(connection)
        connection.execute(
            "insert or ignore into user_settings (user_id, key, value)"
            " values (?, ?, ?)",
            (super_id, _CURRENT_BOOK_KEY, pointer_row[0]),
        )
        connection.execute(
            "delete from settings where key = ?", (_CURRENT_BOOK_KEY,)
        )
        report.append(
            "settings: current_book_id moved to user_settings (super), "
            f"value={pointer_row[0]}"
        )

    # Per-user unique indexes. Fresh databases: plain create. Legacy
    # databases: the old single-column / user-less definition is dropped
    # first. Probing sqlite_master keeps every connect cheap (no
    # drop/recreate churn once the new definition is in place).
    cards_index_sql = _index_sql(connection, "idx_cards_entry") or ""
    if "user_id" not in cards_index_sql:
        connection.execute("DROP INDEX IF EXISTS idx_cards_entry")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_entry"
        " ON cards (user_id, entry_id)"
    )
    queue_index_sql = _index_sql(connection, "idx_today_queue_card") or ""
    if "user_id" not in queue_index_sql:
        connection.execute("DROP INDEX IF EXISTS idx_today_queue_card")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_today_queue_card"
        " ON today_queue (user_id, book_id, study_date, card_id)"
    )

    if report:
        summary = "; ".join(report)
        totals = (
            f"cards={_count(connection, 'cards')}, "
            f"reviews={_count(connection, 'reviews')}, "
            f"today_queue={_count(connection, 'today_queue')}, "
            f"today_queue_snapshots={_count(connection, 'today_queue_snapshots')}"
        )
        print(f"[user-isolation-migration] {summary}")
        print(f"[user-isolation-migration] table totals after migration: {totals}")
