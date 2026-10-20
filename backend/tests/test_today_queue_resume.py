"""PRD ch.8 — 今日学习进度断点续传（当日队列快照 + 进度条不清零）.

Acceptance criteria covered here:
1. Queue snapshot resumes: reviewed cards filtered, progress keeps
   numerator offset (position 11 / 40 after 10 reviews), stable across
   repeated refreshes.
2. Reviewed cards never re-enter the queue; rating them again → 409.
3. Snapshot stable within the day; mid-day merge appends to the tail only
   (denominator grows, earlier positions untouched).
4. Cross-day snapshot is voided: next day gets a fresh queue, unreviewed
   review cards come back via due_at, unreviewed new words rejoin the pool
   within the new day's quota, no carry-over merge.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.db import connect


def _import_words(client: TestClient, words: list[str]) -> None:
    csv_lines = ["sequence_index,word"]
    csv_lines += [f"{index},{word}" for index, word in enumerate(words, start=1)]
    response = client.post(
        "/api/book-words/import",
        files={
            "file": ("book_words.csv", "\n".join(csv_lines).encode(), "text/csv")
        },
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200


def _start(client: TestClient, day: date, target: int) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={"date": day.isoformat(), "dailyNewWordTarget": target},
    )
    assert response.status_code == 200
    return response.json()


def _review(client: TestClient, card: dict, day: date, rating: str = "known") -> None:
    for card_id in card["cardIds"]:
        response = client.post(
            f"/api/cards/{card_id}/reviews",
            json={
                "rating": rating,
                "reviewedAt": f"{day.isoformat()}T09:00:00+08:00",
                "reviewedDate": day.isoformat(),
            },
        )
        assert response.status_code == 200, response.text


def _queue_row_count(day: date) -> int:
    with connect() as connection:
        return connection.execute(
            "select count(*) as total from today_queue where study_date = ?",
            (day.isoformat(),),
        ).fetchone()["total"]


def _mark_cards_as_review_cards(card_ids: list[str], due_day: date) -> None:
    """Turn prepared cards into review cards due on ``due_day``."""
    with connect() as connection:
        connection.executemany(
            """
            update cards
            set stage = 1,
                due_at = ?,
                last_reviewed_at = ?
            where id = ?
            """,
            [
                (
                    due_day.isoformat(),
                    f"{(due_day - timedelta(days=1)).isoformat()}T09:00:00+08:00",
                    card_id,
                )
                for card_id in card_ids
            ],
        )


def _primary_card_ids_for_words(words: list[str]) -> dict:
    with connect() as connection:
        rows = connection.execute(
            """
            select words.normalized_text as word, min(cards.id) as card_id
            from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text in (%s)
            group by words.normalized_text
            order by words.normalized_text
            """
            % ", ".join("?" for _ in words),
            tuple(words),
        ).fetchall()
    return {row["word"]: row["card_id"] for row in rows}


def test_resume_shows_position_11_of_40_after_10_reviews(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    words = [f"word{index:02d}" for index in range(1, 41)]
    _import_words(client, words)

    first = _start(client, today, 40)
    assert first["totalCards"] == 40
    assert first["reviewedCards"] == 0
    assert len(first["cards"]) == 40
    assert [card["queuePosition"] for card in first["cards"]] == list(range(1, 41))

    for card in first["cards"][:10]:
        _review(client, card, today)

    second = _start(client, today, 40)
    assert second["totalCards"] == 40
    assert second["reviewedCards"] == 10
    assert len(second["cards"]) == 30
    # Progress numerator = reviewed (10) + position in remaining list (1).
    assert second["cards"][0]["queuePosition"] == 11
    assert second["cards"][0]["word"] == "word11"
    reviewed_words = {card["word"] for card in first["cards"][:10]}
    assert reviewed_words.isdisjoint({card["word"] for card in second["cards"]})

    # Repeated refreshes are idempotent: same cards, no duplicated rows.
    third = _start(client, today, 40)
    assert third == second
    assert _queue_row_count(today) == 40


def test_reviewed_card_rerating_returns_409(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline"])
    session = _start(client, today, 2)

    card = session["cards"][0]
    _review(client, card, today)

    resumed = _start(client, today, 2)
    assert card["word"] not in {entry["word"] for entry in resumed["cards"]}

    response = client.post(
        f"/api/cards/{card['cardIds'][0]}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{today.isoformat()}T10:00:00+08:00",
            "reviewedDate": today.isoformat(),
        },
    )
    assert response.status_code == 409


def test_same_day_snapshot_stable_across_reentries(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal"])
    first = _start(client, today, 2)
    assert [card["word"] for card in first["cards"]] == ["charge", "decline"]
    assert first["totalCards"] == 2

    second = _start(client, today, 2)
    assert [card["word"] for card in second["cards"]] == ["charge", "decline"]
    assert [card["queuePosition"] for card in second["cards"]] == [1, 2]
    assert second["totalCards"] == 2
    assert _queue_row_count(today) == 2


def test_midday_merge_appends_to_tail_and_denominator_grows(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal", "stable", "prevail"])
    first = _start(client, today, 2)
    assert [card["word"] for card in first["cards"]] == ["charge", "decline"]
    assert first["totalCards"] == 2

    # Quota grew mid-day: merge must append to the tail only.
    merged = _start(client, today, 5)
    assert merged["totalCards"] == 5
    assert [card["word"] for card in merged["cards"]] == [
        "charge",
        "decline",
        "appeal",
        "stable",
        "prevail",
    ]
    assert [card["queuePosition"] for card in merged["cards"]] == [1, 2, 3, 4, 5]
    assert _queue_row_count(today) == 5


def test_cross_day_snapshot_voided_no_carry_over_merge(tmp_path, monkeypatch):
    day_one = date.today()
    day_two = day_one + timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal", "stable", "prevail"])

    # Prepare words first so two of them can be turned into review cards
    # before day one's snapshot is created.
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 3, "maxSensesPerWord": 5},
    )
    card_ids_by_word = _primary_card_ids_for_words(["charge", "decline", "appeal"])
    _mark_cards_as_review_cards(
        [card_ids_by_word["charge"], card_ids_by_word["decline"]], day_one
    )

    day_one_session = _start(client, day_one, 3)
    assert day_one_session["totalCards"] == 5
    assert day_one_session["reviewedCards"] == 0
    queue_types = [card["queueType"] for card in day_one_session["cards"]]
    assert queue_types.count("review") == 2
    assert queue_types.count("new") == 3

    # Review the "appeal" new card on day one (quota consumed for day one).
    appeal_card = next(
        card for card in day_one_session["cards"] if card["word"] == "appeal"
    )
    _review(client, appeal_card, day_one)

    # Day two: fresh snapshot. Unreviewed review cards come back via due_at;
    # day-one's reviewed word does not consume day two's new-word quota —
    # "appeal" returns as a due review card per SM-2 (known on a new card
    # schedules the next review the following day); day-one's unreviewed new
    # words rejoin the pool within the new quota; nothing beyond the quota is
    # carried over from yesterday's queue ("prevail" is not queued).
    day_two_session = _start(client, day_two, 1)
    assert day_two_session["totalCards"] == 4
    assert day_two_session["reviewedCards"] == 0
    assert [card["word"] for card in day_two_session["cards"]] == [
        "charge",
        "decline",
        "appeal",
        "stable",
    ]
    assert [card["queueType"] for card in day_two_session["cards"]] == [
        "review",
        "review",
        "review",
        "new",
    ]
    # Day one's snapshot rows are untouched and day two has its own rows.
    assert _queue_row_count(day_one) == 5
    assert _queue_row_count(day_two) == 4
