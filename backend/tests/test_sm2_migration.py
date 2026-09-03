"""SM-2 scheduling migration tests (P0-4).

Spec: PRD chapter 5 (2026-09-03) - 存量数据迁移（数据安全 · 硬性要求）:

- Purely additive: cards gains ef + interval_days; existing stage /
  due_at / status are never rewritten; the reviews table gets zero
  writes during migration.
- Progress mapping: non-mastered cards get I = INTERVALS[stage]
  (stage 0-6 -> 0/1/2/4/7/15/30), mastered cards keep mastered and get
  I = 30; every card starts at EF = 2.5; due_at stays untouched.
- Idempotent and re-runnable; chunked execution so a long-running
  migration can survive interruption and resume from its cursor.
- Reconciliation (cards total, per-status counts, due_at sample,
  reviews count) must match before/after or the migration aborts.
"""

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.db import connect
from app.scheduling import DEFAULT_EF, INTERVALS
from app.scheduling_migration import (
    _backfill_chunk,
    _reconcile,
    migrate_cards_sm2,
)
from app.services import ReviewConflictError, review_card
from app.models import ReviewCardRequest
from datetime import datetime

LEGACY_SCHEMA = (Path(__file__).parent / "legacy_schema.sql").read_text(
    encoding="utf-8"
)

STAGE_INTERVALS = {0: 0, 1: 1, 2: 2, 3: 4, 4: 7, 5: 15, 6: 30}


def _build_legacy_database(db_path: Path, card_count: int = 8) -> None:
    """Create a pre-P0-4 database: cards on the legacy stage ladder."""
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute("PRAGMA foreign_keys=ON")
        for index in range(card_count):
            stage = index % 7
            status = "mastered" if index == 6 else "learning"
            connection.execute(
                "insert into words (id, text, normalized_text, created_at, updated_at)"
                " values (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"word-{index}", f"word{index}", f"word{index}"),
            )
            connection.execute(
                "insert into entries (id, word_id, sense_order, part_of_speech, sense_label,"
                " definition, definition_source, chinese_note, created_at, updated_at)"
                " values (?, ?, 1, 'noun', '', 'a definition', 'oxford_api', null,"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"entry-{index}", f"word-{index}"),
            )
            connection.execute(
                "insert into cards (id, entry_id, status, stage, due_at, created_on, last_reviewed_at)"
                " values (?, ?, ?, ?, ?, '2026-01-01', ?)",
                (
                    f"card-{index}",
                    f"entry-{index}",
                    status,
                    stage,
                    f"2026-01-{(index % 28) + 1:02d}",
                    "2026-01-02T09:00:00+00:00" if index else None,
                ),
            )
            if index:
                connection.execute(
                    "insert into reviews (id, card_id, rating, reviewed_at, previous_stage,"
                    " next_stage, next_due_at) values (?, ?, 'known', '2026-01-02T09:00:00+00:00',"
                    " ?, ?, '2026-01-03')",
                    (f"review-{index}", f"card-{index}", stage - 1, stage),
                )
        connection.commit()
    finally:
        connection.close()


def _cards_snapshot(db_path: Path) -> dict[str, dict]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return {
            row["id"]: dict(row)
            for row in connection.execute(
                "select id, status, stage, due_at, created_on, last_reviewed_at"
                " from cards order by id"
            )
        }
    finally:
        connection.close()


def _reviews_digest(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "select id, card_id, rating, reviewed_at, previous_stage, next_stage, next_due_at"
            " from reviews order by id"
        ).fetchall()
        return hashlib.md5(json.dumps(rows, default=list).encode()).hexdigest()
    finally:
        connection.close()


def _sm2_state(db_path: Path) -> dict[str, tuple]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "select id, ef, interval_days from cards order by id"
            )
        }
    finally:
        connection.close()


def test_migration_adds_columns_and_backfills_intervals(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    with connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(cards)")}
        assert {"ef", "interval_days"} <= columns
        rows = connection.execute(
            "select id, stage, status, ef, interval_days from cards order by id"
        ).fetchall()

    assert len(rows) == 8
    for row in rows:
        assert row["ef"] == pytest.approx(DEFAULT_EF)
        if row["status"] == "mastered":
            assert row["interval_days"] == 30
        else:
            assert row["interval_days"] == STAGE_INTERVALS[row["stage"]]


def test_migration_preserves_existing_columns_and_reviews(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)
    before_cards = _cards_snapshot(db_path)
    before_reviews = _reviews_digest(db_path)
    before_total = len(before_cards)
    before_status_counts = {}
    for row in before_cards.values():
        before_status_counts[row["status"]] = (
            before_status_counts.get(row["status"], 0) + 1
        )

    with connect():
        pass

    after_cards = _cards_snapshot(db_path)
    # due_at / stage / status / created_on / last_reviewed_at untouched
    assert after_cards == before_cards
    # reviews table had zero writes
    assert _reviews_digest(db_path) == before_reviews
    # totals preserved
    assert len(after_cards) == before_total
    after_status_counts = {}
    for row in after_cards.values():
        after_status_counts[row["status"]] = (
            after_status_counts.get(row["status"], 0) + 1
        )
    assert after_status_counts == before_status_counts


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    with connect():
        pass
    first_run = _sm2_state(db_path)
    first_cards = _cards_snapshot(db_path)

    with connect():
        pass
    assert _sm2_state(db_path) == first_run
    assert _cards_snapshot(db_path) == first_cards


def test_migration_runs_chunked_and_resumes_from_cursor(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        # First: add the columns so the backfill can run against them.
        connection.execute("ALTER TABLE cards ADD COLUMN ef real not null default 2.5")
        connection.execute(
            "ALTER TABLE cards ADD COLUMN interval_days integer not null default 0"
        )
        connection.commit()

        # Simulate an interrupted run: one chunk of 3 rows, then "crash".
        cursor = _backfill_chunk(connection, cursor=0, chunk_size=3)
        assert cursor is not None
        connection.execute(
            "insert into settings (key, value) values ('sm2_backfill_cursor', ?)",
            (str(cursor),),
        )
        connection.commit()
        processed = connection.execute(
            "select count(*) from cards where interval_days > 0 or ef != 2.5"
        ).fetchone()[0]
        # chunk of 3 (stages 0,1,2 -> intervals 0,1,2): rows 1 and 2 updated,
        # row 0 stays interval 0 / ef 2.5 by default (nothing to write).
        assert processed == 2
    finally:
        connection.close()

    # Re-running the full migration must complete from the cursor,
    # not restart from scratch nor re-clobber already-migrated rows.
    with connect():
        pass

    state = _sm2_state(db_path)
    assert len(state) == 8
    connection = sqlite3.connect(db_path)
    try:
        for card_id, (ef, interval_days) in state.items():
            assert ef == pytest.approx(DEFAULT_EF)
            index = int(card_id.split("-")[1])
            stage = index % 7
            expected = 30 if index == 6 else STAGE_INTERVALS[stage]
            assert interval_days == expected
    finally:
        connection.close()


def test_post_migration_review_uses_new_algorithm(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    today = date.today()
    with connect() as connection:
        # card-5 is stage 5 -> migrated interval 15, EF 2.5
        connection.execute(
            "update cards set due_at = ? where id = 'card-5'", (today.isoformat(),)
        )
        connection.commit()

    response = review_card(
        "card-5",
        ReviewCardRequest(
            rating="known",
            reviewedAt=datetime.fromisoformat(f"{today.isoformat()}T09:00:00+08:00"),
        ),
    )

    # PRD: round(15 x 2.5) = 38 -> progress continues, not resets
    assert response.nextDueAt == today + timedelta(days=38)
    assert response.status == "learning"
    # stage is frozen as a legacy field
    assert response.previousStage == 5
    assert response.nextStage == 5

    with connect() as connection:
        row = connection.execute(
            "select ef, interval_days, stage, status, due_at from cards where id = 'card-5'"
        ).fetchone()
        assert row["ef"] == pytest.approx(2.6)
        assert row["interval_days"] == 38
        assert row["stage"] == 5
        assert row["status"] == "learning"

    # same-day duplicate review still conflicts
    with pytest.raises(ReviewConflictError):
        review_card(
            "card-5",
            ReviewCardRequest(
                rating="uncertain",
                reviewedAt=datetime.fromisoformat(f"{today.isoformat()}T10:00:00+08:00"),
            ),
        )


def test_post_migration_mastered_card_continues_new_algorithm(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    today = date.today()
    with connect() as connection:
        connection.execute(
            "update cards set due_at = ? where id = 'card-6'", (today.isoformat(),)
        )
        connection.commit()

    response = review_card(
        "card-6",
        ReviewCardRequest(
            rating="known",
            reviewedAt=datetime.fromisoformat(f"{today.isoformat()}T09:00:00+08:00"),
        ),
    )

    # mastered card (I=30, EF=2.5) known -> round(30 x 2.5) = 75, stays mastered
    assert response.status == "mastered"
    assert response.nextDueAt == today + timedelta(days=75)


def test_reconcile_aborts_on_mismatch():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("create table cards (id text, status text, due_at text)")
        before = {
            "total": 3,
            "by_status": {"learning": 3},
            "due_at_sample": [("a", "2026-01-01")],
            "reviews_count": 2,
        }
        after = {
            "total": 2,  # a card vanished
            "by_status": {"learning": 2},
            "due_at_sample": [("a", "2026-01-01")],
            "reviews_count": 2,
        }
        with pytest.raises(RuntimeError, match="reconciliation failed"):
            _reconcile(connection, before, after)
    finally:
        connection.close()


def test_reconcile_passes_on_identical_snapshots():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("create table cards (id text, status text, due_at text)")
        snapshot = {
            "total": 3,
            "by_status": {"learning": 2, "mastered": 1},
            "due_at_sample": [("a", "2026-01-01")],
            "reviews_count": 5,
        }
        _reconcile(connection, snapshot, snapshot)  # must not raise
    finally:
        connection.close()


def test_migrate_cards_sm2_on_fresh_schema(tmp_path):
    db_path = tmp_path / "vocabulary.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        schema = (Path(__file__).parent.parent / "app" / "schema.sql").read_text(
            encoding="utf-8"
        )
        connection.executescript(schema)
        connection.execute(
            "insert into cards (id, entry_id, status, stage, due_at, created_on, last_reviewed_at)"
            " values ('c1', 'e1', 'learning', 2, '2026-01-01', '2026-01-01', null)"
        )
        connection.commit()

        # Fresh-schema cards already carry the columns (NOT NULL defaults);
        # the migration still back-fills the interval from the legacy stage
        # so progress maps the same way for schema-fresh-but-legacy data.
        migrate_cards_sm2(connection)

        row = connection.execute(
            "select ef, interval_days from cards where id = 'c1'"
        ).fetchone()
        assert row[0] == pytest.approx(DEFAULT_EF)
        assert row[1] == INTERVALS[2]
    finally:
        connection.close()


def test_fresh_prepare_cards_start_with_default_ef_and_zero_interval(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", b"sequence_index,word\n1,charge\n", "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post("/api/prepare-jobs", json={"scope": "next", "count": 1})

    with connect() as connection:
        rows = connection.execute("select ef, interval_days from cards").fetchall()
        assert rows
        for row in rows:
            assert row["ef"] == pytest.approx(DEFAULT_EF)
            assert row["interval_days"] == 0
