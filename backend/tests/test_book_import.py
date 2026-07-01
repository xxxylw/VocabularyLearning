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
