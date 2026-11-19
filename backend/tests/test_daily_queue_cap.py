"""DAILY_QUEUE_CAP 组成修正（2026-11-12 需求二）：新词保底 + 到期复习取剩余容量。

用户原话「每日单词上限是150个左右，然后还是最后x个是背的新词的
数量，不能因为限制次数了就把新词剔出去，没来得及复习的，可以在
明天下次背诵中安排复习」。口径（2026-11-12 修正）：
- 新词配额保底占据队列尾部：新词数 x = min(今日剩余新词配额, 实际
  可取新词数)，这 x 张新词固定排在队列最后；
- 到期复习取剩余容量：reviews = 到期复习按 due_at 升序取前
  (DAILY_QUEUE_CAP − x) 张；
- 装不下的到期复习 due_at 不动（不丢卡、不重置进度），次日快照自然
  再选中它们 —— 即「没来得及复习的，明天安排复习」；
- 新词不足 x 张时复习按实际可取数补位（以队列实际词数为准，不硬凑）；
- 「再来一组」（extraNewWords）是用户显式加练，不受 CAP 限制。
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.db import connect
from app import services


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client() -> TestClient:
    return TestClient(create_app())


def _import_book(client: TestClient, words: list[str]) -> None:
    csv_lines = "sequence_index,word\n" + "\n".join(
        f"{index + 1},{word}" for index, word in enumerate(words)
    )
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", csv_lines.encode(), "text/csv")},
        data={"sourceName": "Cap Test Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200, response.text


def _prepare_all(client: TestClient, count: int) -> None:
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": count, "maxSensesPerWord": 5},
    )
    assert response.status_code == 200, response.text


def _mark_due_reviews(words: list[str], today: date) -> None:
    """把指定单词的卡标记为「已复习、已到期」：due_at 逐词错开一天
    （列表越靠前的词过期越久），last_reviewed_at 非空使其进入复习池。"""
    with connect() as connection:
        user_id = connection.execute(
            "select id from users where is_super = 1 limit 1"
        ).fetchone()["id"]
        for index, word in enumerate(words):
            due = today - timedelta(days=len(words) - index)
            connection.execute(
                """
                update cards set status = 'learning', interval_days = 1,
                    last_reviewed_at = ?, due_at = ?
                where user_id = ? and entry_id in (
                    select entries.id from entries
                    join words on words.id = entries.word_id
                    where words.normalized_text = ?
                )
                """,
                (
                    _iso_now(),
                    due.isoformat(),
                    user_id,
                    word.lower(),
                ),
            )
        connection.commit()


def _start(client: TestClient, day: date, target: int = 20, extra: int = 0) -> dict:
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


def _queue_rows(day: date) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "select card_id, queue_type, position from today_queue"
            " where study_date = ? order by position",
            (day.isoformat(),),
        ).fetchall()
    return [dict(row) for row in rows]


def _review_known(client: TestClient, card: dict, day: date) -> None:
    for card_id in card["cardIds"]:
        response = client.post(
            f"/api/cards/{card_id}/reviews",
            json={
                "rating": "known",
                "reviewedAt": f"{day.isoformat()}T09:00:00+08:00",
                "reviewedDate": day.isoformat(),
            },
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def cap_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cap.sqlite"))
    monkeypatch.setattr(services, "DAILY_QUEUE_CAP", 5)
    return tmp_path


@pytest.fixture
def real_cap_env(tmp_path, monkeypatch):
    """生产口径的 CAP（150）：用 monkeypatch 固定，避免依赖环境变量。"""
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "realcap.sqlite"))
    monkeypatch.setattr(services, "DAILY_QUEUE_CAP", 150)
    return tmp_path


def test_snapshot_caps_reviews_by_urgency_and_defers_overflow(cap_env):
    """8 张到期复习 + 上限 5 + 无可用新词：当日队列 = 最过期的 5 张
    复习卡（无新词），其余 3 张当日不入队；复习完这 5 张后，次日队列
    恰为顺延的 3 张。"""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    words = [f"capword{i}" for i in range(8)]

    client = _client()
    client.get("/api/health")  # 触发建库 + super 账号
    _import_book(client, words)
    _prepare_all(client, 8)
    _mark_due_reviews(words, today)  # words[0] 过期最久，words[7] 最轻

    session = _start(client, today)
    assert session["totalCards"] == 5
    assert [card["queueType"] for card in session["cards"]] == ["review"] * 5
    # 最过期者优先（due_at 升序）：words[0..4] 入队，words[5..7] 顺延。
    assert [card["word"] for card in session["cards"]] == words[:5]
    rows = _queue_rows(today)
    assert len(rows) == 5
    assert {row["queue_type"] for row in rows} == {"review"}

    # 顺延卡未入队也不丢：due_at 保持原值。
    with connect() as connection:
        deferred_due = connection.execute(
            """
            select words.normalized_text, min(cards.due_at) as due_at
            from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text in ('capword5', 'capword6', 'capword7')
            group by words.normalized_text
            """
        ).fetchall()
    assert len(deferred_due) == 3
    for row in deferred_due:
        assert date.fromisoformat(row["due_at"]) <= today

    # 复习完当日 5 张（known → 间隔 3 天，次日不到期）。
    for card in session["cards"]:
        _review_known(client, card, today)

    # 次日：队列恰为顺延的 3 张复习卡，无新词（全部 8 词都已入复习流）。
    next_session = _start(client, tomorrow)
    assert next_session["totalCards"] == 3
    assert sorted(card["word"] for card in next_session["cards"]) == sorted(
        words[5:]
    )
    assert [card["queueType"] for card in next_session["cards"]] == [
        "review"
    ] * 3


def test_new_word_quota_is_reserved_before_reviews(cap_env):
    """新词保底（2026-11-12 需求二）：配额新词固定占队列尾部，到期
    复习只取剩余容量 —— 上限 5、配额 2 时 = 3 张复习 + 2 张新词，
    新词在尾部，复习按 due_at 升序取最过期的 3 张。"""
    today = date.today()
    review_words = [f"floorrev{i}" for i in range(8)]
    new_words = [f"floornew{i}" for i in range(4)]

    client = _client()
    client.get("/api/health")
    _import_book(client, review_words + new_words)
    _prepare_all(client, 12)
    _mark_due_reviews(review_words, today)  # review_words[0] 过期最久

    session = _start(client, today, target=2)
    assert session["totalCards"] == 5
    types = [card["queueType"] for card in session["cards"]]
    # 复习在前（最过期的 3 张）、新词在尾部 2 张。
    assert types == ["review", "review", "review", "new", "new"]
    assert [card["word"] for card in session["cards"][:3]] == review_words[:3]
    assert {card["word"] for card in session["cards"][3:]} <= set(new_words)

    rows = _queue_rows(today)
    assert [row["queue_type"] for row in rows] == ["review"] * 3 + ["new"] * 2


def test_new_word_quota_not_squeezed_out_by_reviews(real_cap_env):
    """生产口径（CAP 150）：200 张到期复习 + 今日新词配额 20 → 队列 =
    130 张复习 + 20 张新词，新词固定在队列尾部；没来得及复习的 70 张
    due_at 不动，次日自然再选。"""
    today = date.today()
    review_words = [f"realrev{i}" for i in range(200)]
    new_words = [f"realnew{i}" for i in range(20)]

    client = _client()
    client.get("/api/health")
    _import_book(client, review_words + new_words)
    _prepare_all(client, 220)
    _mark_due_reviews(review_words, today)

    session = _start(client, today, target=20)
    assert session["totalCards"] == 150
    types = [card["queueType"] for card in session["cards"]]
    assert types.count("review") == 130
    assert types.count("new") == 20
    # 新词在队列尾部（最后 20 张）。
    assert types[-20:] == ["new"] * 20
    # 复习按 due_at 升序：最过期的 130 张入队。
    assert [card["word"] for card in session["cards"][:130]] == review_words[:130]

    rows = _queue_rows(today)
    assert len(rows) == 150
    assert [row["queue_type"] for row in rows] == ["review"] * 130 + ["new"] * 20

    # 被挤出队列的 70 张复习 due_at 不动（顺延到明天），不丢卡。
    with connect() as connection:
        deferred = connection.execute(
            """
            select count(*) as total from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text in ({}) and cards.due_at <= ?
            """.format(",".join("?" for _ in review_words[130:])),
            (*review_words[130:], today.isoformat()),
        ).fetchone()["total"]
    assert deferred == 70


def test_reviews_take_full_cap_when_no_new_words_available(cap_env):
    """新词数 x = 0 时：到期复习取满上限（上限 5 → 5 张复习）。"""
    today = date.today()
    review_words = [f"onlyrev{i}" for i in range(8)]

    client = _client()
    client.get("/api/health")
    _import_book(client, review_words)
    _prepare_all(client, 8)
    _mark_due_reviews(review_words, today)

    session = _start(client, today, target=20)
    assert session["totalCards"] == 5
    assert [card["queueType"] for card in session["cards"]] == ["review"] * 5
    assert [card["word"] for card in session["cards"]] == review_words[:5]


def test_reviews_take_full_cap_when_quota_already_consumed(cap_env):
    """今日新词配额已用尽（x = 0）时：到期复习取满上限。"""
    today = date.today()
    review_words = [f"usedrev{i}" for i in range(8)]
    new_words = [f"usednew{i}" for i in range(4)]

    client = _client()
    client.get("/api/health")
    _import_book(client, review_words + new_words)
    _prepare_all(client, 12)
    _mark_due_reviews(review_words, today)

    # 先把今日 2 张新词配额用掉（新词保底 + 到期复习取剩余容量：
    # 上限 5、配额 2 → 3 张复习 + 2 张新词）。
    first = _start(client, today, target=2)
    assert first["totalCards"] == 5
    assert [card["queueType"] for card in first["cards"]] == [
        "review",
        "review",
        "review",
        "new",
        "new",
    ]
    for card in first["cards"]:
        _review_known(client, card, today)

    # 同日再 start：配额已用尽 → x = 0 → 复习取满上限 5。
    with connect() as connection:
        connection.execute(
            "delete from today_queue where study_date = ?", (today.isoformat(),)
        )
        connection.execute(
            "delete from today_queue_snapshots where study_date = ?",
            (today.isoformat(),),
        )
        connection.commit()
    second = _start(client, today, target=2)
    assert second["totalCards"] == 5
    assert [card["queueType"] for card in second["cards"]] == ["review"] * 5


def test_merge_quota_respects_cap(cap_env):
    """快照后提高每日新词目标：merge 只补到上限空位，不越顶。"""
    today = date.today()
    words = [f"mergeword{i}" for i in range(8)]

    client = _client()
    client.get("/api/health")
    _import_book(client, words)
    _prepare_all(client, 8)

    first = _start(client, today, target=3)
    assert first["totalCards"] == 3

    # 目标从 3 提到 10：配额想补 7 张，但上限只剩 2 个空位。
    second = _start(client, today, target=10)
    assert second["totalCards"] == 5
    assert len(_queue_rows(today)) == 5


def test_extra_new_words_group_is_exempt_from_cap(cap_env):
    """「再来一组」是用户显式加练，不受 CAP 限制：到顶后仍可追加。"""
    today = date.today()
    words = [f"extraword{i}" for i in range(9)]

    client = _client()
    client.get("/api/health")
    _import_book(client, words)
    _prepare_all(client, 9)

    # 配额 5 = 上限 5：当日队列被配额新词占满（5 张）。
    first = _start(client, today, target=5)
    assert first["totalCards"] == 5
    assert {row["queue_type"] for row in _queue_rows(today)} == {"new"}

    # 显式加练 2 张：不受 CAP 限制，队列变成 7 张。
    second = _start(client, today, target=5, extra=2)
    assert second["totalCards"] == 7
    assert len(_queue_rows(today)) == 7
