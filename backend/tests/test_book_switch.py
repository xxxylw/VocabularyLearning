"""PRD ch.9 — 书本封面展示与换书（书架 + 切换零改写 + 快照按书隔离）.

Acceptance criteria covered here:
1. Today cover data: GET /api/books/current returns per-book aggregates
   (totalWords / learnedWords / masteredWords).
2. Bookshelf list: GET /api/books returns every book with isCurrent on the
   current one only.
3. Switch is pointer-only (切换零改写): PUT /api/books/current rewrites the
   settings pointer and never touches reviews / cards / today_queue data.
4. Idempotent switch: switching to the already-current book is a no-op.
5. Switching to a missing book → 404.
6. After switching, Today runs against the new book only — the old book's
   same-day queue snapshot never leaks into the new book's queue, and
   re-entering the original book later reuses its own snapshot.
7. Daily new-word quota is tracked per book.
8. Fallback: a pointer referencing a missing book falls back to the default
   book with a fallbackNotice instead of erroring.
"""

from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.db import connect


def _import_words(client: TestClient, words: list[str], source: str = "IELTS Book") -> None:
    csv_lines = ["sequence_index,word"]
    csv_lines += [f"{index},{word}" for index, word in enumerate(words, start=1)]
    response = client.post(
        "/api/book-words/import",
        files={
            "file": ("book_words.csv", "\n".join(csv_lines).encode(), "text/csv")
        },
        data={"sourceName": source, "replaceExisting": "false"},
    )
    assert response.status_code == 200, response.text


def _start(client: TestClient, day: date, target: int) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={"date": day.isoformat(), "dailyNewWordTarget": target},
    )
    assert response.status_code == 200, response.text
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


def _add_book(book_id: str, title: str, words: list[str]) -> None:
    """Insert a second book directly (v1 ships with a single built-in book,
    so multi-book setups only exist in tests / future imports)."""
    now = "2026-09-03T00:00:00+00:00"
    with connect() as connection:
        connection.execute(
            """
            insert into vocabulary_books (id, title, source, created_at, updated_at)
            values (?, ?, ?, ?, ?)
            """,
            (book_id, title, None, now, now),
        )
        source_id = str(uuid4())
        connection.execute(
            """
            insert into sources (id, type, name, path_or_url, metadata_json, created_at)
            values (?, 'csv', ?, null, null, ?)
            """,
            (source_id, title, now),
        )
        for index, word in enumerate(words, start=1):
            connection.execute(
                """
                insert into book_words (
                    id, source_id, book_id, sequence_index, word_text,
                    normalized_text, import_status, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (str(uuid4()), source_id, book_id, index, word, word, now, now),
            )


def _switch(client: TestClient, book_id: str) -> dict:
    response = client.put("/api/books/current", json={"bookId": book_id})
    assert response.status_code == 200, response.text
    return response.json()


def _table_counts() -> dict[str, int]:
    with connect() as connection:
        return {
            table: connection.execute(f"select count(*) as c from {table}").fetchone()["c"]
            for table in ("reviews", "cards", "entries", "book_words")
        }


def _queue_words(book_id: str, day: date) -> list[tuple[str, str]]:
    with connect() as connection:
        rows = connection.execute(
            """
            select today_queue.queue_type, today_queue.position, words.normalized_text
            from today_queue
            join cards on cards.id = today_queue.card_id
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where today_queue.book_id = ? and today_queue.study_date = ?
            order by today_queue.position
            """,
            (book_id, day.isoformat()),
        ).fetchall()
    return [(row["normalized_text"], row["queue_type"]) for row in rows]


def test_books_list_marks_single_current_book(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline"])

    response = client.get("/api/books")
    assert response.status_code == 200
    books = response.json()["books"]
    assert len(books) == 1
    book = books[0]
    assert book["title"] == "雅思词汇真经"
    assert book["isCurrent"] is True
    assert book["totalWords"] == 2
    assert book["learnedWords"] == 0
    assert book["masteredWords"] == 0


def test_current_book_aggregates_learned_and_mastered(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline", "appeal"])

    session = _start(client, today, 3)
    _review(client, session["cards"][0], today)  # charge → learned
    with connect() as connection:
        # Force one word fully mastered for the aggregate check.
        connection.execute(
            """
            update cards set status = 'mastered'
            where id in (
                select cards.id from cards
                join entries on entries.id = cards.entry_id
                join words on words.id = entries.word_id
                where words.normalized_text = 'appeal'
            )
            """
        )

    response = client.get("/api/books/current")
    assert response.status_code == 200
    body = response.json()
    assert body["totalWords"] == 3
    assert body["learnedWords"] == 1  # charge (reviewed); appeal has no review rows
    assert body["masteredWords"] == 1
    assert body["fallbackNotice"] is None


def test_switch_to_missing_book_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    response = client.put("/api/books/current", json={"bookId": "no-such-book"})
    assert response.status_code == 404


def test_switch_moves_today_queue_to_new_book_only(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline"])

    session = _start(client, today, 2)
    assert [card["word"] for card in session["cards"]] == ["charge", "decline"]
    _review(client, session["cards"][0], today)  # charge reviewed in book A

    _add_book("book-b", "托福核心词汇", ["appeal", "hotel"])
    switched = _switch(client, "book-b")
    assert switched["title"] == "托福核心词汇"
    assert switched["totalWords"] == 2

    # New book gets a fresh snapshot: only its own words, and the old book's
    # reviewed/unreviewed cards never leak into the new queue.
    new_session = _start(client, today, 2)
    assert [card["word"] for card in new_session["cards"]] == ["appeal", "hotel"]
    assert new_session["totalCards"] == 2

    # Old book's snapshot rows are untouched but isolated per book.
    assert _queue_words("default-book", today) == [
        ("charge", "new"),
        ("decline", "new"),
    ]
    assert _queue_words("book-b", today) == [
        ("appeal", "new"),
        ("hotel", "new"),
    ]


def test_switch_back_same_day_reuses_original_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline", "appeal"])

    first = _start(client, today, 3)
    _review(client, first["cards"][0], today)  # charge reviewed

    _add_book("book-b", "托福核心词汇", ["hotel"])
    _switch(client, "book-b")
    _start(client, today, 1)  # book B builds its own snapshot

    # Switch back the same day: the original book's snapshot is reused —
    # the reviewed card stays filtered out, no duplicate queue rows.
    _switch(client, "default-book")
    again = _start(client, today, 3)
    assert [card["word"] for card in again["cards"]] == ["decline", "appeal"]
    assert again["totalCards"] == 3
    assert again["reviewedCards"] == 1
    assert _queue_words("default-book", today) == [
        ("charge", "new"),
        ("decline", "new"),
        ("appeal", "new"),
    ]


def test_switch_is_pointer_only_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline"])
    _start(client, today, 2)

    _add_book("book-b", "托福核心词汇", ["hotel"])
    before = _table_counts()

    first = _switch(client, "book-b")
    second = _switch(client, "book-b")  # idempotent no-op
    assert first["id"] == second["id"] == "book-b"

    # 切换零改写: no review / card / entry / book_words data is touched.
    assert _table_counts() == before

    with connect() as connection:
        pointer = connection.execute(
            "select value from settings where key = 'current_book_id'"
        ).fetchone()
        assert pointer["value"] == "book-b"


def test_new_word_quota_is_tracked_per_book(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge", "decline"])

    # Book A studies its two new words today.
    session = _start(client, today, 2)
    _review(client, session["cards"][0], today)
    _review(client, session["cards"][1], today)

    _add_book("book-b", "托福核心词汇", ["appeal", "hotel"])
    _switch(client, "book-b")

    # Book B still gets its own full quota for the same day.
    new_session = _start(client, today, 2)
    assert [card["word"] for card in new_session["cards"]] == ["appeal", "hotel"]


def test_pointer_to_missing_book_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
    today = date.today()
    client = TestClient(create_app())
    _import_words(client, ["charge"])

    with connect() as connection:
        connection.execute(
            "insert or replace into settings (key, value) values ('current_book_id', 'deleted-book')"
        )

    response = client.get("/api/books/current")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "default-book"
    assert body["title"] == "雅思词汇真经"
    assert "已回退默认书" in body["fallbackNotice"]

    # The app keeps working after the fallback: Today still serves words.
    session = _start(client, today, 1)
    assert [card["word"] for card in session["cards"]] == ["charge"]

    with connect() as connection:
        pointer = connection.execute(
            "select value from settings where key = 'current_book_id'"
        ).fetchone()
        assert pointer["value"] == "default-book"


def test_books_list_marks_switched_book_current(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    _import_words(client, ["charge"])

    _add_book("book-b", "托福核心词汇", ["hotel"])
    _switch(client, "book-b")

    books = client.get("/api/books").json()["books"]
    by_id = {book["id"]: book for book in books}
    assert by_id["default-book"]["isCurrent"] is False
    assert by_id["book-b"]["isCurrent"] is True
