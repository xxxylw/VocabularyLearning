from fastapi.testclient import TestClient

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
    assert body["cardCount"] >= 2
    assert body["downloadUrl"].endswith(".apkg")
