import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.enrichment import PreparedSense
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
    # fallback enrichment no longer emits template examples (PRD decision 2)
    assert _count_rows("entry_examples") == 0
    assert _count_rows("cards") == 2
    assert _count_rows("prepare_jobs") == 1
    assert _count_prepared_graph_rows() == 2

    progress = client.get("/api/book-words/progress")
    assert progress.status_code == 200
    assert progress.json()["nextSequenceIndex"] is None


def test_prepare_next_persists_enrichment_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))

    class FakeProvider:
        def prepare(self, word: str, max_senses: int):
            return [
                PreparedSense(
                    part_of_speech="noun",
                    sense_label="test sense",
                    definition="a test definition",
                    example="A test example helps learners remember the word.",
                    chinese_note=None,
                    definition_source="oxford_api",
                    example_source="oxford_api",
                )
            ]

    monkeypatch.setattr("app.services._create_enrichment_provider", lambda: FakeProvider())
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
            "select definition_source from entries"
        ).fetchone()
        example = connection.execute(
            "select source from entry_examples"
        ).fetchone()

    assert entry["definition_source"] == "oxford_api"
    assert example["source"] == "oxford_api"


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
    assert _count_rows("entry_examples") == 0


def test_prepare_next_overwrites_existing_study_material(tmp_path, monkeypatch):
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

    class ReplacementProvider:
        def prepare(self, word: str, max_senses: int):
            return [
                PreparedSense(
                    part_of_speech="noun",
                    sense_label="replacement 1",
                    definition="replacement definition 1",
                    example="Replacement example one.",
                    definition_source="oxford_api",
                    example_source="oxford_api",
                ),
                PreparedSense(
                    part_of_speech="verb",
                    sense_label="replacement 2",
                    definition="replacement definition 2",
                    example="Replacement example two.",
                    definition_source="oxford_api",
                    example_source="fallback",
                ),
            ][:max_senses]

    monkeypatch.setattr(
        "app.services._create_enrichment_provider", lambda: ReplacementProvider()
    )
    with connect() as connection:
        connection.execute("update book_words set import_status = 'needs_review'")

    response = client.post(
        "/api/prepare-jobs",
        json={
            "scope": "next",
            "count": 1,
            "maxSensesPerWord": 5,
            "overwriteExisting": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["readyCards"] == 2
    assert _count_rows("cards") == 2
    assert _count_rows("entries") == 2
    assert _count_rows("entry_examples") == 2
    with connect() as connection:
        rows = connection.execute(
            """
            select entries.definition, entries.definition_source, entry_examples.source
            from entries
            join entry_examples on entry_examples.entry_id = entries.id
            order by entries.sense_order
            """
        ).fetchall()

    assert [dict(row) for row in rows] == [
        {
            "definition": "replacement definition 1",
            "definition_source": "oxford_api",
            "source": "oxford_api",
        },
        {
            "definition": "replacement definition 2",
            "definition_source": "oxford_api",
            "source": "fallback",
        },
    ]


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
    today = date.today()
    tomorrow = today + timedelta(days=1)
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] >= 1
    card = session["cards"][0]
    assert card["word"] == "charge"
    assert card["definition"]
    # fallback sense has no example rows anymore — no template text
    assert card["examples"] == []

    review = client.post(
        f"/api/cards/{card['cardId']}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{today.isoformat()}T09:00:00+08:00",
            "reviewedDate": today.isoformat(),
        },
    ).json()

    assert review["previousStage"] == 0
    assert review["nextStage"] == 1
    assert review["nextDueAt"] == tomorrow.isoformat()


def test_today_session_prepares_next_book_words_when_no_new_cards_exist(
    tmp_path, monkeypatch
):
    today = date.today()
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

    session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 2},
    ).json()

    assert session["totalCards"] == 2
    assert [card["word"] for card in session["cards"]] == ["charge", "decline"]
    assert _count_rows("prepare_jobs") == 1


def test_today_session_does_not_prepare_more_new_words_after_daily_target_is_met(
    tmp_path, monkeypatch
):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n2,decline\n3,appeal\n4,stable\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )

    first_session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 2},
    ).json()
    assert [card["word"] for card in first_session["cards"]] == ["charge", "decline"]

    for card in first_session["cards"]:
        for card_id in card["cardIds"]:
            review = client.post(
                f"/api/cards/{card_id}/reviews",
                json={
                    "rating": "known",
                    "reviewedAt": f"{today.isoformat()}T09:00:00+08:00",
                    "reviewedDate": today.isoformat(),
                },
            )
            assert review.status_code == 200

    second_session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 2},
    ).json()

    assert second_session["totalCards"] == 0
    assert second_session["cards"] == []


def test_today_session_orders_new_cards_by_book_sequence(
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

    session = client.post(
        "/api/study/today/start",
        json={"date": date.today().isoformat(), "dailyNewWordTarget": 3},
    ).json()

    assert [card["word"] for card in session["cards"]] == [
        "charge",
        "decline",
        "appeal",
    ]


def test_today_session_groups_multiple_senses_into_one_word_card(
    tmp_path, monkeypatch
):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,atmosphere\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 1},
    )

    with connect() as connection:
        word_id = connection.execute(
            "select id from words where normalized_text = 'atmosphere'"
        ).fetchone()["id"]
        entry_id = "entry-atmosphere-2"
        card_id = "card-atmosphere-2"
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
                ?,
                ?,
                2,
                'noun',
                'mood in a place',
                'the mood or feeling in a place',
                'manual',
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00'
            )
            """,
            (entry_id, word_id),
        )
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
                'example-atmosphere-2',
                ?,
                1,
                'The atmosphere in the classroom helped students focus on the task.',
                'manual',
                1,
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00'
            )
            """,
            (entry_id,),
        )
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
            values (?, ?, 'learning', 0, ?, ?, null)
            """,
            (card_id, entry_id, today.isoformat(), today.isoformat()),
        )

    session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] == 1
    assert session["cards"][0]["word"] == "atmosphere"
    assert len(session["cards"][0]["cardIds"]) == 2
    assert len(session["cards"][0]["senses"]) == 2
    assert session["cards"][0]["senses"][1]["definition"] == "the mood or feeling in a place"


def test_today_session_shows_all_senses_for_a_due_word_card(
    tmp_path, monkeypatch
):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,atmosphere\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 1},
    )

    with connect() as connection:
        word_id = connection.execute(
            "select id from words where normalized_text = 'atmosphere'"
        ).fetchone()["id"]
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
                'entry-atmosphere-future',
                ?,
                2,
                'noun',
                'mood in a place',
                'the mood or feeling in a place',
                'manual',
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00'
            )
            """,
            (word_id,),
        )
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
                'example-atmosphere-future',
                'entry-atmosphere-future',
                1,
                'A calm classroom atmosphere can improve concentration.',
                'manual',
                1,
                '2026-07-02T00:00:00+00:00',
                '2026-07-02T00:00:00+00:00'
            )
            """
        )
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
                'card-atmosphere-future',
                'entry-atmosphere-future',
                'learning',
                0,
                ?,
                ?,
                null
            )
            """,
            (tomorrow.isoformat(), today.isoformat()),
        )

    session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] == 1
    assert session["cards"][0]["cardIds"] != ["card-atmosphere-future"]
    assert len(session["cards"][0]["cardIds"]) == 1
    assert len(session["cards"][0]["senses"]) == 2
    assert session["cards"][0]["senses"][1]["definition"] == "the mood or feeling in a place"


def test_duplicate_same_day_review_returns_conflict_without_mutation(
    tmp_path, monkeypatch
):
    today = date.today()
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()
    card_id = session["cards"][0]["cardId"]
    review_payload = {
        "rating": "known",
        "reviewedAt": f"{today.isoformat()}T09:00:00+08:00",
        "reviewedDate": today.isoformat(),
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
    today = date.today()
    tomorrow = today + timedelta(days=1)
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()
    card_id = session["cards"][0]["cardId"]

    review = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{today.isoformat()}T00:30:00+00:00",
            "reviewedDate": today.isoformat(),
        },
    ).json()

    assert review["nextDueAt"] == tomorrow.isoformat()


def test_today_session_limits_new_cards_but_includes_all_due_reviews(
    tmp_path, monkeypatch
):
    today = date.today()
    yesterday = today - timedelta(days=1)
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
                due_at = ?,
                last_reviewed_at = ?
            where id = ?
            """,
            [
                (today.isoformat(), f"{yesterday.isoformat()}T09:00:00+08:00", card_ids[0]),
                (today.isoformat(), f"{yesterday.isoformat()}T09:00:00+08:00", card_ids[1]),
            ],
        )

    session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    queue_types = [card["queueType"] for card in session["cards"]]
    assert session["totalCards"] == 3
    assert queue_types.count("review") == 2
    assert queue_types.count("new") == 1


def test_today_session_new_word_target_counts_words_not_due_reviews_or_senses(
    tmp_path, monkeypatch
):
    today = date.today()
    yesterday = today - timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))

    class FakeProvider:
        def prepare(self, word: str, max_senses: int):
            senses_by_word = {
                "charge": [
                    PreparedSense(
                        part_of_speech="verb",
                        sense_label="risk",
                        definition="to ask an amount of money for something",
                        example="The service may charge a small fee for access.",
                    )
                ],
                "atmosphere": [
                    PreparedSense(
                        part_of_speech="noun",
                        sense_label="air",
                        definition="the mixture of gases around the earth",
                        example="Air pollution can alter the atmosphere over cities.",
                    ),
                    PreparedSense(
                        part_of_speech="noun",
                        sense_label="mood",
                        definition="the mood or feeling in a place",
                        example="A calm atmosphere can help candidates focus during exams.",
                    ),
                ],
                "decline": [
                    PreparedSense(
                        part_of_speech="verb",
                        sense_label="decrease",
                        definition="to become smaller, weaker, or less important",
                        example="The chart shows that rainfall may decline in summer.",
                    )
                ],
                "appeal": [
                    PreparedSense(
                        part_of_speech="noun",
                        sense_label="attraction",
                        definition="a quality that makes people like something",
                        example="Public transport has strong appeal in crowded cities.",
                    )
                ],
            }
            return senses_by_word[word][:max_senses]

    monkeypatch.setattr("app.services._create_enrichment_provider", lambda: FakeProvider())
    client = TestClient(create_app())
    client.post(
        "/api/book-words/import",
        files={
            "file": (
                "book_words.csv",
                b"sequence_index,word\n1,charge\n2,atmosphere\n3,decline\n4,appeal\n",
                "text/csv",
            )
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 4, "maxSensesPerWord": 5},
    )

    with connect() as connection:
        connection.execute(
            """
            update cards
            set stage = 1,
                due_at = ?,
                last_reviewed_at = ?
            where entry_id in (
                select entries.id
                from entries
                join words on words.id = entries.word_id
                where words.normalized_text = 'charge'
            )
            """,
            (today.isoformat(), f"{yesterday.isoformat()}T09:00:00+08:00"),
        )

    session = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 2},
    ).json()

    new_cards = [card for card in session["cards"] if card["queueType"] == "new"]
    review_cards = [card for card in session["cards"] if card["queueType"] == "review"]
    assert session["totalCards"] == 3
    assert [card["word"] for card in review_cards] == ["charge"]
    assert [card["word"] for card in new_cards] == ["atmosphere", "decline"]
    assert len(new_cards[0]["senses"]) == 2


def test_due_reviews_returns_cards_due_on_or_before_date(tmp_path, monkeypatch):
    today = date.today()
    tomorrow = today + timedelta(days=1)
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()
    card = session["cards"][0]
    client.post(
        f"/api/cards/{card['cardId']}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{today.isoformat()}T09:00:00+08:00",
            "reviewedDate": today.isoformat(),
        },
    )

    response = client.get(f"/api/reviews/due?date={tomorrow.isoformat()}")

    assert response.status_code == 200
    due = response.json()
    assert due["date"] == tomorrow.isoformat()
    assert due["total"] == 1
    assert due["cards"][0]["cardId"] == card["cardId"]
    assert due["cards"][0]["queueType"] == "review"


def test_today_session_marks_fallback_cards_as_degraded(tmp_path, monkeypatch):
    """Cards whose definition came from the fallback provider must carry the
    `degraded=True` flag so the frontend can swap fake text for a placeholder
    instead of misleading the learner."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))

    class FallbackProvider:
        def prepare(self, word: str, max_senses: int):
            return [
                PreparedSense(
                    part_of_speech="noun",
                    sense_label="stub sense",
                    definition=f"A learner-friendly IELTS study meaning for '{word}'.",
                    example=(
                        "This is a placeholder example while the real entry is"
                        " being prepared."
                    ),
                    chinese_note=None,
                    definition_source="fallback",
                    example_source="template",
                )
            ]

    monkeypatch.setattr(
        "app.services._create_enrichment_provider", lambda: FallbackProvider()
    )
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] == 1
    card = session["cards"][0]
    assert card["degraded"] is True
    assert card["definitionSource"] == "fallback"
    assert all(
        sense["definitionSource"] == "fallback" for sense in card["senses"]
    )


def test_today_session_keeps_oxford_cards_non_degraded(tmp_path, monkeypatch):
    """When the Oxford provider succeeds the card must NOT be marked degraded
    even if fallback data exists elsewhere in the database."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))

    class OxfordProvider:
        def prepare(self, word: str, max_senses: int):
            return [
                PreparedSense(
                    part_of_speech="noun",
                    sense_label="a price asked",
                    definition="the amount of money that is asked for goods or services",
                    example=(
                        "The restaurant charges a small fee for delivery on"
                        " Sundays."
                    ),
                    chinese_note=None,
                    definition_source="oxford_api",
                    example_source="oxford_api",
                )
            ]

    monkeypatch.setattr(
        "app.services._create_enrichment_provider", lambda: OxfordProvider()
    )
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
        json={"date": today.isoformat(), "dailyNewWordTarget": 1},
    ).json()

    assert session["totalCards"] == 1
    card = session["cards"][0]
    assert card["degraded"] is False
    assert card["definitionSource"] == "oxford_api"
    assert all(
        sense["definitionSource"] == "oxford_api" for sense in card["senses"]
    )


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
            join cards on cards.entry_id = entries.id
            """
        ).fetchone()
    return row["total"]
