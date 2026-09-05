"""v2 batch 2 (C-05..C-07): multi-user data isolation tests.

Two layers:

* Migration acceptance — a legacy (pre-batch-2) database with real study
  data is migrated on connect(): every row is attributed to the super
  account, nothing is lost, the migration is idempotent, and the shared
  content layer (words/entries/examples/book_words) is untouched.
* Isolation matrix — two real accounts drive the study API with their
  own Bearer tokens: due queues, SM-2 state, progress statistics, the
  current-book pointer and today's queue snapshot are all per-user; a
  user cannot read or write another user's data, and a second user
  preparing the same book reuses the shared entries without another
  provider (Oxford) call.

The module uses the real auth flow (``real_auth`` marker — no conftest
TestClient header injection).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.db import connect
from app.main import create_app

pytestmark = pytest.mark.real_auth

LEGACY_SCHEMA = (Path(__file__).parent / "legacy_schema.sql").read_text(
    encoding="utf-8"
)


@pytest.fixture
def no_email(monkeypatch):
    monkeypatch.setattr(
        "app.emailing.send_verification_email", lambda to, token: None
    )


def _register_and_login(client: TestClient, email: str, password: str = "pass-2026a") -> str:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code in (200, 201), response.text
    row = (
        _client_db().execute("select id from users where email = ?", (email,)).fetchone()
    )
    assert row is not None
    auth_module.mark_user_verified(str(row["id"]))
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def legacy_db(tmp_path, monkeypatch) -> Path:
    """Pre-batch-2 database: cards/reviews without user_id, the
    current-book pointer in the global settings table."""

    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2025-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'ready',"
                " '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
            connection.execute(
                "insert into words (id, text, normalized_text, created_at, updated_at)"
                " values (?, ?, ?, '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')",
                (f"word-{index}", word, word),
            )
            connection.execute(
                "insert into entries (id, word_id, sense_order, part_of_speech,"
                " sense_label, definition, definition_source, chinese_note, created_at, updated_at)"
                " values (?, ?, 1, 'noun', 'sense', 'definition', 'fallback', null,"
                " '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')",
                (f"entry-{index}", f"word-{index}"),
            )
            connection.execute(
                "insert into cards (id, entry_id, status, stage, due_at, created_on,"
                " last_reviewed_at) values (?, ?, 'learning', 1, '2025-01-02', '2025-01-01',"
                " '2025-01-01T10:00:00+00:00')",
                (f"card-{index}", f"entry-{index}"),
            )
            connection.execute(
                "insert into reviews (id, card_id, rating, reviewed_at, previous_stage,"
                " next_stage, next_due_at) values (?, ?, 'known',"
                " '2025-01-01T10:00:00+00:00', 0, 1, '2025-01-02')",
                (f"review-{index}", f"card-{index}"),
            )
        # legacy_schema.sql predates the Today queue (P0-3): build the
        # pre-batch-2 shapes inline so the queue rebuild is exercised.
        connection.execute(
            "create table today_queue (id text primary key, book_id text not null,"
            " study_date text not null, position integer not null, card_id text not null,"
            " queue_type text not null, created_at text not null,"
            " unique (book_id, study_date, position))"
        )
        connection.execute(
            "create table today_queue_snapshots (book_id text not null,"
            " study_date text not null, created_at text not null,"
            " primary key (book_id, study_date))"
        )
        connection.execute(
            "insert into settings (key, value) values ('current_book_id', 'default-book')"
        )
        connection.execute(
            "insert into today_queue (id, book_id, study_date, position, card_id,"
            " queue_type, created_at) values ('q-1', 'default-book', '2025-01-01', 1,"
            " 'card-1', 'new', '2025-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "insert into today_queue_snapshots (book_id, study_date, created_at)"
            " values ('default-book', '2025-01-01', '2025-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()
    return db_path


def _client_db():
    with connect() as connection:
        return connection.execute("select 1").fetchone() and connection


def _count(db_path: Path, sql: str, params: tuple = ()) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql, params).fetchone()[0]
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# C-05/C-06 migration acceptance (legacy database → per-user schema)
# ---------------------------------------------------------------------------


def test_migration_attributes_all_study_data_to_super(legacy_db):
    with connect() as connection:  # triggers migrate()
        super_id = connection.execute(
            "select id from users where is_super = 1"
        ).fetchone()["id"]

    assert _count(legacy_db, "select count(*) from cards where user_id = ?", (super_id,)) == 2
    assert _count(legacy_db, "select count(*) from reviews where user_id = ?", (super_id,)) == 2
    assert _count(legacy_db, "select count(*) from today_queue where user_id = ?", (super_id,)) == 1
    assert (
        _count(
            legacy_db,
            "select count(*) from today_queue_snapshots where user_id = ?",
            (super_id,),
        )
        == 1
    )
    # zero rows left unattributed / zero data lost
    assert _count(legacy_db, "select count(*) from cards") == 2
    assert _count(legacy_db, "select count(*) from reviews") == 2
    # the global current-book pointer moved into the super user's row
    assert _count(legacy_db, "select count(*) from settings where key = 'current_book_id'") == 0
    assert (
        _count(
            legacy_db,
            "select count(*) from user_settings where user_id = ? and key = 'current_book_id'"
            " and value = 'default-book'",
            (super_id,),
        )
        == 1
    )
    # shared content layer untouched
    assert _count(legacy_db, "select count(*) from words") == 2
    assert _count(legacy_db, "select count(*) from entries") == 2
    assert _count(legacy_db, "select count(*) from book_words") == 2


def test_migration_is_idempotent(legacy_db):
    with connect() as connection:
        pass
    fingerprint = {
        table: _count(legacy_db, f"select count(*) from {table}")
        for table in ("cards", "reviews", "today_queue", "today_queue_snapshots")
    }
    with connect() as connection:  # second run: no-op
        pass
    assert {
        table: _count(legacy_db, f"select count(*) from {table}")
        for table in ("cards", "reviews", "today_queue", "today_queue_snapshots")
    } == fingerprint


def test_fresh_schema_has_per_user_unique_constraints(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "fresh.sqlite"))
    with connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(cards)")}
        assert "user_id" in columns
        for uid, email in (("user-a", "a@example.com"), ("user-b", "b@example.com")):
            connection.execute(
                "insert into users (id, email, password_hash, email_verified, is_super,"
                " created_at, updated_at) values (?, ?, 'x', 1, 0,"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (uid, email),
            )
        connection.execute(
            "insert into words (id, text, normalized_text, created_at, updated_at)"
            " values ('w1', 'charge', 'charge', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "insert into entries (id, word_id, sense_order, part_of_speech, sense_label,"
            " definition, definition_source, chinese_note, created_at, updated_at)"
            " values ('e1', 'w1', 1, 'noun', 's', 'd', 'fallback', null,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
        # (user_id, entry_id) uniqueness: one card per entry per user
        connection.execute(
            "insert into cards (id, user_id, entry_id, status, stage, due_at, created_on)"
            " values ('c1', 'user-a', 'e1', 'learning', 0, '2026-01-01', '2026-01-01')"
        )
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        with connect() as connection:
            connection.execute(
                "insert into cards (id, user_id, entry_id, status, stage, due_at, created_on)"
                " values ('c2', 'user-a', 'e1', 'learning', 0, '2026-01-01', '2026-01-01')"
            )
            connection.commit()
    # ...but a different user may own a card of the same entry
    with connect() as connection:
        connection.execute(
            "insert into cards (id, user_id, entry_id, status, stage, due_at, created_on)"
            " values ('c3', 'user-b', 'e1', 'learning', 0, '2026-01-01', '2026-01-01')"
        )
        connection.commit()


def test_reviews_require_existing_user(legacy_db):
    with connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into reviews (id, user_id, card_id, rating, reviewed_at,"
                " previous_stage, next_stage, next_due_at)"
                " values ('r-x', 'no-such-user', 'card-1', 'known',"
                " '2026-01-01T00:00:00+00:00', 0, 1, '2026-01-02')"
            )
            connection.commit()


# ---------------------------------------------------------------------------
# C-07 isolation matrix: two real accounts on one book
# ---------------------------------------------------------------------------


@pytest.fixture
def two_users(tmp_path, monkeypatch, no_email):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "iso.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    client = TestClient(create_app())
    alice = _register_and_login(client, "alice@example.com")
    bob = _register_and_login(client, "bob@example.com")
    return client, alice, bob


def _import(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", b"sequence_index,word\n1,charge\n2,decline\n", "text/csv")},
        data={"sourceName": "IELTS", "replaceExisting": "false"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # shared content management is super-only
    assert response.status_code == 403


def _prepare(client: TestClient, token: str, count: int = 2) -> dict:
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": count, "overwriteExisting": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start(client: TestClient, token: str, today: date, target: int = 2) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": target},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _review(client: TestClient, token: str, card_id: str, today: date) -> dict:
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{today.isoformat()}T09:00:00+08:00",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_regular_user_cannot_import_book_words(two_users):
    client, alice, _bob = two_users
    _import(client, alice)


def test_second_user_reuses_shared_entries_without_new_provider_calls(
    two_users, monkeypatch
):
    client, alice, bob = two_users

    calls = {"count": 0}
    from app.enrichment import FallbackEnrichmentProvider

    class CountingProvider(FallbackEnrichmentProvider):
        def prepare(self, word: str, max_senses: int):
            calls["count"] += 1
            return super().prepare(word, max_senses)

    monkeypatch.setattr(
        "app.services._create_enrichment_provider", lambda: CountingProvider()
    )

    # The super account imports the shared word list once.
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    alice_job = _prepare(client, alice)
    assert alice_job["readyCards"] == 2
    assert calls["count"] == 2  # one provider call per new word

    bob_job = _prepare(client, bob)
    assert bob_job["readyCards"] == 2
    assert calls["count"] == 2  # entries are shared: no new provider calls

    with connect() as connection:
        assert connection.execute("select count(*) from entries").fetchone()[0] == 2
        assert connection.execute("select count(*) from cards").fetchone()[0] == 4


def test_due_queue_and_review_state_are_isolated(two_users):
    client, alice, bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    _prepare(client, alice)
    _prepare(client, bob)
    today = date.today()

    alice_session = _start(client, alice, today, 2)
    assert alice_session["totalCards"] == 2

    # Bob reviews one of his own cards; Alice's SM-2 state must not move.
    bob_session = _start(client, bob, today, 2)
    bob_card_id = bob_session["cards"][0]["cardIds"][0]
    outcome = _review(client, bob, bob_card_id, today)
    assert outcome["status"] in ("learning", "mastered")

    alice_ids = {cid for card in alice_session["cards"] for cid in card["cardIds"]}
    assert bob_card_id not in alice_ids

    with connect() as connection:
        alice_card = connection.execute(
            "select status, last_reviewed_at from cards where id = ?",
            (next(iter(alice_ids)),),
        ).fetchone()
        assert alice_card["last_reviewed_at"] is None  # untouched by Bob's review

    # Alice's due list still reports her own cards only.
    due = client.get(
        "/api/reviews/due", params={"date": today.isoformat()},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert due.status_code == 200
    due_ids = {cid for card in due.json()["cards"] for cid in card["cardIds"]}
    assert due_ids == alice_ids


def test_user_cannot_review_anothers_card(two_users):
    client, alice, bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    _prepare(client, alice)
    _prepare(client, bob)
    today = date.today()
    alice_session = _start(client, alice, today, 2)
    alice_card_id = alice_session["cards"][0]["cardIds"][0]

    # Bob reviews Alice's card id → not his data → 404, and no review row.
    response = client.post(
        f"/api/cards/{alice_card_id}/reviews",
        json={"rating": "known", "reviewedAt": f"{today.isoformat()}T09:00:00+08:00"},
        headers={"Authorization": f"Bearer {bob}"},
    )
    assert response.status_code == 404
    with connect() as connection:
        row = connection.execute(
            "select count(*) from reviews where card_id = ?", (alice_card_id,)
        ).fetchone()
        assert row[0] == 0


def test_regular_user_cannot_overwrite_existing(two_users):
    client, alice, _bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "overwriteExisting": True},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert response.status_code == 403


def test_progress_statistics_are_per_user(two_users):
    client, alice, bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    _prepare(client, alice)
    _prepare(client, bob)
    today = date.today()
    alice_session = _start(client, alice, today, 2)
    for card in alice_session["cards"]:
        _review(client, alice, card["cardIds"][0], today)

    alice_books = client.get(
        "/api/books/current", headers={"Authorization": f"Bearer {alice}"}
    ).json()
    assert alice_books["learnedWords"] == 2

    bob_books = client.get(
        "/api/books/current", headers={"Authorization": f"Bearer {bob}"}
    ).json()
    assert bob_books["learnedWords"] == 0
    assert bob_books["totalWords"] == 2


def test_current_book_pointer_is_per_user(two_users):
    client, alice, bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "insert into vocabulary_books (id, title, description, source, created_at, updated_at)"
            " values ('book-b', 'Book B', null, 'csv',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "insert into book_words (id, book_id, source_id, sequence_index, word_text,"
            " normalized_text, import_status, created_at, updated_at)"
            " values ('bw-b', 'book-b', 'source-1', 1, 'hotel', 'hotel', 'pending',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()

    response = client.put(
        "/api/books/current",
        json={"bookId": "book-b"},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert response.status_code == 200, response.text

    alice_current = client.get(
        "/api/books/current", headers={"Authorization": f"Bearer {alice}"}
    ).json()
    assert alice_current["id"] == "book-b"

    bob_current = client.get(
        "/api/books/current", headers={"Authorization": f"Bearer {bob}"}
    ).json()
    assert bob_current["id"] == "default-book"  # Bob's pointer never moved


def test_today_queue_snapshot_is_per_user(two_users):
    client, alice, bob = two_users
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    _prepare(client, alice)
    _prepare(client, bob)
    today = date.today()

    alice_session = _start(client, alice, today, 1)
    assert alice_session["totalCards"] == 1

    # Bob's snapshot is independent: full quota, own card ids.
    bob_session = _start(client, bob, today, 2)
    assert bob_session["totalCards"] == 2
    alice_ids = {cid for card in alice_session["cards"] for cid in card["cardIds"]}
    bob_ids = {cid for card in bob_session["cards"] for cid in card["cardIds"]}
    assert not alice_ids & bob_ids

    with connect() as connection:
        rows = connection.execute(
            "select user_id, count(*) as total from today_queue group by user_id"
        ).fetchall()
        by_user = {row["user_id"]: row["total"] for row in rows}
        assert sum(by_user.values()) == 3


def test_unauthenticated_requests_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "anon.sqlite"))
    client = TestClient(create_app())
    for method, path in (
        ("get", "/api/books/current"),
        ("get", "/api/reviews/due?date=2026-01-01"),
        ("post", "/api/prepare-jobs"),
        ("post", "/api/study/today/start"),
    ):
        if method == "post":
            response = client.post(path, json={})
        else:
            response = client.get(path)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_register_rejects_password_without_digits_or_letters(tmp_path, monkeypatch, no_email):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "policy.sqlite"))
    client = TestClient(create_app())
    for password in ("letters-only", "12345678", "short1"):
        response = client.post(
            "/api/auth/register",
            json={"email": f"user-{password}@example.com", "password": password},
        )
        assert response.status_code == 400, password
    response = client.post(
        "/api/auth/register",
        json={"email": "ok@example.com", "password": "goodpass1"},
    )
    assert response.status_code in (200, 201)
