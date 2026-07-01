from fastapi.testclient import TestClient

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

    progress = client.get("/api/book-words/progress")
    assert progress.status_code == 200
    assert progress.json()["nextSequenceIndex"] is None
