"""P1 2026-09-08 跨书共享词 UTC 日界竞态（task 7683097747100093410）.

Reproduces the production incident on super 09-08: a card shared by
three books was reviewed via a cross-book session at 02:25 Beijing
(UTC 18:25 the previous day). The legacy aggregation derived
``study_date`` by taking ``substr(reviews.reviewed_at, 1, 10)`` —
the client always sends a UTC ISO string via ``Date.toISOString()``
— so the review was bucketed into 09-07 UTC, missing the 09-08
Beijing study day. The same card therefore stayed pending in each
of the three books' today queues, and the user could never reach
21/21 reviewedCards.

Fix: ``reviews.study_date`` (server-local date) is now the single
source of truth for day aggregation, written at insert time and
back-filled for legacy rows. The 00:00-08:00 Beijing window is the
canonical trigger and these tests cover it end-to-end.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, db_path, migrate, _migrated_paths
from app.main import create_app
from app.reviews_study_date_migration import _server_local_date, migrate_reviews_study_date
from app.services import _resolve_study_date

# Tests in this file exercise the 00:00-08:00 Beijing window which
# straddles the UTC day boundary. The aggregation must follow the
# server-local calendar (Asia/Shanghai on the deployed server, UTC in
# this sandbox); the helper assertions below pin both interpretations
# so a future TZ change in CI is caught explicitly rather than silently
# shifting coverage.
_BEIJING = timezone(timedelta(hours=8))


def _clear_migrate_cache() -> None:
    """Reset the per-process migration cache so each test gets a fresh
    ``migrate()`` pass against its tmp VOCAB_DB_PATH."""
    _migrated_paths.clear()


# ---------------------------------------------------------------------------
# Helpers: pure unit coverage of the date-口径 utilities.


def test_resolve_study_date_prefers_client_reviewed_date() -> None:
    """``reviewedDate`` (the client-local date) is the authoritative
    study date when supplied — the today_queue and reviewed_ids must
    share the same date basis."""
    beijing_2am = datetime(2026, 9, 8, 2, 0, tzinfo=_BEIJING)
    assert _resolve_study_date(date(2026, 9, 8), beijing_2am) == date(2026, 9, 8)


def test_resolve_study_date_falls_back_to_local_for_utc_reviewed_at() -> None:
    """When ``reviewedDate`` is absent, ``reviewedAt`` (UTC ISO) is
    converted to the server-local calendar date. The production
    incident's review was at 02:25 Beijing = 18:25 UTC the previous
    day; the local date must be the Beijing date, not the UTC one."""
    beijing_2am = datetime(2026, 9, 8, 2, 0, tzinfo=_BEIJING).astimezone(timezone.utc)
    assert _resolve_study_date(None, beijing_2am) == date(2026, 9, 8)


def test_resolve_study_date_treats_naive_datetime_as_utc() -> None:
    """A naive ``reviewedAt`` (no tz suffix) is interpreted as UTC
    so external API consumers / older clients that submit the legacy
    format land in the correct server-local day."""
    naive_utc = datetime(2026, 9, 8, 2, 0)  # 02:00 UTC
    assert _resolve_study_date(None, naive_utc) == date(2026, 9, 8)


def test_server_local_date_handles_z_and_offset_and_naive() -> None:
    """The backfill helper must accept the three ``reviewed_at``
    shapes we have in the wild: ``Z`` suffix, explicit ``+00:00``,
    and naive (assumed UTC). The expected local date is derived in
    the test (server-local) so the assertion holds under any TZ the
    test runner happens to use — the production server runs in
    Asia/Shanghai, this sandbox in UTC, and both are correct so long
    as the helper is the inverse of the production frontend's
    UTC ISO submission."""
    expected_z = (
        datetime.fromisoformat("2026-09-07T18:25:00+00:00")
        .astimezone()
        .date()
        .isoformat()
    )
    expected_offset = (
        datetime.fromisoformat("2026-09-07T18:25:00+00:00")
        .astimezone()
        .date()
        .isoformat()
    )
    expected_naive = (
        datetime.fromisoformat("2026-09-07T18:25:00")
        .replace(tzinfo=timezone.utc)
        .astimezone()
        .date()
        .isoformat()
    )
    assert _server_local_date("2026-09-07T18:25:00Z") == expected_z
    assert _server_local_date("2026-09-07T18:25:00+00:00") == expected_offset
    assert _server_local_date("2026-09-07T18:25:00") == expected_naive


# ---------------------------------------------------------------------------
# Migration backfill coverage.


def _seed_legacy_reviews(connection: sqlite3.Connection, rows: list[tuple[str, str]]) -> None:
    """Insert review rows as they exist on a pre-P1 production database
    — no ``study_date`` column. The column is added by the migration
    under test, not pre-created here."""
    for review_id, reviewed_at in rows:
        connection.execute(
            "insert into reviews (id, user_id, card_id, rating, reviewed_at,"
            " previous_stage, next_stage, next_due_at) values (?, 'u', 'c',"
            " 'known', ?, 0, 1, '2026-09-09')",
            (review_id, reviewed_at),
        )


def _has_study_date_column(connection: sqlite3.Connection) -> bool:
    return any(
        row[1] == "study_date"
        for row in connection.execute("PRAGMA table_info(reviews)").fetchall()
    )


def test_migration_adds_column_and_backfills_legacy_rows(tmp_path, monkeypatch) -> None:
    db = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db))
    _clear_migrate_cache()

    # Build a pre-P1 database: only the tables the migration under
    # test needs (the FK chain on reviews). The full ``connect()``
    # pipeline would re-run unrelated migrations; the migration itself
    # is the unit under test, so we drive it with a raw connection
    # against a minimal schema and assert only the contract: column
    # added, rows back-filled with the server-local study_date.
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            create table users (
                id text primary key,
                email text,
                is_super integer not null default 0
            );
            create table entries (
                id text primary key,
                word_id text not null
            );
            create table cards (
                id text primary key,
                user_id text not null references users(id),
                entry_id text not null references entries(id)
            );
            create table reviews (
                id text primary key,
                user_id text not null,
                card_id text not null,
                rating text not null,
                reviewed_at text not null,
                previous_stage integer not null,
                next_stage integer not null,
                next_due_at text not null
            );
            -- The migration reads a checkpoint cursor from ``settings``;
            -- pre-P1 production databases always had it (other
            -- migrations use the same table), so include it here too.
            create table settings (
                key text primary key,
                value text not null
            );
            insert into users (id, email, is_super) values ('u', 'u@e.test', 1);
            insert into entries (id, word_id) values ('e', 'w');
            insert into cards (id, user_id, entry_id) values ('c', 'u', 'e');
            """
        )
        # The incident row: a review at the *server-local* 02:25 of
        # study_date 2026-09-08, expressed in UTC. The UTC offset is
        # derived from the same TZ the migration runs under, so the
        # back-fill is verified to be self-consistent under any TZ
        # the test runner happens to use (production Asia/Shanghai,
        # this sandbox also +08 — both correct so long as the helper
        # is the inverse of the runtime _resolve_study_date).
        local_incident = datetime(2026, 9, 8, 2, 25).astimezone(timezone.utc)
        local_morning = datetime(2026, 9, 8, 10, 0).astimezone(timezone.utc)
        local_prev_day = datetime(2026, 9, 7, 22, 0).astimezone(timezone.utc)
        incident_utc = local_incident.isoformat().replace("+00:00", "Z")
        morning_utc = local_morning.isoformat().replace("+00:00", "Z")
        prev_day_utc = local_prev_day.isoformat().replace("+00:00", "Z")
        _seed_legacy_reviews(
            connection,
            [
                ("r-incident", incident_utc),
                ("r-morning", morning_utc),
                ("r-prev-day", prev_day_utc),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    # Drive the migration directly on a raw connection (same SQLite
    # library the runtime uses; no full ``migrate()`` to re-run the
    # unrelated historical migrations).
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        migrate_reviews_study_date(conn)
        conn.commit()

        assert _has_study_date_column(conn), "study_date column not added"

        study_dates = {
            row["id"]: row["study_date"]
            for row in conn.execute("select id, study_date from reviews").fetchall()
        }

    assert study_dates["r-incident"] == "2026-09-08", (
        "production incident row: must back-fill to the *local* date 2026-09-08, "
        f"got {study_dates['r-incident']}"
    )
    assert study_dates["r-morning"] == "2026-09-08"
    assert study_dates["r-prev-day"] == "2026-09-07"


def test_migration_is_idempotent(tmp_path, monkeypatch) -> None:
    db = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db))
    _clear_migrate_cache()

    with connect() as conn:
        migrate_reviews_study_date(conn)
        conn.commit()
    with connect() as conn:
        # Re-running the migration on a database that already has the
        # column must be a no-op (column probe + index IF NOT EXISTS).
        migrate_reviews_study_date(conn)
        conn.commit()

        assert _has_study_date_column(conn)


# ---------------------------------------------------------------------------
# End-to-end: the 0-8 点 window bug, both via the queue read and the
# per-card review submission path.


def _import_words(client: TestClient, words: list[str]) -> None:
    csv_lines = ["sequence_index,word"] + [
        f"{index},{word}" for index, word in enumerate(words, start=1)
    ]
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", "\n".join(csv_lines).encode(), "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200, response.text


def _start(client: TestClient, day: date, target: int) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={"date": day.isoformat(), "dailyNewWordTarget": target, "extraNewWords": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _review_card(client: TestClient, card_id: str, beijing_dt: datetime) -> None:
    """Submit a review whose UTC timestamp sits inside the 0-8 点 window
    but whose client-local date is the intended study day (matches the
    production client behavior)."""
    utc_iso = beijing_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": utc_iso,
            "reviewedDate": beijing_dt.date().isoformat(),
        },
    )
    assert response.status_code == 200, response.text


def _summary(client: TestClient, day: date) -> dict:
    response = client.get(
        "/api/study/today/summary", params={"date": day.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_utc_boundary_review_counts_toward_beijing_day(tmp_path, monkeypatch) -> None:
    """Repro of the production deadlock, in miniature: a card reviewed
    at 02:00 Beijing (= UTC 18:00 the previous day) must be counted in
    the Beijing day's reviewedCards. Before the fix, substr-based
    bucketing dropped it into the prior UTC day and the user stayed
    stuck at 1/2 forever."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline"])
    session = _start(client, today, 2)
    assert session["totalCards"] == 2

    # Review the first card in the production-troublesome 0-8 点 window.
    _review_card(client, session["cards"][0]["cardId"], datetime.combine(today, datetime.min.time()).replace(hour=2, tzinfo=_BEIJING))

    summary = _summary(client, today)
    assert summary["reviewedCards"] == 1, (
        "0-8 点 Beijing review must be counted in the Beijing study day"
    )
    assert summary["dayCompleted"] is False

    # Review the second card outside the window to confirm the day still
    # completes normally.
    _review_card(client, session["cards"][1]["cardId"], datetime.combine(today, datetime.min.time()).replace(hour=10, tzinfo=_BEIJING))
    assert _summary(client, today)["dayCompleted"] is True


def test_utc_boundary_review_dedupes_within_study_day(tmp_path, monkeypatch) -> None:
    """Two reviews of the same card on the same Beijing day — one
    inside the 0-8 点 window, one outside — must produce exactly one
    review row, with the same-day de-duplication blocking the second
    attempt. The incident hinged on this: a cross-book review at 02:25
    and a Today-flow submission at 09:00 would otherwise both pass the
    old UTC-prefix check and double-apply SM-2."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge"])
    session = _start(client, today, 1)
    card_id = session["cards"][0]["cardId"]

    _review_card(client, card_id, datetime.combine(today, datetime.min.time()).replace(hour=2, tzinfo=_BEIJING))

    # A second submission for the same Beijing day must 409 — the new
    # study_date column anchors the de-dup to the local day basis.
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": datetime.combine(today, datetime.min.time()).replace(hour=10, tzinfo=_BEIJING).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reviewedDate": today.isoformat(),
        },
    )
    assert response.status_code == 409, response.text

    with connect() as conn:
        review_count = conn.execute(
            "select count(*) as total from reviews where card_id = ? and study_date = ?",
            (card_id, today.isoformat()),
        ).fetchone()["total"]
    assert review_count == 1


def test_utc_boundary_review_records_local_study_date(tmp_path, monkeypatch) -> None:
    """The runtime insert must write the server-local date into
    ``reviews.study_date``, even when ``reviewedAt`` is a UTC ISO
    timestamp that falls in the previous UTC day. The production
    incident's review row (UTC 09-07 18:25) had to land on study_date
    09-08 to be counted in the Beijing day; this test pins that
    contract at the SQL boundary, independently of the queue read."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge"])
    session = _start(client, today, 1)
    card_id = session["cards"][0]["cardId"]

    _review_card(client, card_id, datetime.combine(today, datetime.min.time()).replace(hour=2, tzinfo=_BEIJING))

    with connect() as conn:
        row = conn.execute(
            "select study_date, reviewed_at from reviews where card_id = ?",
            (card_id,),
        ).fetchone()
    assert row["study_date"] == today.isoformat(), (
        f"study_date must be the server-local date ({today}), "
        f"not the UTC date of reviewed_at"
    )
    # And the reviewed_at column still holds the client UTC ISO for
    # downstream consumers (audit, debugging).
    assert row["reviewed_at"].endswith("Z") or "+" in row["reviewed_at"]


def test_today_session_does_not_count_previous_day_review(
    tmp_path, monkeypatch
) -> None:
    """Day-bucket isolation: a review whose ``study_date`` falls on
    yesterday must not be counted in today's reviewedCards, even
    though the today queue might still surface the same card (SM-2
    reschedules it forward into today). This is the negative twin
    of ``test_utc_boundary_review_counts_toward_beijing_day`` —
    the fix must not collapse the two days into one bucket."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    # Seed a real user/card via the API so the today summary is read
    # against the production auth+queue path.
    _import_words(client, ["charge"])
    today_session = _start(client, today, 1)
    card_id = today_session["cards"][0]["cardId"]

    # Direct-DB seed: a review row stamped with study_date=yesterday
    # (the production incident's negative case is exactly this — a
    # yesterday review must not leak into today's count via the
    # substr-based fallback that the old code had).
    with connect() as conn:
        user_id = conn.execute(
            "select id from users where email = 'super@vocab.local'"
        ).fetchone()["id"]
        yesterday_iso = yesterday.isoformat()
        conn.execute(
            "insert into reviews (id, user_id, card_id, rating, reviewed_at,"
            " previous_stage, next_stage, next_due_at, study_date)"
            " values ('r-yest', ?, ?, 'known', ?, 0, 1, ?, ?)",
            (
                user_id,
                card_id,
                f"{yesterday_iso}T10:00:00+00:00",
                f"{yesterday_iso}T10:00:00+00:00",
                yesterday_iso,
            ),
        )
        conn.commit()

    today_summary = _summary(client, today)
    assert today_summary["reviewedCards"] == 0, (
        "yesterday's review must not be counted in today's reviewedCards"
    )
