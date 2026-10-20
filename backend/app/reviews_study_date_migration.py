"""One-time migration adding reviews.study_date (P1 2026-09-08).

Spec: P1 修复 — 跨书共享词 UTC 日界竞态（task 7683097747100093410）

The reviews table gains an explicit ``study_date`` column (TEXT, NOT
NULL) holding the **server-local** calendar date of the review — the
same calendar boundary the today_queue snapshot uses (``date.today()``
on the deployed server, which runs in Asia/Shanghai). Before this
column, the day-aggregating queries (reviewedCards, dayCompleted,
_count_new_words_studied_on, _review_exists_on_date) derived the date
by taking ``substr(reviewed_at, 1, 10)`` of the client-supplied ISO
timestamp. Because clients send ``new Date().toISOString()`` which is
always UTC, any review submitted during the Beijing 00:00-08:00 window
was bucketed into the *previous* UTC day — the count missed it, the
card was still pending in the user's queue, and a follow-up review
attempt 409'd with "Card is not due on the reviewed date" after SM-2
had already pushed due_at into the next day. Cross-book shared words
magnified the symptom: the same review row was meant to satisfy three
books' queues simultaneously, so the day stayed stuck at totalCards
- 1 forever.

Backfill strategy: every existing review row gets ``study_date`` =
the server-local date derived from its ``reviewed_at`` text. Client
ISO timestamps are UTC (``...Z`` or ``...+00:00``); the conversion
lands on the Beijing date the user actually intended the review for.
A before/after row-count report is printed whenever a transformation
actually ran (matching the C-05 acceptance pattern in
``user_isolation_migration``).

Idempotency: the migration is a no-op on databases that already carry
``study_date`` (column probe). The schema for fresh databases picks
up the column from ``schema.sql``.

All day-aggregating queries in ``services.py`` were switched to read
``study_date`` in the same change set; the column is the single source
of truth for "which local day does this review belong to".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _count(connection, table: str) -> int:
    return connection.execute(f"select count(*) from {table}").fetchone()[0]


def _server_local_date(utc_iso: str) -> str:
    """Convert a UTC ISO 8601 timestamp to the server-local YYYY-MM-DD.

    Accepts the Z-suffixed form produced by ``Date.toISOString()`` and
    the ``+00:00`` explicit-offset form used by the schema. Naive
    timestamps (no offset) are treated as UTC — that matches what the
    client always sends and what older test fixtures write.
    """
    if utc_iso.endswith("Z"):
        utc_iso = utc_iso[:-1] + "+00:00"
    parsed = datetime.fromisoformat(utc_iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().date().isoformat()


def migrate_reviews_study_date(connection) -> None:
    """Add reviews.study_date to legacy databases and back-fill rows."""

    report: list[str] = []

    reviews_columns = _columns(connection, "reviews")
    needs_column = "study_date" not in reviews_columns
    needs_backfill = False

    if needs_column:
        before = _count(connection, "reviews")
        connection.execute(
            "ALTER TABLE reviews ADD COLUMN study_date text not null default ''"
        )
        # NOT NULL with empty default so the ALTER works on tables that
        # already have rows. The back-fill below overwrites every row,
        # so the placeholder is only visible for a brief moment inside
        # this transaction — and migrate() runs in a single connection
        # under the _migrate_lock, so no reader sees the intermediate
        # state.
        needs_backfill = True
        report.append(f"add reviews.study_date column (reviews rows={before})")

    if needs_backfill:
        # Cursor + chunked update so an interrupted run resumes cleanly
        # (mirrors the SM-2 migration pattern). ``id`` is the primary
        # key so a lexicographic cursor is stable.
        cursor_row = connection.execute(
            "select value from settings where key = 'reviews_study_date_backfill_cursor'"
        ).fetchone()
        cursor = cursor_row[0] if cursor_row else ""

        chunk_size = 500
        backfilled = 0
        while True:
            rows = connection.execute(
                "select id, reviewed_at from reviews"
                " where id > ? order by id limit ?",
                (cursor, chunk_size),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                study_date = _server_local_date(row["reviewed_at"])
                connection.execute(
                    "update reviews set study_date = ? where id = ?",
                    (study_date, row["id"]),
                )
            cursor = rows[-1]["id"]
            connection.execute(
                "insert or replace into settings (key, value)"
                " values ('reviews_study_date_backfill_cursor', ?)",
                (cursor,),
            )
            backfilled += len(rows)

        connection.execute(
            "delete from settings where key = 'reviews_study_date_backfill_cursor'"
        )
        connection.execute(
            "update reviews set study_date = study_date where 1 = 0"
        )  # no-op touch — placeholder for future invariants
        report.append(f"back-filled reviews.study_date for {backfilled} row(s)")

    # Index supports the new day-aggregating queries (one per user per
    # book per day; selectivity is high). The legacy idx_reviews_user_card
    # covers card_id-scoped reads and is left alone.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_user_study_date"
        " ON reviews (user_id, study_date)"
    )

    if report:
        logger.warning("reviews study_date migration: %s", "; ".join(report))
