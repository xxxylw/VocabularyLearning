from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app


def test_full_book_export_refuses_until_all_book_words_are_prepared(
    tmp_path, monkeypatch
):
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
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )

    response = client.post(
        "/api/export/anki/full-book",
        json={
            "deckName": "IELTS Vocabulary Book",
            "includeChineseNote": True,
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["preparedWords"] == 1
    assert body["totalWords"] == 2
    assert body["missingWords"] == 1


def test_full_book_export_returns_download_when_all_book_words_are_prepared(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("VOCAB_EXPORT_DIR", str(export_dir))
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
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "maxSensesPerWord": 5},
    )

    response = client.post(
        "/api/export/anki/full-book",
        json={
            "deckName": "IELTS Vocabulary Book",
            "includeChineseNote": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cardCount"] == _count_local_export_cards()
    assert body["downloadUrl"] == "/api/export/anki/files/ielts-vocabulary-book.apkg"

    download_response = client.get(body["downloadUrl"])

    assert download_response.status_code == 200
    assert "application" in download_response.headers["content-type"]
    assert (
        'filename="ielts-vocabulary-book.apkg"'
        in download_response.headers["content-disposition"]
    )
    assert len(download_response.content) > 0

    exported_file = export_dir / "ielts-vocabulary-book.apkg"
    assert exported_file.exists()
    assert exported_file.stat().st_size > 0


def test_full_book_export_counts_duplicate_normalized_words_by_book_row(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())
    for source_name in ("IELTS Book A", "IELTS Book B"):
        client.post(
            "/api/book-words/import",
            files={
                "file": (
                    "book_words.csv",
                    b"sequence_index,word\n1,charge\n",
                    "text/csv",
                )
            },
            data={"sourceName": source_name, "replaceExisting": "false"},
        )
    client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "maxSensesPerWord": 5},
    )

    response = client.post(
        "/api/export/anki/full-book",
        json={
            "deckName": "IELTS Vocabulary Book",
            "includeChineseNote": True,
        },
    )

    assert response.status_code == 200


def test_full_book_export_refuses_empty_book_with_zero_readiness(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    response = client.post(
        "/api/export/anki/full-book",
        json={
            "deckName": "IELTS Vocabulary Book",
            "includeChineseNote": True,
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "totalWords": 0,
        "preparedWords": 0,
        "missingWords": 0,
    }


def _count_local_export_cards() -> int:
    with connect() as connection:
        row = connection.execute(
            """
            select count(distinct cards.id) as total
            from book_words
            join words on words.normalized_text = book_words.normalized_text
            join entries on entries.word_id = words.id
            join cards on cards.entry_id = entries.id
            """
        ).fetchone()
    return row["total"]
