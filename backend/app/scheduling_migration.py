"""One-time migration from the fixed 7-step ladder to SM-2 (P0-4).

Spec: PRD chapter 5 (2026-09-03) - 存量数据迁移（数据安全 · 硬性要求）.

The migration is purely additive: the cards table gains ``ef`` and
``interval_days`` columns; existing stage / due_at / status values are
never rewritten; the reviews table gets zero writes. Existing cards get
their interval derived from the legacy ladder:

- non-mastered: I = INTERVALS[stage] (stage 0-6 -> 0/1/2/4/7/15/30 days)
- mastered:     I = 30, mastered status preserved as-is
- EF starts at 2.5 for every card (stage carries no difficulty signal)

The back-fill runs in chunks and persists its cursor in the settings
table, so an interrupted run resumes where it stopped instead of
restarting. Before/after reconciliation (cards total, per-status
counts, due_at samples, reviews count) is verified once the back-fill
completes; any mismatch aborts the migration.
"""

from __future__ import annotations

import sqlite3

from app.scheduling import DEFAULT_EF, INTERVALS

CHUNK_SIZE = 500
_DONE_KEY = "sm2_backfill_done"
_CURSOR_KEY = "sm2_backfill_cursor"
_SAMPLE_SIZE = 5


def migrate_cards_sm2(connection: sqlite3.Connection, chunk_size: int = CHUNK_SIZE) -> None:
    """Run the SM-2 schema migration + interval back-fill (idempotent).

    Uses positional column access only, so it works with connections
    that do not set ``sqlite3.Row`` as row factory.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cards)")}
    if "ef" not in columns:
        connection.execute(
            "ALTER TABLE cards ADD COLUMN ef real not null default 2.5"
        )
    if "interval_days" not in columns:
        connection.execute(
            "ALTER TABLE cards ADD COLUMN interval_days integer not null default 0"
        )
    connection.commit()

    if _get_setting(connection, _DONE_KEY) == "1":
        return

    before = _snapshot(connection)
    cursor = int(_get_setting(connection, _CURSOR_KEY) or 0)
    migrated = 0
    chunk_index = 0
    while True:
        next_cursor = _backfill_chunk(connection, cursor=cursor, chunk_size=chunk_size)
        if next_cursor is None:
            break
        chunk_index += 1
        migrated += next_cursor - cursor
        cursor = next_cursor
        _set_setting(connection, _CURSOR_KEY, str(cursor))
        connection.commit()
        print(
            f"[sm2-migration] backfill chunk {chunk_index}: "
            f"processed through rowid {cursor} ({migrated} cards so far)"
        )

    _reconcile(connection, before, _snapshot(connection))
    _set_setting(connection, _DONE_KEY, "1")
    _set_setting(connection, _CURSOR_KEY, "")
    connection.commit()
    print(
        f"[sm2-migration] done: {migrated} cards migrated to SM-2 "
        f"(ef={DEFAULT_EF}, interval from legacy stage); "
        "stage/due_at/status untouched, reviews untouched"
    )


def _backfill_chunk(
    connection: sqlite3.Connection, cursor: int, chunk_size: int
) -> int | None:
    """Back-fill one chunk of cards; return the new rowid cursor or None."""
    rows = connection.execute(
        "select rowid, id, stage, status from cards where rowid > ?"
        " order by rowid limit ?",
        (cursor, chunk_size),
    ).fetchall()
    if not rows:
        return None

    connection.executemany(
        "update cards set ef = ?, interval_days = ? where id = ?",
        [
            (DEFAULT_EF, _interval_for_legacy_card(row[2], row[3]), row[1])
            for row in rows
        ],
    )
    return rows[-1][0]


def _interval_for_legacy_card(stage: int, status: str) -> int:
    if status == "mastered":
        return INTERVALS[-1]
    if 0 <= stage < len(INTERVALS):
        return INTERVALS[stage]
    return INTERVALS[-1]


def _snapshot(connection: sqlite3.Connection) -> dict:
    total = connection.execute("select count(*) from cards").fetchone()[0]
    by_status = {
        row[0]: row[1]
        for row in connection.execute(
            "select status, count(*) as total from cards group by status"
        )
    }
    reviews_count = connection.execute("select count(*) from reviews").fetchone()[0]
    due_at_sample = list(
        connection.execute(
            "select id, due_at from cards order by rowid limit ?", (_SAMPLE_SIZE,)
        )
    )
    return {
        "total": total,
        "by_status": by_status,
        "due_at_sample": due_at_sample,
        "reviews_count": reviews_count,
    }


def _reconcile(
    connection: sqlite3.Connection, before: dict, after: dict
) -> None:
    if before != after:
        raise RuntimeError(
            "sm2 migration reconciliation failed: "
            f"before={before!r} after={after!r} "
            "(aborted; done-flag not set, migration will retry on next connect)"
        )


def _get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "select value from settings where key = ?", (key,)
    ).fetchone()
    return row[0] if row is not None else None


def _set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "insert into settings (key, value) values (?, ?)"
        " on conflict(key) do update set value = excluded.value",
        (key, value),
    )
