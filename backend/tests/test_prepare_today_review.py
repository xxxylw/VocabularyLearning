import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app


def test_prepare_next_creates_one_or_more_cards_per_word(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
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

    response = client.post(
        "/api/prepare-jobs",
        json={
            "scope": "next",
            "count": 2,
            "maxSensesPerWord": 5,
            "overwriteExisting": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["processedWords"] == 2
    assert body["readyCards"] >= 2
    assert body["needsReview"] == 0
    assert _count_rows("words") == 2
    assert _count_rows("entries") == 2
    assert _count_rows("entry_examples") == 2
    assert _count_rows("cards") == 2
    assert _count_rows("prepare_jobs") == 1
    assert _count_prepared_graph_rows() == 2

    progress = client.get("/api/book-words/progress")
    assert progress.status_code == 200
    assert progress.json()["nextSequenceIndex"] is None


def test_prepare_next_rejects_negative_count_without_preparing(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": -1, "maxSensesPerWord": 5},
    )

    assert response.status_code == 422
    assert _count_rows("cards") == 0
    assert _count_rows("prepare_jobs") == 0
    progress = client.get("/api/book-words/progress")
    assert progress.json()["nextSequenceIndex"] == 1


def test_prepare_next_does_not_duplicate_cards_for_existing_word(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    first_response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    assert first_response.status_code == 200
    assert _count_rows("cards") == 1

    with connect() as connection:
        connection.execute(
            "update book_words set import_status = 'needs_review'"
        )

    second_response = client.post(
        "/api/prepare-jobs",
        json={
            "scope": "next",
            "count": 1,
            "maxSensesPerWord": 5,
            "overwriteExisting": False,
        },
    )

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["processedWords"] == 1
    assert body["readyCards"] == 0
    assert _count_rows("cards") == 1
    assert _count_rows("entries") == 1
    assert _count_rows("entry_examples") == 1


def test_prepare_next_rejects_overwrite_existing_until_supported(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    response = client.post(
        "/api/prepare-jobs",
        json={
            "scope": "next",
            "count": 1,
            "maxSensesPerWord": 5,
            "overwriteExisting": True,
        },
    )

    assert response.status_code == 400
    assert _count_rows("cards") == 0
    assert _count_rows("prepare_jobs") == 0


def test_prepare_schema_rejects_duplicate_entries_and_cards(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    assert response.status_code == 200

    with connect() as connection:
        entry = connection.execute(
            """
            select entries.id, entries.word_id
            from entries
            join cards on cards.entry_id = entries.id
            """
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into entries (
                    id,
                    word_id,
                    sense_order,
                    part_of_speech,
                    sense_label,
                    definition,
                    definition_source,
                    created_at,
                    updated_at
                )
                values (
                    'entry-duplicate',
                    ?,
                    1,
                    'word',
                    'duplicate',
                    'duplicate',
                    'fallback',
                    '2026-07-01T00:00:00+00:00',
                    '2026-07-01T00:00:00+00:00'
                )
                """,
                (entry["word_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into cards (
                    id,
                    entry_id,
                    status,
                    stage,
                    due_at,
                    created_on,
                    last_reviewed_at
                )
                values (
                    'card-duplicate',
                    ?,
                    'learning',
                    0,
                    '2026-07-01',
                    '2026-07-01',
                    null
                )
                """,
                (entry["id"],),
            )


def test_today_session_combines_ready_cards_and_records_known_review(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )

    session = client.post(
        "/api/study/today/start",
        json={"date": "2026-07-01", "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] >= 1
    card = session["cards"][0]
    assert card["word"] == "charge"
    assert card["definition"]
    assert card["examples"][0]["sentence"]

    review = client.post(
        f"/api/cards/{card['cardId']}/reviews",
        json={
            "rating": "known",
            "reviewedAt": "2026-07-01T09:00:00+08:00",
            "reviewedDate": "2026-07-01",
        },
    ).json()

    assert review["previousStage"] == 0
    assert review["nextStage"] == 1
    assert review["nextDueAt"] == "2026-07-02"


def test_duplicate_same_day_review_returns_conflict_without_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    session = client.post(
        "/api/study/today/start",
        json={"date": "2026-07-01", "dailyNewWordTarget": 1},
    ).json()
    card_id = session["cards"][0]["cardId"]
    review_payload = {
        "rating": "known",
        "reviewedAt": "2026-07-01T09:00:00+08:00",
        "reviewedDate": "2026-07-01",
    }

    first_review = client.post(
        f"/api/cards/{card_id}/reviews",
        json=review_payload,
    )
    second_review = client.post(
        f"/api/cards/{card_id}/reviews",
        json=review_payload,
    )

    assert first_review.status_code == 200
    assert first_review.json()["previousStage"] == 0
    assert first_review.json()["nextStage"] == 1
    assert second_review.status_code == 409
    assert _card_stage(card_id) == 1
    assert _count_reviews(card_id) == 1


def test_reviewed_date_controls_scheduling_date(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    session = client.post(
        "/api/study/today/start",
        json={"date": "2026-07-01", "dailyNewWordTarget": 1},
    ).json()
    card_id = session["cards"][0]["cardId"]

    review = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": "2026-06-30T16:30:00+00:00",
            "reviewedDate": "2026-07-01",
        },
    ).json()

    assert review["nextDueAt"] == "2026-07-02"


def test_today_session_limits_new_cards_but_includes_all_due_reviews(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n2,decline\n3,appeal\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 3, "maxSensesPerWord": 5},
    )

    with connect() as connection:
        card_ids = [
            row["id"]
            for row in connection.execute(
                "select id from cards order by created_on, id"
            ).fetchall()
        ]
        connection.executemany(
            """
            update cards
            set stage = 1,
                due_at = '2026-07-01',
                last_reviewed_at = '2026-06-30T09:00:00+08:00'
            where id = ?
            """,
            [(card_ids[0],), (card_ids[1],)],
        )

    session = client.post(
        "/api/study/today/start",
        json={"date": "2026-07-01", "dailyNewWordTarget": 1},
    ).json()

    queue_types = [card["queueType"] for card in session["cards"]]
    assert session["totalCards"] == 3
    assert queue_types.count("review") == 2
    assert queue_types.count("new") == 1


def test_due_reviews_returns_cards_due_on_or_before_date(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    session = client.post(
        "/api/study/today/start",
        json={"date": "2026-07-01", "dailyNewWordTarget": 1},
    ).json()
    card = session["cards"][0]
    client.post(
        f"/api/cards/{card['cardId']}/reviews",
        json={
            "rating": "known",
            "reviewedAt": "2026-07-01T09:00:00+08:00",
            "reviewedDate": "2026-07-01",
        },
    )

    response = client.get("/api/reviews/due?date=2026-07-02")

    assert response.status_code == 200
    due = response.json()
    assert due["date"] == "2026-07-02"
    assert due["total"] == 1
    assert due["cards"][0]["cardId"] == card["cardId"]
    assert due["cards"][0]["queueType"] == "review"


def _count_rows(table_name: str) -> int:
    allowed_tables = {
        "words",
        "entries",
        "entry_examples",
        "cards",
        "prepare_jobs",
    }
    assert table_name in allowed_tables
    with connect() as connection:
        row = connection.execute(
            f"select count(*) as total from {table_name}"
        ).fetchone()
    return row["total"]


def _count_reviews(card_id: str) -> int:
    with connect() as connection:
        row = connection.execute(
            "select count(*) as total from reviews where card_id = ?",
            (card_id,),
        ).fetchone()
    return row["total"]


def _card_stage(card_id: str) -> int:
    with connect() as connection:
        row = connection.execute(
            "select stage from cards where id = ?",
            (card_id,),
        ).fetchone()
    return row["stage"]


def _count_prepared_graph_rows() -> int:
    with connect() as connection:
        row = connection.execute(
            """
            select count(*) as total
            from words
            join entries on entries.word_id = words.id
            join entry_examples on entry_examples.entry_id = entries.id
            join cards on cards.entry_id = entries.id
            """
        ).fetchone()
    return row["total"]
