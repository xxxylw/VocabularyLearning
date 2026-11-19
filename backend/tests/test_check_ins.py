"""P1 2026-09-08 打卡热点图服务端化（task 7683154325467565322）.

Coverage:
1. GET /api/check-ins — 从 reviews 按 study_date 聚合派生每日打卡
   （completedCards / newCards / reviewCards / completedAt），跨设备
   共享同一份服务端数据（修复「手机背完、电脑热点图看不到今天」）。
2. 派生覆盖一切完成路径：不经过前端完成回调的 review 也计入（bug
   存活期间完成的会话、API 层面完成）。
3. new vs review 口径：卡的**首次** review 落在当日 → new；之后
   当日出现的卡 → review。
4. POST /api/check-ins/merge — 本地 localStorage 历史一次性上报：
   服务端当天已有 reviews 的日期以派生值为准；没有 reviews 的本地
   独有日期存为 override，按字段取 max、幂等。
5. 用户隔离（real_auth）：A 的打卡/override 不泄露给 B。
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import _migrated_paths, connect
from app.main import create_app


def _clear_migrate_cache() -> None:
    _migrated_paths.clear()


def _import_words(client: TestClient, words: list[str]) -> None:
    csv_lines = ["sequence_index,word"] + [
        f"{index},{word}" for index, word in enumerate(words, start=1)
    ]
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", "\n".join(csv_lines).encode(), "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200, response.text


def _start(client: TestClient, day: date, target: int) -> dict:
    response = client.post(
        "/api/study/today/start",
        json={"date": day.isoformat(), "dailyNewWordTarget": target, "extraNewWords": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _review_card(client: TestClient, card_id: str, day: date, headers: dict | None = None) -> None:
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": "known",
            "reviewedAt": f"{day.isoformat()}T09:00:00+08:00",
            "reviewedDate": day.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _check_ins(client: TestClient, headers: dict | None = None) -> list[dict]:
    response = client.get("/api/check-ins", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["checkIns"]


def _merge(client: TestClient, records: list[dict], headers: dict | None = None) -> dict:
    response = client.post(
        "/api/check-ins/merge", json={"checkIns": records}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _by_date(records: list[dict]) -> dict[str, dict]:
    return {record["date"]: record for record in records}


def test_check_ins_empty_for_fresh_user(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    assert _check_ins(client) == []


def test_check_ins_derived_from_reviews_on_completion_paths(tmp_path, monkeypatch):
    """派生数据覆盖一切完成路径：不经过前端完成回调的 review 也计入。"""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline", "appeal"])
    session = _start(client, today, 3)
    assert session["totalCards"] == 3

    # 只复习前两张（第三张不提交 —— 未完成日）。
    for card in session["cards"][:2]:
        _review_card(client, card["cardId"], today)

    records = _by_date(_check_ins(client))
    today_record = records[today.isoformat()]
    assert today_record["completedCards"] == 2
    assert today_record["newCards"] == 2
    assert today_record["reviewCards"] == 0
    assert today_record["completedAt"].startswith(f"{today.isoformat()}T")


def test_check_ins_classifies_new_vs_review_across_days(tmp_path, monkeypatch):
    """首次 review 落在当日 → new；之后的复习 → review。"""
    day1 = date.today()
    # known 首评后 interval=1 天 → day2 即 due，可合法再评。
    day2 = day1 + timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline"])
    session = _start(client, day1, 2)

    _review_card(client, session["cards"][0]["cardId"], day1)
    _review_card(client, session["cards"][1]["cardId"], day1)

    # 学习日边界 02:00：day2 的复习要归 day2，必须把服务端学习时钟
    # 推进到 day2（归日以服务器时间为准，客户端 reviewedDate 已退出
    # 归日判定）。
    from app import study_clock

    monkeypatch.setattr(
        study_clock,
        "now",
        lambda: datetime.combine(day2, dtime(10, 0), tzinfo=timezone.utc),
    )
    _review_card(client, session["cards"][0]["cardId"], day2)

    records = _by_date(_check_ins(client))
    assert records[day1.isoformat()]["completedCards"] == 2
    assert records[day1.isoformat()]["newCards"] == 2
    assert records[day1.isoformat()]["reviewCards"] == 0
    assert records[day2.isoformat()]["completedCards"] == 1
    assert records[day2.isoformat()]["newCards"] == 0
    assert records[day2.isoformat()]["reviewCards"] == 1


def test_merge_keeps_local_only_dates_as_overrides(tmp_path, monkeypatch):
    """服务端没有 reviews 的日期：本地历史入库为 override。"""
    legacy_day = date.today() - timedelta(days=10)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    merged = _merge(
        client,
        [
            {
                "date": legacy_day.isoformat(),
                "completedCards": 12,
                "newCards": 8,
                "reviewCards": 4,
                "completedAt": f"{legacy_day.isoformat()}T21:00:00.000Z",
            }
        ],
    )
    legacy_record = _by_date(merged["checkIns"])[legacy_day.isoformat()]
    assert legacy_record == {
        "date": legacy_day.isoformat(),
        "completedCards": 12,
        "newCards": 8,
        "reviewCards": 4,
        "completedAt": f"{legacy_day.isoformat()}T21:00:00.000Z",
    }
    # GET 与 merge 返回一致（持久化生效）。
    assert _by_date(_check_ins(client))[legacy_day.isoformat()] == legacy_record


def test_merge_server_derived_dates_win_over_local_records(tmp_path, monkeypatch):
    """服务端当天已有 reviews → 派生值唯一权威，本地记录被忽略。"""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge", "decline"])
    session = _start(client, today, 2)
    for card in session["cards"]:
        _review_card(client, card["cardId"], today)

    merged = _merge(
        client,
        [
            {
                # 本地记录夸大今天的数据 —— 必须被服务端派生值覆盖。
                "date": today.isoformat(),
                "completedCards": 99,
                "newCards": 99,
                "reviewCards": 0,
                "completedAt": f"{today.isoformat()}T23:00:00.000Z",
            }
        ],
    )
    today_record = _by_date(merged["checkIns"])[today.isoformat()]
    assert today_record["completedCards"] == 2
    assert today_record["newCards"] == 2
    assert today_record["reviewCards"] == 0


def test_merge_is_idempotent_and_takes_field_max(tmp_path, monkeypatch):
    """同一日期重复上报按字段取 max，多次上报幂等。"""
    legacy_day = date.today() - timedelta(days=5)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    def local_record(completed: int, new: int, review: int, at: str) -> dict:
        return {
            "date": legacy_day.isoformat(),
            "completedCards": completed,
            "newCards": new,
            "reviewCards": review,
            "completedAt": at,
        }

    first = _merge(
        client, [local_record(5, 3, 2, f"{legacy_day.isoformat()}T10:00:00.000Z")]
    )["checkIns"]
    second = _merge(
        client, [local_record(7, 2, 4, f"{legacy_day.isoformat()}T20:00:00.000Z")]
    )["checkIns"]
    third = _merge(
        client, [local_record(7, 2, 4, f"{legacy_day.isoformat()}T20:00:00.000Z")]
    )["checkIns"]

    expected = {
        "date": legacy_day.isoformat(),
        "completedCards": 7,
        "newCards": 3,
        "reviewCards": 4,
        "completedAt": f"{legacy_day.isoformat()}T20:00:00.000Z",
    }
    assert _by_date(second)[legacy_day.isoformat()] == expected
    assert _by_date(third)[legacy_day.isoformat()] == expected
    assert _by_date(first)[legacy_day.isoformat()]["completedCards"] == 5


def test_merge_rejects_oversized_payload(tmp_path, monkeypatch):
    legacy_day = date.today() - timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    from app.services import MAX_MERGE_RECORDS

    records = [
        {
            "date": (legacy_day - timedelta(days=index)).isoformat(),
            "completedCards": 1,
            "newCards": 1,
            "reviewCards": 0,
            "completedAt": "2026-09-08T10:00:00.000Z",
        }
        for index in range(MAX_MERGE_RECORDS + 1)
    ]
    response = client.post("/api/check-ins/merge", json={"checkIns": records})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 用户隔离（real auth：conftest 的 test-suite-token 旁路在此不生效）。
# ---------------------------------------------------------------------------


@pytest.fixture
def no_email(monkeypatch):
    monkeypatch.setattr(
        "app.emailing.send_verification_email", lambda to, token: None
    )


def _register_and_login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "pass-2026a"}
    )
    assert response.status_code in (200, 201), response.text
    from app import auth as auth_module

    with connect() as connection:
        row = connection.execute(
            "select id from users where email = ?", (email,)
        ).fetchone()
    assert row is not None
    auth_module.mark_user_verified(str(row["id"]))
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "pass-2026a"}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.mark.real_auth
def test_check_ins_and_overrides_are_isolated_between_users(
    tmp_path, monkeypatch, no_email
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "iso.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())
    alice = _register_and_login(client, "alice@example.com")
    bob = _register_and_login(client, "bob@example.com")
    alice_headers = {"Authorization": f"Bearer {alice}"}
    bob_headers = {"Authorization": f"Bearer {bob}"}

    today = date.today()
    legacy_day = today - timedelta(days=7)

    # real_auth 下超管导入不可用（无超管凭据），直接播种共享 book_words
    # （沿用 test_multiuser_isolation 的播种口径：book_id='default-book'）。
    with connect() as connection:
        connection.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
            " values ('source-1', 'csv', 'IELTS', null, null, '2026-01-01T00:00:00+00:00')"
        )
        for index, word in enumerate(("charge", "decline"), start=1):
            connection.execute(
                "insert into book_words (id, book_id, source_id, sequence_index, word_text,"
                " normalized_text, import_status, created_at, updated_at)"
                " values (?, 'default-book', 'source-1', ?, ?, ?, 'pending',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (f"bw-{index}", index, word, word),
            )
        connection.commit()

    # Alice：prepare → start → review（正常学习产生服务端打卡）。
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 2, "overwriteExisting": False},
        headers=alice_headers,
    )
    assert response.status_code == 200, response.text

    response = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 2},
        headers=alice_headers,
    )
    assert response.status_code == 200, response.text
    for card in response.json()["cards"]:
        _review_card(client, card["cardId"], today, headers=alice_headers)

    # Alice：上报一条本地独有历史（服务端无 reviews 的日期）。
    merged = _merge(
        client,
        [
            {
                "date": legacy_day.isoformat(),
                "completedCards": 6,
                "newCards": 6,
                "reviewCards": 0,
                "completedAt": f"{legacy_day.isoformat()}T20:00:00.000Z",
            }
        ],
        headers=alice_headers,
    )
    alice_records = _by_date(merged["checkIns"])
    assert alice_records[today.isoformat()]["completedCards"] == 2
    assert alice_records[legacy_day.isoformat()]["completedCards"] == 6

    # Bob 全空 —— Alice 的派生数据和 override 都不泄露。
    assert _check_ins(client, headers=bob_headers) == []

    # Bob 的 merge 不会覆盖 Alice 的 override（各写各的 user_settings）。
    bob_merged = _merge(
        client,
        [
            {
                "date": legacy_day.isoformat(),
                "completedCards": 1,
                "newCards": 1,
                "reviewCards": 0,
                "completedAt": f"{legacy_day.isoformat()}T01:00:00.000Z",
            }
        ],
        headers=bob_headers,
    )
    assert _by_date(bob_merged["checkIns"])[legacy_day.isoformat()]["completedCards"] == 1

    alice_records = _by_date(_check_ins(client, headers=alice_headers))
    assert alice_records[legacy_day.isoformat()]["completedCards"] == 6
    assert alice_records[today.isoformat()]["completedCards"] == 2



# ---------------------------------------------------------------------------
# 需求 1（task 7707203351260843028，2026-11-12）：首页日期格上的
# 「每日已背」计数口径 —— 只数「当天队列里已清除（Got it 且不再
# pending 于重复池）」的唯一卡：重复池同一张卡多次评分只计 1 次，
# 且永远 ≤ 当天队列卡数（用户原话「每日已背的词汇应该最多是那个
# 要学的卡片的数量，不是翻了多少次卡片」）。
# ---------------------------------------------------------------------------


def _prepare_all(client: TestClient, count: int) -> None:
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": count, "maxSensesPerWord": 5},
    )
    assert response.status_code == 200, response.text


def _rate_card(
    client: TestClient,
    card_id: str,
    day: date,
    rating: str = "known",
    headers: dict | None = None,
) -> None:
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": rating,
            "reviewedAt": f"{day.isoformat()}T09:00:00+08:00",
            "reviewedDate": day.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _review_pool_card(
    client: TestClient, card_id: str, rating: str, headers: dict | None = None
) -> None:
    response = client.post(
        "/api/study/today/repeat-pool/reviews",
        json={"cardId": card_id, "rating": rating},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _mark_due_reviews(words: list[str], due: date) -> None:
    """把指定单词的卡标成「已复习、已到期」—— 到期复习候选池。"""
    with connect() as connection:
        user_id = connection.execute(
            "select id from users where is_super = 1 limit 1"
        ).fetchone()["id"]
        for word in words:
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
                    datetime.now(timezone.utc).isoformat(),
                    due.isoformat(),
                    user_id,
                    word.lower(),
                ),
            )
        connection.commit()


def _card_id_of_word(word: str) -> str:
    with connect() as connection:
        row = connection.execute(
            """
            select cards.id from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text = ?
            """,
            (word.lower(),),
        ).fetchone()
    assert row is not None
    return str(row["id"])


def test_check_ins_counts_repeat_pool_card_once_after_multiple_ratings(
    tmp_path, monkeypatch
):
    """重复池同一张卡多次评分，「每日已背」只 +1：没 Got it 前不算，
    Got it 后算 1，终态后再评分也不累加（不按「翻卡次数」计数）。"""
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    _import_words(client, ["charge"])
    session = _start(client, today, 1)
    card_id = session["cards"][0]["cardId"]

    # Maybe（uncertain）→ 进当日重复池 pending：还没掌握 → 已背 0。
    _rate_card(client, card_id, today, "uncertain")
    records = _by_date(_check_ins(client))
    assert records[today.isoformat()]["completedCards"] == 0

    # 重复副本上评 New（unknown）→ 仍未 Got it，已背仍 0。
    _review_pool_card(client, card_id, "unknown")
    assert _by_date(_check_ins(client))[today.isoformat()]["completedCards"] == 0

    # 重复副本上评 Got it → 池状态 cleared → 已背 +1（只 +1）。
    _review_pool_card(client, card_id, "known")
    records = _by_date(_check_ins(client))
    assert records[today.isoformat()]["completedCards"] == 1

    # 终态后再评分（幂等返回现状）→ 已背不再增加。
    _review_pool_card(client, card_id, "unknown")
    assert _by_date(_check_ins(client))[today.isoformat()]["completedCards"] == 1


def test_check_ins_count_never_exceeds_day_queue_size(tmp_path, monkeypatch):
    """「每日已背」≤ 当天队列卡数：队列外的到期复习（当日队列装不下
    的那批）不计入当天已背 —— 旧口径会把它们一起算进去，导致
    已背数大于当天要学的卡片数。"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    client = TestClient(create_app())

    from app import services

    # 上限压到 2：4 张到期复习 + 1 张配额新词 → 当日队列 = 1 复习 + 1 新词。
    monkeypatch.setattr(services, "DAILY_QUEUE_CAP", 2)

    review_words = ["dueone", "duetwo", "duethree", "duefour"]
    _import_words(client, review_words + ["freshone"])
    _prepare_all(client, 5)
    _mark_due_reviews(review_words, yesterday)

    session = _start(client, today, 1)
    assert session["totalCards"] == 2

    # 队列里的两张都 Got it。
    for card in session["cards"]:
        _rate_card(client, card["cardId"], today, "known")

    # 队列外的到期复习（队列装不下、没来得及复习的那批）→ 归到今天，
    # 但不属于「当天要学的卡片」，不计入当天已背。
    _rate_card(client, _card_id_of_word("duethree"), today, "known")

    records = _by_date(_check_ins(client))
    completed = records[today.isoformat()]["completedCards"]
    assert completed == 2
    assert completed <= session["totalCards"]
