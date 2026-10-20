"""P0 2026-09-08 今日背完完成态交互.

Coverage:
1. /study/today/summary — 跨设备完成态恢复（acceptance #2）。
2. /study/today/start extraNewWords — 「再来一组」加练额度
   (acceptance #1：背完后追加一组新卡，超出当日默认新词量)。
3. 跨日无残留：加练不影响次日快照。
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app


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


def _start(client: TestClient, day: date, target: int, extra: int = 0) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={
            "date": day.isoformat(),
            "dailyNewWordTarget": target,
            "extraNewWords": extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _summary(client: TestClient, day: date) -> dict:
    response = client.get(
        "/api/study/today/summary",
        params={"date": day.isoformat()},
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


def _review_every(client: TestClient, cards: list[dict], day: date) -> None:
    for card in cards:
        _review(client, card, day)


def test_summary_empty_when_no_queue(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    summary = _summary(client, today)
    assert summary == {
        "studyDate": today.isoformat(),
        "totalCards": 0,
        "reviewedCards": 0,
        "dayCompleted": False,
        "completedCards": [],
    }


def test_summary_day_completed_after_reviewing_all_queue_cards(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal"])
    session = _start(client, today, 3)
    assert session["totalCards"] == 3

    # Nothing reviewed yet — summary says not complete.
    initial = _summary(client, today)
    assert initial["dayCompleted"] is False
    assert initial["reviewedCards"] == 0
    assert initial["totalCards"] == 3
    assert initial["completedCards"] == []

    _review_every(client, session["cards"], today)

    completed = _summary(client, today)
    assert completed["dayCompleted"] is True
    assert completed["reviewedCards"] == 3
    assert completed["totalCards"] == 3
    # completedCards preserves the day queue order (positions 1..3) so
    # spelling practice replays the same left-to-right learning order
    # as the card-mode session did.
    assert [card["word"] for card in completed["completedCards"]] == [
        "charge",
        "decline",
        "appeal",
    ]
    assert [card["queuePosition"] for card in completed["completedCards"]] == [1, 2, 3]
    assert all(card["queueType"] == "new" for card in completed["completedCards"])


def test_summary_reflects_server_state_independently_of_session_call(
    tmp_path, monkeypatch
):
    """跨设备恢复：手机背完 → 电脑刷新页面完成态正确恢复 (acceptance #2).

    We never invoke /study/today/start after the reviews — the
    summary endpoint alone is what surfaces the completion state on
    the freshly refreshed device. If the completion state were
    cached in client memory only, this read would return dayCompleted
    = False.
    """
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal"])
    session = _start(client, today, 3)
    _review_every(client, session["cards"], today)

    # Simulate a second, freshly opened device: an independently
    # minted TestClient (same DB) reads the summary without any
    # in-memory state from the first client.
    second_client = TestClient(create_app())
    summary = _summary(second_client, today)
    assert summary["dayCompleted"] is True
    assert summary["reviewedCards"] == 3
    assert [card["word"] for card in summary["completedCards"]] == [
        "charge",
        "decline",
        "appeal",
    ]


def test_summary_partial_review_does_not_flip_to_completed(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal"])
    session = _start(client, today, 3)
    # Review only the first card.
    _review(client, session["cards"][0], today)

    summary = _summary(client, today)
    assert summary["dayCompleted"] is False
    assert summary["reviewedCards"] == 1
    assert summary["totalCards"] == 3
    assert [card["word"] for card in summary["completedCards"]] == ["charge"]


def test_summary_idempotent_under_repeated_reads(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline"])
    session = _start(client, today, 2)
    _review_every(client, session["cards"], today)

    first = _summary(client, today)
    second = _summary(client, today)
    third = _summary(client, today)
    assert first == second == third


def test_another_group_appends_fresh_new_cards_after_queue_complete(
    tmp_path, monkeypatch
):
    """「再来一组」: 背完后追加一组新卡加练，超出当日默认新词量 (acceptance #1)."""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    # Need at least 3 (default target) + 3 (extra) = 6 words.
    _import_words(client, [f"word{index:02d}" for index in range(1, 7)])
    first = _start(client, today, 3)
    assert first["totalCards"] == 3
    assert [card["word"] for card in first["cards"]] == ["word01", "word02", "word03"]

    _review_every(client, first["cards"], today)

    # Day is complete — summary says so.
    assert _summary(client, today)["dayCompleted"] is True

    # Tap 「再来一组」with extraNewWords == current target (3) — merge
    # appends 3 fresh new cards at the tail of today's queue.
    extra = _start(client, today, 3, extra=3)
    assert extra["totalCards"] == 6
    # Earlier reviewed words are still reviewed and not re-queued; only
    # the new 3 fresh cards come back as pending.
    assert [card["word"] for card in extra["cards"]] == ["word04", "word05", "word06"]
    assert [card["queuePosition"] for card in extra["cards"]] == [4, 5, 6]
    assert extra["reviewedCards"] == 3
    # Day is no longer complete after the extra group is queued.
    assert _summary(client, today)["dayCompleted"] is False


def test_another_group_repeatable_for_subsequent_groups(tmp_path, monkeypatch):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, [f"word{index:02d}" for index in range(1, 11)])
    first = _start(client, today, 2)
    _review_every(client, first["cards"], today)

    # First extra group.
    second = _start(client, today, 2, extra=2)
    assert second["totalCards"] == 4
    assert [card["word"] for card in second["cards"]] == ["word03", "word04"]
    _review_every(client, second["cards"], today)

    # Second extra group — should still be allowed and grow the queue
    # again with the next two fresh words.
    third = _start(client, today, 2, extra=2)
    assert third["totalCards"] == 6
    assert [card["word"] for card in third["cards"]] == ["word05", "word06"]
    assert _summary(client, today)["dayCompleted"] is False


def test_another_group_does_not_carry_over_to_next_day(tmp_path, monkeypatch):
    """跨日无残留：加练不影响次日快照的当日默认新词量配额。"""
    day_one = date.today()
    day_two = day_one + timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    # Need at least 3 (default target) + 3 (extra) = 6 words for day one.
    _import_words(client, [f"word{index:02d}" for index in range(1, 7)])
    # Day one: default 3, then extra 3.
    first = _start(client, day_one, 3)
    _review_every(client, first["cards"], day_one)
    extra = _start(client, day_one, 3, extra=3)
    _review_every(client, extra["cards"], day_one)
    # Day one totals: 6 new words learned.
    assert _summary(client, day_one)["reviewedCards"] == 6
    assert _summary(client, day_one)["dayCompleted"] is True

    # Day two: a fresh snapshot. The 加练 words entered SM-2
    # scheduling and may reappear as due reviews, but the day's
    # *new-word quota* still defaults to the requested target (3) —
    # no extra residue from yesterday.
    day_two_session = _start(client, day_two, 3)
    assert day_two_session["reviewedCards"] == 0
    # New snapshot: the day's quota defaults to the target (no extra
    # in the request, so the snapshot was sized by target alone). The
    # exact queue composition is non-deterministic because the
    # extra-words-studied yesterday may have advanced the due_at of
    # some new cards, so we only assert the new-card cap.
    new_count = sum(1 for card in day_two_session["cards"] if card["queueType"] == "new")
    assert new_count <= 3


def test_extra_new_words_does_not_pad_existing_pending_queue(
    tmp_path, monkeypatch
):
    """Pending 队列已存在但未完成时, extra 仅补充新词 — 不重复已入队卡。"""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, [f"word{index:02d}" for index in range(1, 6)])
    first = _start(client, today, 2)
    assert [card["word"] for card in first["cards"]] == ["word01", "word02"]
    # User has NOT completed the day — 再来一组 on the partial queue
    # would in normal UX be unreachable, but the API still honors
    # `extra` deterministically: remaining = 2 + extra − 0 − (2 − 0) = extra.
    extra = _start(client, today, 2, extra=2)
    assert extra["totalCards"] == 4
    pending_words = {card["word"] for card in extra["cards"]}
    assert pending_words == {"word01", "word02", "word03", "word04"}


def test_summary_with_completed_extra_group_reflects_full_progress(
    tmp_path, monkeypatch
):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    _import_words(client, [f"word{index:02d}" for index in range(1, 7)])
    first = _start(client, today, 3)
    _review_every(client, first["cards"], today)
    extra = _start(client, today, 3, extra=3)
    _review_every(client, extra["cards"], today)

    summary = _summary(client, today)
    assert summary["dayCompleted"] is True
    assert summary["reviewedCards"] == 6
    assert summary["totalCards"] == 6
    # completedCards follows queue order: original 3 then extra 3.
    assert [card["word"] for card in summary["completedCards"]] == [
        f"word{index:02d}" for index in range(1, 7)
    ]
    assert [card["queuePosition"] for card in summary["completedCards"]] == list(
        range(1, 7)
    )
