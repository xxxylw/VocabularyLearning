"""Vocabulary book (P1) migration tests.

Covers the vocabulary_books data model, the legacy-database migration
(backfilling book_id onto existing book_words), idempotency of the
migration, data preservation, and the current-book API endpoint.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.books import DEFAULT_BOOK_ID, DEFAULT_BOOK_TITLE
from app.db import connect
from app.main import create_app

# The pre-P1 schema: identical to the shipped schema.sql before the
# vocabulary_books table and book_words.book_id column were introduced.
LEGACY_SCHEMA = (Path(__file__).parent.parent / "tests" / "legacy_schema.sql").read_text(
    encoding="utf-8"
)


def _build_legacy_database(db_path: Path) -> None:
    """Create a pre-P1 database with real study data in every table."""
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute("PRAGMA foreign_keys=ON")

        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS Book', null, null, '2025-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline", "abandon"), start=1):
            connection.execute(
                """
                insert into book_words (
                    id, source_id, sequence_index, word_text, normalized_text,
                    part_of_speech, definition, definition_source, chinese_note,
                    import_status, created_at, updated_at
                )
                values (?, 'source-1', ?, ?, ?, null, null, null, null, 'ready',
                        '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')
                """,
                (f"bw-{index}", index, word, word),
            )
            connection.execute(
                "insert into words (id, text, normalized_text, created_at, updated_at)"
                " values (?, ?, ?, '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')",
                (f"word-{index}", word, word),
            )
            connection.execute(
                """
                insert into entries (
                    id, word_id, sense_order, part_of_speech, sense_label,
                    definition, definition_source, chinese_note,
                    created_at, updated_at
                )
                values (?, ?, 1, 'noun', 'a sense', 'a definition', 'oxford_api', null,
                        '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')
                """,
                (f"entry-{index}", f"word-{index}"),
            )
            connection.execute(
                "insert into entry_examples (id, entry_id, example_order, sentence, source, is_primary, created_at, updated_at)"
                " values (?, ?, 1, 'an example sentence', 'oxford_api', 1,"
                " '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')",
                (f"example-{index}", f"entry-{index}"),
            )
            connection.execute(
                "insert into cards (id, entry_id, status, stage, due_at, created_on, last_reviewed_at)"
                " values (?, ?, 'learning', 1, '2025-01-02', '2025-01-01', '2025-01-01T10:00:00+00:00')",
                (f"card-{index}", f"entry-{index}"),
            )
            connection.execute(
                "insert into reviews (id, card_id, rating, reviewed_at, previous_stage, next_stage, next_due_at)"
                " values (?, ?, 'known', '2025-01-01T10:00:00+00:00', 0, 1, '2025-01-02')",
                (f"review-{index}", f"card-{index}"),
            )
        connection.execute(
            "insert into pronunciation_cache (normalized_word, response_json, status, retry_after, cached_at)"
            " values ('charge', '{}', 'ready', null, '2025-01-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()


def _snapshot_counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            table: connection.execute(f"select count(*) from {table}").fetchone()[0]
            for table in (
                "sources",
                "book_words",
                "words",
                "entries",
                "entry_examples",
                "cards",
                "reviews",
                "pronunciation_cache",
            )
        }
    finally:
        connection.close()


def test_migration_backfills_book_id_without_data_loss(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)
    before = _snapshot_counts(db_path)

    # connect() runs migrate() on every open, which is exactly what happens
    # when the user restarts the backend.
    with connect() as connection:
        book = connection.execute(
            "select * from vocabulary_books where id = ?", (DEFAULT_BOOK_ID,)
        ).fetchone()
        assert book is not None
        assert book["title"] == DEFAULT_BOOK_TITLE

        unattributed = connection.execute(
            "select count(*) from book_words where book_id is null"
        ).fetchone()[0]
        assert unattributed == 0
        attributed = connection.execute(
            "select count(*) from book_words where book_id = ?", (DEFAULT_BOOK_ID,)
        ).fetchone()[0]
        assert attributed == 3
        assert attributed == before["book_words"]

    assert _snapshot_counts(db_path) == before


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)

    with connect() as first_connection:
        first_run = first_connection.execute(
            "select created_at, updated_at from vocabulary_books where id = ?",
            (DEFAULT_BOOK_ID,),
        ).fetchone()
        rows_after_first = _snapshot_counts(db_path)

    with connect() as second_connection:
        book = second_connection.execute(
            "select created_at, updated_at from vocabulary_books where id = ?",
            (DEFAULT_BOOK_ID,),
        ).fetchone()
        # INSERT OR IGNORE keeps the original row; backfill only touches
        # rows where book_id is null (none the second time around).
        assert book["created_at"] == first_run["created_at"]
        assert book["updated_at"] == first_run["updated_at"]

    assert _snapshot_counts(db_path) == rows_after_first

    # A third connection must not raise on the existing book_id column.
    with connect() as third_connection:
        assert third_connection.execute(
            "select count(*) from book_words where book_id = ?", (DEFAULT_BOOK_ID,)
        ).fetchone()[0] == 3


def test_fresh_database_gets_default_book(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))

    with connect() as connection:
        book = connection.execute(
            "select * from vocabulary_books where id = ?", (DEFAULT_BOOK_ID,)
        ).fetchone()
        assert book is not None
        assert book["title"] == DEFAULT_BOOK_TITLE
        total = connection.execute("select count(*) from book_words").fetchone()[0]
        assert total == 0


def test_books_current_endpoint_returns_default_book(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    response = client.get("/api/books/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == DEFAULT_BOOK_ID
    assert body["title"] == DEFAULT_BOOK_TITLE
    assert body["totalWords"] == 0


def test_books_current_endpoint_counts_migrated_words(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    _build_legacy_database(db_path)
    client = TestClient(create_app())

    response = client.get("/api/books/current")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == DEFAULT_BOOK_TITLE
    assert body["totalWords"] == 3


def test_import_attributing_words_to_current_book(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n2,decline\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 2

    with connect() as connection:
        unattributed = connection.execute(
            "select count(*) from book_words where book_id is null"
        ).fetchone()[0]
        assert unattributed == 0
        attributed = connection.execute(
            "select count(*) from book_words where book_id = ?", (DEFAULT_BOOK_ID,)
        ).fetchone()[0]
        assert attributed == 2
