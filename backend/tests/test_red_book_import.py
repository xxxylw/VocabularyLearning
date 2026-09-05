"""PRD ch.10 — 内置第二本词书（考研英语红宝书）导入管道.

Acceptance criteria covered here:
1. 词表整理 + 导入管道: the red-book word list enters the existing
   book_words pipeline as "one word per line with a layer column" and is
   attributed to the second book's book_id (RED_BOOK_ID).
2. 导入幂等: re-importing the same CSV is a no-op (imported=0, row counts
   unchanged).
3. 隔离/不污染: the default IELTS book's words, book row and aggregates
   are untouched by the red-book import.
4. 跨书重复词独立成行: the same word may appear in both books as two
   independent book_words rows.
5. prepare-jobs with bookId targets that book without moving the
   current-book pointer (prepare ≠ switch); unknown bookId → 404.
6. The red book shows up in GET /api/books with its own aggregates and can
   be switched to (数据就绪即可入架/可选).
"""

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import super_user_id
from app.db import connect
from app.books import DEFAULT_BOOK_ID, RED_BOOK_ID, RED_BOOK_TITLE

RED_CSV_LINES = [
    "sequence_index,word,layer",
    "1,radiate,必考词",
    "2,abandon,必考词",
    "3,zebra,基础词",
    "4,think tank,超纲词",
]


def _import_red_book(client: TestClient, replace: str = "false") -> dict:
    response = client.post(
        "/api/book-words/import",
        files={
            "file": (
                "kaoyan_hongbaoshu_2027.csv",
                "\n".join(RED_CSV_LINES).encode(),
                "text/csv",
            )
        },
        data={
            "sourceName": "考研英语红宝书词表",
            "replaceExisting": replace,
            "bookId": RED_BOOK_ID,
            "bookTitle": RED_BOOK_TITLE,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _import_default_book(client: TestClient, words: list[str]) -> None:
    csv_lines = ["sequence_index,word"]
    csv_lines += [f"{index},{word}" for index, word in enumerate(words, start=1)]
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", "\n".join(csv_lines).encode(), "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200, response.text


def test_red_book_import_attributes_words_with_layer(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    result = _import_red_book(client)

    assert result["imported"] == 4
    assert result["skipped"] == 0

    with connect() as connection:
        book = connection.execute(
            "select * from vocabulary_books where id = ?", (RED_BOOK_ID,)
        ).fetchone()
        assert book is not None
        assert book["title"] == RED_BOOK_TITLE

        rows = connection.execute(
            """
            select sequence_index, word_text, layer, book_id, import_status
            from book_words
            where book_id = ?
            order by sequence_index
            """,
            (RED_BOOK_ID,),
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "sequence_index": 1,
                "word_text": "radiate",
                "layer": "必考词",
                "book_id": RED_BOOK_ID,
                "import_status": "pending",
            },
            {
                "sequence_index": 2,
                "word_text": "abandon",
                "layer": "必考词",
                "book_id": RED_BOOK_ID,
                "import_status": "pending",
            },
            {
                "sequence_index": 3,
                "word_text": "zebra",
                "layer": "基础词",
                "book_id": RED_BOOK_ID,
                "import_status": "pending",
            },
            {
                "sequence_index": 4,
                "word_text": "think tank",
                "layer": "超纲词",
                "book_id": RED_BOOK_ID,
                "import_status": "pending",
            },
        ]


def test_red_book_import_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    _import_red_book(client)
    with connect() as connection:
        first_count = connection.execute(
            "select count(*) as c from book_words where book_id = ?", (RED_BOOK_ID,)
        ).fetchone()["c"]

    second = _import_red_book(client)

    assert second["imported"] == 0
    assert second["skipped"] == 4

    with connect() as connection:
        second_count = connection.execute(
            "select count(*) as c from book_words where book_id = ?", (RED_BOOK_ID,)
        ).fetchone()["c"]
    assert second_count == first_count


def test_red_book_import_does_not_pollute_default_book(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    _import_default_book(client, ["abandon", "atmosphere"])
    _import_red_book(client)

    with connect() as connection:
        default_words = connection.execute(
            "select word_text from book_words where book_id = ? order by sequence_index",
            (DEFAULT_BOOK_ID,),
        ).fetchall()
        assert [row["word_text"] for row in default_words] == ["abandon", "atmosphere"]

        # 跨书重复词独立成行: "abandon" exists in both books.
        abandon_rows = connection.execute(
            """
            select book_id from book_words where normalized_text = 'abandon'
            order by book_id
            """
        ).fetchall()
        assert [row["book_id"] for row in abandon_rows] == sorted(
            [DEFAULT_BOOK_ID, RED_BOOK_ID]
        )

    progress = client.get("/api/book-words/progress").json()
    assert progress["totalWords"] == 2
    assert progress["nextSequenceIndex"] == 1

    books = client.get("/api/books").json()["books"]
    by_id = {book["id"]: book for book in books}
    assert set(by_id) == {DEFAULT_BOOK_ID, RED_BOOK_ID}
    assert by_id[DEFAULT_BOOK_ID]["totalWords"] == 2
    assert by_id[RED_BOOK_ID]["totalWords"] == 4
    assert by_id[DEFAULT_BOOK_ID]["isCurrent"] is True
    assert by_id[RED_BOOK_ID]["isCurrent"] is False


def test_import_into_missing_book_without_title_is_rejected(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/book-words/import",
        files={
            "file": ("book_words.csv", b"sequence_index,word\n1,abandon\n", "text/csv")
        },
        data={"sourceName": "Ghost Book", "replaceExisting": "false", "bookId": "ghost"},
    )

    assert response.status_code == 400

    with connect() as connection:
        book = connection.execute(
            "select 1 from vocabulary_books where id = 'ghost'"
        ).fetchone()
        assert book is None


def test_prepare_job_with_book_id_targets_book_without_switching(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    _import_default_book(client, ["atmosphere"])
    _import_red_book(client)

    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "bookId": RED_BOOK_ID},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["processedWords"] == 2

    # prepare ≠ switch: the current-book pointer still names the default book.
    current = client.get("/api/books/current").json()
    assert current["id"] == DEFAULT_BOOK_ID

    with connect() as connection:
        pointer = connection.execute(
            "select value from user_settings where user_id = ? and key = 'current_book_id'",
            (super_user_id(),),
        ).fetchone()
        assert pointer is None or pointer["value"] == DEFAULT_BOOK_ID

        # Only the red book's words were prepared (count=2 of 4); the
        # default book's words are all still pending.
        statuses_by_book: dict[str, set[str]] = {}
        for row in connection.execute(
            "select book_id, import_status as status from book_words"
        ).fetchall():
            statuses_by_book.setdefault(row["book_id"], set()).add(row["status"])
        assert statuses_by_book[RED_BOOK_ID] == {"ready", "pending"}
        assert statuses_by_book[DEFAULT_BOOK_ID] == {"pending"}


def test_prepare_job_with_unknown_book_id_returns_404(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "bookId": "ghost"},
    )

    assert response.status_code == 404


def test_switch_to_red_book_moves_today_to_it(tmp_path, monkeypatch):
    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))
    client = TestClient(create_app())

    _import_default_book(client, ["atmosphere"])
    _import_red_book(client)
    client.post("/api/prepare-jobs", json={"scope": "next", "count": 4})
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 4, "bookId": RED_BOOK_ID},
    )

    response = client.put("/api/books/current", json={"bookId": RED_BOOK_ID})
    assert response.status_code == 200, response.text

    current = client.get("/api/books/current").json()
    assert current["id"] == RED_BOOK_ID
    assert current["title"] == RED_BOOK_TITLE
    assert current["totalWords"] == 4
