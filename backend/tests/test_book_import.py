import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app


def test_import_book_words_csv_creates_ordered_words(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    csv_bytes = b"sequence_index,word\n1,abandon\n2,ability\n"
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", csv_bytes, "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 2
    assert response.json()["skipped"] == 0
    assert response.json()["needsReview"] == 0

    progress = client.get("/api/book-words/progress").json()
    assert progress["totalWords"] == 2
    assert progress["nextSequenceIndex"] == 1


def test_book_progress_skips_ready_words(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    csv_bytes = b"sequence_index,word\n1,abandon\n2,ability\n"
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", csv_bytes, "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200

    with connect() as connection:
        connection.execute(
            "update book_words set import_status = 'ready' where sequence_index = 1"
        )

    progress = client.get("/api/book-words/progress").json()
    assert progress["totalWords"] == 2
    assert progress["nextSequenceIndex"] == 2

    with connect() as connection:
        connection.execute("update book_words set import_status = 'ready'")

    progress = client.get("/api/book-words/progress").json()
    assert progress["totalWords"] == 2
    assert progress["nextSequenceIndex"] is None


def test_schema_rejects_unknown_enrichment_source_labels(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))

    with connect() as connection:
        connection.execute(
            """
            insert into sources (id, type, name, path_or_url, metadata_json, created_at)
            values ('source-1', 'csv', 'IELTS Book', null, null, '2026-07-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            insert into words (id, text, normalized_text, created_at, updated_at)
            values ('word-1', 'abandon', 'abandon', '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00')
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into book_words (
                    id,
                    source_id,
                    sequence_index,
                    word_text,
                    normalized_text,
                    definition_source,
                    import_status,
                    created_at,
                    updated_at
                )
                values (
                    'book-word-1',
                    'source-1',
                    1,
                    'abandon',
                    'abandon',
                    'unknown_source',
                    'pending',
                    '2026-07-01T00:00:00+00:00',
                    '2026-07-01T00:00:00+00:00'
                )
                """
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into entries (
                    id,
                    word_id,
                    sense_order,
                    part_of_speech,
                    definition,
                    definition_source,
                    created_at,
                    updated_at
                )
                values (
                    'entry-1',
                    'word-1',
                    1,
                    'verb',
                    'leave behind',
                    'unknown_source',
                    '2026-07-01T00:00:00+00:00',
                    '2026-07-01T00:00:00+00:00'
                )
                """
            )

        connection.execute(
            """
            insert into entries (
                id,
                word_id,
                sense_order,
                part_of_speech,
                definition,
                definition_source,
                created_at,
                updated_at
            )
            values (
                'entry-2',
                'word-1',
                1,
                'verb',
                'leave behind',
                'manual',
                '2026-07-01T00:00:00+00:00',
                '2026-07-01T00:00:00+00:00'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into entry_examples (
                    id,
                    entry_id,
                    example_order,
                    sentence,
                    source,
                    is_primary,
                    created_at,
                    updated_at
                )
                values (
                    'example-1',
                    'entry-2',
                    1,
                    'They abandoned the plan.',
                    'unknown_source',
                    1,
                    '2026-07-01T00:00:00+00:00',
                    '2026-07-01T00:00:00+00:00'
                )
                """
            )
