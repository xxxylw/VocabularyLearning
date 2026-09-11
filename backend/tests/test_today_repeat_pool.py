"""当日重复池（规格：当日不会的单词在今日后续卡片中重复，Got it
一次才移除；PM 定稿 recvuPXKUX970L）.

覆盖规格第五章验收标准的后端部分：
1. 当日队列内新卡评 New 后间隔 3 张重新出现（不足 3 张时队尾）；
   Got it 后当日不再出现。
2. SM-2 零污染：重复卡操作不写 reviews、不改 EF / 间隔 / due_at；
   重复 Got it 幂等、不改次日调度。
3. 次日到期：池内 Got it 过的卡次日按 New 评分出现在新学习日
   到期复习集合。
4. 重复上限：第 3 次重复仍非 Got it 自动移出、进度计入完成。
5. 完成态：原队列评完 + 池清空才 dayCompleted；池非空不触发。
6. 进度条：评 New 未清空分子停滞不回退、分母不因重复增大；
   清空后 +1；全部完成 N/N。
7. 断点续传：重进（幂等 start）保留池、重复卡继续出现、
   已评卡（池内除外）不出现。
8. 02:00 联动：跨 02:00 池状态归旧学习日照常更新；02:00 后
   重进旧池作废、未 Got it 卡出现在新学习日到期集合。
9. D1 边界：Maybe 不进池；复习卡评 New 不进池；队列外评分不进池
   且不推进池计数。
10. 409 隔离：同卡同日重复 review 仍 409、池状态不受影响；池端点
    对非池卡 404；repeatCount 数据面可查。

多义项（规格边界态：以卡为粒度）：同词多义项各自进池、同一次
展示的兄弟义项评分不推进彼此计数、会话读取按词去重只注入一张
重复卡。
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import study_clock
from app.db import _migrated_paths, connect
from app.main import create_app

_BEIJING = timezone(timedelta(hours=8))


def _clear_migrate_cache() -> None:
    _migrated_paths.clear()


def _pin(monkeypatch, natural_day: date, hh: int, mm: int = 0, ss: int = 0) -> None:
    moment = datetime.combine(
        natural_day, dtime(hh, mm, ss), tzinfo=_BEIJING
    ).astimezone(timezone.utc)
    monkeypatch.setattr(study_clock, "now", lambda: moment)


def _setup(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _clear_migrate_cache()
    return TestClient(create_app())


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


def _prepare(client: TestClient, count: int) -> None:
    response = client.post(
        "/api/prepare-jobs",
        json={
            "scope": "next",
            "count": count,
            "maxSensesPerWord": 5,
            "overwriteExisting": False,
        },
    )
    assert response.status_code == 200, response.text


def _backdate_unreviewed_cards(day: date) -> None:
    with connect() as conn:
        conn.execute(
            "update cards set created_on = ?, due_at = ? where last_reviewed_at is null",
            (day.isoformat(), day.isoformat()),
        )
        conn.commit()


def _start(client: TestClient, day: date | None = None, target: int = 20) -> dict:
    payload = {"dailyNewWordTarget": target, "extraNewWords": 0}
    if day is not None:
        payload["date"] = day.isoformat()
    response = client.post("/api/study/today/start", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _review(
    client: TestClient,
    card_id: str,
    reviewed_at: datetime,
    rating: str = "known",
) -> int:
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": rating,
            "reviewedAt": reviewed_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )
    return response.status_code


def _pool_review(client: TestClient, card_id: str, rating: str) -> tuple[int, dict]:
    response = client.post(
        "/api/study/today/repeat-pool/reviews",
        json={"cardId": card_id, "rating": rating},
    )
    if response.status_code == 200:
        return 200, response.json()
    return response.status_code, {}


def _summary(client: TestClient) -> dict:
    response = client.get("/api/study/today/summary")
    assert response.status_code == 200, response.text
    return response.json()


def _pool_row(card_id: str):
    with connect() as conn:
        return conn.execute(
            "select * from today_repeat_pool where card_id = ? order by created_at desc",
            (card_id,),
        ).fetchone()


def _review_count(card_id: str, study_date: str) -> int:
    with connect() as conn:
        return conn.execute(
            "select count(*) as c from reviews where card_id = ? and study_date = ?",
            (card_id, study_date),
        ).fetchone()["c"]


def _card_row(card_id: str):
    with connect() as conn:
        return conn.execute(
            "select * from cards where id = ?", (card_id,)
        ).fetchone()


def _add_second_sense(card_id: str, user_id: str, *, study_day: date) -> str:
    """给 card_id 所在词追加第二个义项 + 新卡（模拟 Oxford 多义项），
    返回新卡 id。study_day 显式回填 due_at / created_on，避免依赖
    SQLite date('now') 的真实日期（与钉死时钟解耦）。"""
    with connect() as conn:
        base = conn.execute(
            """
            select entries.word_id from cards
            join entries on entries.id = cards.entry_id
            where cards.id = ?
            """,
            (card_id,),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        day = study_day.isoformat()
        entry_id = str(uuid4())
        sibling_id = str(uuid4())
        conn.execute(
            """
            insert into entries (
                id, word_id, sense_order, part_of_speech, sense_label,
                definition, definition_source, created_at, updated_at
            ) values (?, ?, 2, 'noun', 'second sense',
                      'second sense definition', 'manual', ?, ?)
            """,
            (entry_id, base["word_id"], now, now),
        )
        conn.execute(
            """
            insert into cards (
                id, user_id, entry_id, status, stage, due_at, created_on,
                last_reviewed_at, ef, interval_days
            ) values (?, ?, ?, 'new', 0, ?, ?, null, 2.5, 0)
            """,
            (sibling_id, user_id, entry_id, day, day),
        )
        conn.commit()
        return sibling_id


def _words_in(session: dict) -> list[str]:
    return [card["word"] for card in session["cards"]]


def _repeat_words_in(session: dict) -> list[str]:
    return [card["word"] for card in session["cards"] if card["isRepeat"]]


# ---------------------------------------------------------------------------
# 验收 1：间隔 3 张重新出现；Got it 一次移除、当日不再出现
# ---------------------------------------------------------------------------


def test_unknown_enters_pool_reappears_after_3_cards(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])
    _prepare(client, 6)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=6)
    assert _words_in(session) == [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
    ]

    # alpha（当日 new 卡）评 New → 进池。
    alpha = session["cards"][0]
    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, rating="unknown") == 200

    # 幂等重进（断点续传同款读取路径）：alpha 隔 3 张重新出现。
    session = _start(client, target=6)
    assert session["totalCards"] == 6
    assert _repeat_words_in(session) == ["alpha"]
    words = _words_in(session)
    # 前面恰 3 张待学卡（bravo / charlie / delta），间隔 3 张。
    assert words == ["bravo", "charlie", "delta", "alpha", "echo", "foxtrot"]
    repeat_card = session["cards"][3]
    assert repeat_card["isRepeat"] is True
    assert repeat_card["queueType"] == "new"

    # Got it 一次才移除：池端点 known → cleared，当日不再出现。
    status, body = _pool_review(client, alpha["cardId"], "known")
    assert status == 200 and body["status"] == "cleared"
    session = _start(client, target=6)
    assert _repeat_words_in(session) == []
    assert "alpha" not in _words_in(session)


def test_reinsert_at_tail_when_fewer_than_3_left(tmp_path, monkeypatch):
    """剩余未学卡不足 3 张时插队尾（规格规则 2 边界态）。"""
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=2)

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, session["cards"][0]["cardId"], at, "unknown") == 200

    session = _start(client, target=2)
    # 只剩 1 张待学卡（不足 3）→ alpha 出现在队尾。
    assert _words_in(session) == ["bravo", "alpha"]
    assert _repeat_words_in(session) == ["alpha"]


# ---------------------------------------------------------------------------
# 验收 2：SM-2 零污染；重复 Got it 幂等
# ---------------------------------------------------------------------------


def test_sm2_zero_pollution_and_idempotent_got_it(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta"])
    _prepare(client, 4)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=4)
    alpha, bravo = session["cards"][0], session["cards"][1]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    # 两张卡都评 New（各入池）；alpha 走完整重复流程，bravo 只评一次。
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    assert _review(client, bravo["cardId"], at, "unknown") == 200

    # 重复卡上的 Maybe / New / Got it 一整套池操作。
    _pool_review(client, alpha["cardId"], "uncertain")
    _pool_review(client, alpha["cardId"], "unknown")
    _pool_review(client, alpha["cardId"], "known")
    # 重复 Got it（幂等）与对已移出卡的再次操作。
    _pool_review(client, alpha["cardId"], "known")
    _pool_review(client, alpha["cardId"], "known")

    day_iso = day.isoformat()
    assert _review_count(alpha["cardId"], day_iso) == 1
    assert _review_count(bravo["cardId"], day_iso) == 1
    # EF / 间隔 / due_at 与仅评一次 New 的对照卡完全一致。
    a, b = _card_row(alpha["cardId"]), _card_row(bravo["cardId"])
    assert (a["ef"], a["interval_days"], a["due_at"]) == (
        b["ef"], b["interval_days"], b["due_at"],
    )

    # 重复 Got it 不改次日调度：due_at 已被锁定，不随池操作漂移。
    row = _pool_row(alpha["cardId"])
    assert row["status"] == "cleared"
    assert _card_row(alpha["cardId"])["due_at"] == a["due_at"]


# ---------------------------------------------------------------------------
# 验收 3：次日按 New 评分到期，出现在新学习日到期复习集合
# ---------------------------------------------------------------------------


def test_pooled_card_appears_next_day_as_review_due(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie"])
    _prepare(client, 3)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=3)
    alpha = session["cards"][0]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    # 池内 Got it（当日记住）—— 次日仍按 New 评分到期重学。
    status, _ = _pool_review(client, alpha["cardId"], "known")
    assert status == 200

    next_day = day + timedelta(days=1)
    _pin(monkeypatch, next_day, 10, 0)
    session = _start(client, target=3)
    alpha_next = [c for c in session["cards"] if c["word"] == "alpha"]
    assert len(alpha_next) == 1
    # 旧池作废：以复习卡身份出现在新学习日到期集合，不再是重复卡。
    assert alpha_next[0]["queueType"] == "review"
    assert alpha_next[0]["isRepeat"] is False


# ---------------------------------------------------------------------------
# 验收 4 + 5：上限 3 次移出、计入完成；池清空才 dayCompleted
# ---------------------------------------------------------------------------


def test_cap_three_repeats_removes_and_counts_progress(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=2)
    alpha = session["cards"][0]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200

    # 第 1 / 2 / 3 次重复仍评 Maybe / New → repeatCount 递增，第 3 次
    # 达上限自动移出（capped）。
    status, body = _pool_review(client, alpha["cardId"], "uncertain")
    assert (status, body["status"], body["repeatCount"]) == (200, "pending", 1)
    status, body = _pool_review(client, alpha["cardId"], "unknown")
    assert (status, body["status"], body["repeatCount"]) == (200, "pending", 2)
    status, body = _pool_review(client, alpha["cardId"], "unknown")
    assert (status, body["status"], body["repeatCount"]) == (200, "capped", 3)

    # 达上限后当日不再出现。
    session = _start(client, target=2)
    assert "alpha" not in _words_in(session)
    assert _repeat_words_in(session) == []


def test_day_completed_requires_pool_empty(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta"])
    _prepare(client, 4)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=4)
    cards = {c["word"]: c for c in session["cards"]}

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, cards["alpha"]["cardId"], at, "unknown") == 200
    for word in ["bravo", "charlie", "delta"]:
        assert _review(client, cards[word]["cardId"], at, "known") == 200

    # 原队列全部已评，但重复池非空 → 不触发完成态。
    summary = _summary(client)
    assert summary["totalCards"] == 4
    assert summary["reviewedCards"] == 3
    assert summary["dayCompleted"] is False

    # 池清空（Got it）→ 完成态触发。
    status, _ = _pool_review(client, cards["alpha"]["cardId"], "known")
    assert status == 200
    summary = _summary(client)
    assert summary["reviewedCards"] == 4
    assert summary["dayCompleted"] is True


# ---------------------------------------------------------------------------
# 验收 6：进度条停滞不回退、分母不变
# ---------------------------------------------------------------------------


def test_progress_stalls_until_pool_card_cleared(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta"])
    _prepare(client, 4)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=4)
    cards = {c["word"]: c for c in session["cards"]}

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    # alpha 评 New 入池：分子停在 0（停滞、不回退）。
    assert _review(client, cards["alpha"]["cardId"], at, "unknown") == 200
    assert _summary(client)["reviewedCards"] == 0
    # 再评 bravo：分子 +1，alpha 仍不计入；分母不因重复增大。
    assert _review(client, cards["bravo"]["cardId"], at, "known") == 200
    summary = _summary(client)
    assert summary["reviewedCards"] == 1
    assert summary["totalCards"] == 4
    # alpha Got it 后分子 +1。
    _pool_review(client, cards["alpha"]["cardId"], "known")
    assert _summary(client)["reviewedCards"] == 2
    # 剩余两张评完 → N/N。
    for word in ["charlie", "delta"]:
        assert _review(client, cards[word]["cardId"], at, "known") == 200
    summary = _summary(client)
    assert (summary["reviewedCards"], summary["totalCards"]) == (4, 4)
    assert summary["dayCompleted"] is True


# ---------------------------------------------------------------------------
# 验收 7：断点续传保留池；重进后已评卡（池内除外）不出现
# ---------------------------------------------------------------------------


def test_resume_preserves_pool_across_reentry(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta", "echo"])
    _prepare(client, 5)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=5)
    alpha = session["cards"][0]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    # 重进（刷新 / exit / 换设备同款幂等 start）：池保留、alpha 继续出现。
    session = _start(client, target=5)
    assert _words_in(session) == [
        "bravo", "charlie", "delta", "alpha", "echo",
    ]
    # 再评一张已评卡外的：bravo known 后重进，bravo 不再出现（池外）。
    assert _review(client, session["cards"][0]["cardId"], at, "known") == 200
    session = _start(client, target=5)
    assert "bravo" not in _words_in(session)
    assert _repeat_words_in(session) == ["alpha"]
    # alpha 距上次展示已隔 bravo（1 张），重进后再隔 charlie / delta
    # 共 3 张 —— 间隔 3 张从评分时刻的展示张数起算。
    assert _words_in(session) == ["charlie", "delta", "alpha", "echo"]


# ---------------------------------------------------------------------------
# 验收 8：02:00 学习日边界联动
# ---------------------------------------------------------------------------


def test_cross_0200_pool_attaches_to_old_day_then_voided(tmp_path, monkeypatch):
    natural_day = date(2026, 9, 11)
    old_study_day = natural_day - timedelta(days=1)  # 01:50 前的学习日
    _pin(monkeypatch, natural_day, 1, 50)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie"])
    _prepare(client, 3)
    _backdate_unreviewed_cards(old_study_day)
    session = _start(client, target=3)
    alpha = session["cards"][0]

    at = datetime.combine(natural_day, dtime(1, 51), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    assert _pool_row(alpha["cardId"])["study_date"] == old_study_day.isoformat()

    # 跨 02:00 的进行中会话不中断：池端点照常更新（状态归旧学习日）。
    _pin(monkeypatch, natural_day, 2, 10)
    status, body = _pool_review(client, alpha["cardId"], "known")
    assert status == 200 and body["status"] == "cleared"
    row = _pool_row(alpha["cardId"])
    assert row["status"] == "cleared"
    assert row["study_date"] == old_study_day.isoformat()

    # 02:00 后重进：生成新学习日队列，旧池作废（不注入新会话）。
    session = _start(client, target=3)
    assert _repeat_words_in(session) == []
    # alpha 当日已评 New（未知 due 次日）→ 出现在新学习日到期集合。
    alpha_next = [c for c in session["cards"] if c["word"] == "alpha"]
    assert len(alpha_next) == 1
    assert alpha_next[0]["queueType"] == "review"


def test_cross_0200_pending_pool_card_returns_next_day(tmp_path, monkeypatch):
    """02:00 后重进：池内未 Got it 的卡按 New 评分出现在新学习日
    到期集合（旧池随旧快照作废）。"""
    natural_day = date(2026, 9, 11)
    old_study_day = natural_day - timedelta(days=1)
    _pin(monkeypatch, natural_day, 1, 50)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(old_study_day)
    session = _start(client, target=2)
    alpha = session["cards"][0]

    at = datetime.combine(natural_day, dtime(1, 51), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    # 池内操作一次（仍 pending），跨 02:00 不中断。
    _pin(monkeypatch, natural_day, 2, 5)
    status, body = _pool_review(client, alpha["cardId"], "uncertain")
    assert (status, body["status"]) == (200, "pending")

    _pin(monkeypatch, natural_day, 2, 30)
    session = _start(client, target=2)
    # 旧池作废：不作为 isRepeat 卡注入新会话。
    assert _repeat_words_in(session) == []
    alpha_next = [c for c in session["cards"] if c["word"] == "alpha"]
    assert len(alpha_next) == 1
    assert alpha_next[0]["queueType"] == "review"
    assert alpha_next[0]["isRepeat"] is False


# ---------------------------------------------------------------------------
# D1 边界：Maybe 不进池；复习卡评 New 不进池；队列外评分不进池不推进
# ---------------------------------------------------------------------------


def test_maybe_does_not_enter_pool(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=2)

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, session["cards"][0]["cardId"], at, "uncertain") == 200

    # Maybe 不进池：当日不再出现、池表无行。
    session = _start(client, target=2)
    assert _words_in(session) == ["bravo"]
    assert _pool_row(session["cards"][0]["cardId"]) is None


def test_review_card_rated_new_does_not_enter_pool(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=2)
    alpha = session["cards"][0]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    # 当日评 known → 次日成为复习卡。
    assert _review(client, alpha["cardId"], at, "known") == 200
    next_day = day + timedelta(days=1)
    _pin(monkeypatch, next_day, 10, 0)
    session = _start(client, target=2)
    alpha_next = [c for c in session["cards"] if c["word"] == "alpha"]
    assert len(alpha_next) == 1 and alpha_next[0]["queueType"] == "review"

    # 复习卡评 New（D1）：不进池。
    at = datetime.combine(next_day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha_next[0]["cardId"], at, "unknown") == 200
    assert _pool_row(alpha["cardId"]) is None
    session = _start(client, target=2)
    assert "alpha" not in _words_in(session)


def test_out_of_queue_review_neither_enters_nor_advances(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])
    _prepare(client, 6)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=4)  # 队列只含前 4 张
    queued = {c["cardId"] for c in session["cards"]}
    all_cards = []
    with connect() as conn:
        for row in conn.execute(
            """
            select cards.id as id, words.normalized_text as word
            from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where cards.user_id = (select user_id from today_queue limit 1)
              and cards.last_reviewed_at is null
            order by words.normalized_text
            """
        ):
            all_cards.append((row["id"], row["word"]))
    outside = [(cid, w) for cid, w in all_cards if cid not in queued]
    assert len(outside) == 2
    in_pool = [(cid, w) for cid, w in all_cards if cid in queued]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    # 队列内 alpha 评 New 入池。
    alpha_id = in_pool[0][0]
    assert _pool_row(alpha_id) is None
    assert _review(client, alpha_id, at, "unknown") == 200
    assert _pool_row(alpha_id)["defer_remaining"] == 3

    # 队列外卡（第 5/6 张，自由复习）评 New：不进池。
    outside_id, _ = outside[0]
    assert _review(client, outside_id, at, "unknown") == 200
    assert _pool_row(outside_id) is None
    # 且不推进队列内池卡的间隔计数。
    assert _pool_row(alpha_id)["defer_remaining"] == 3


# ---------------------------------------------------------------------------
# 验收 10 / 409 隔离：review 的 409 路径不受池影响；池端点 404
# ---------------------------------------------------------------------------


def test_review_409_path_and_pool_endpoint_404(tmp_path, monkeypatch):
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo"])
    _prepare(client, 2)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=2)
    alpha, bravo = session["cards"][0], session["cards"][1]

    at = datetime.combine(day, dtime(10, 1), tzinfo=_BEIJING)
    assert _review(client, alpha["cardId"], at, "unknown") == 200

    # 同卡同日重复 review 仍 409（既有硬约束），池状态不受影响。
    assert _review(client, alpha["cardId"], at, "known") == 409
    row = _pool_row(alpha["cardId"])
    assert (row["status"], row["repeat_count"], row["defer_remaining"]) == (
        "pending", 0, 3,
    )

    # 池端点对非池卡 404（bravo 未入池）。
    status, _ = _pool_review(client, bravo["cardId"], "known")
    assert status == 404

    # 池内全套操作后 reviews 仍只 1 条（409 不复现——重复操作走池）。
    _pool_review(client, alpha["cardId"], "uncertain")
    _pool_review(client, alpha["cardId"], "known")
    _pool_review(client, alpha["cardId"], "known")
    assert _review_count(alpha["cardId"], day.isoformat()) == 1


# ---------------------------------------------------------------------------
# 多义项边界态：以卡为粒度整卡重新出现、兄弟义项不互相推进
# ---------------------------------------------------------------------------


def test_multi_sense_siblings_dedup_and_no_mutual_advance(tmp_path, monkeypatch):
    """多义项边界态（规格规则 1 / 3 + 边界态「多义项以卡为粒度」）：

    生产形态：today_queue 行是词级（primary cardId）；兄弟 sense 卡只
    出现在 StudyCardResponse.cardIds 用于渲染，不在队列里。因此
    兄弟 sense 评分：① 不入池（_enter_repeat_pool 的 today_queue join
    不到）；② 不推进其他池卡的 defer（_decrement 的 book_id scoping
    不到，兄弟卡不在队列）；③ 兄弟评分不影响彼此（book_id 限制）。
    会话读出时按词去重：同词的多张池卡（实际不会出现）只注入一张。

    关键不变式：单次展示的多义项学习卡多 POST 一次不会重复推进他
    人计数 —— 这是 sibling-exclusion 与 book_id scoping 的双重保险
    （book_id scoping 在生产里就是主防线；sibling-exclusion 是
    invariant 破裂时的兜底）。"""
    day = date(2026, 9, 11)
    _pin(monkeypatch, day, 10, 0)
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["alpha", "bravo", "charlie", "delta", "echo"])
    _prepare(client, 5)
    with connect() as conn:
        user_id = conn.execute("select user_id from cards limit 1").fetchone()["user_id"]
        alpha_main = conn.execute(
            """
            select cards.id from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text = 'alpha'
            order by entries.sense_order
            limit 1
            """
        ).fetchone()["id"]

    # 在 first-start 之前追加 alpha 第二个义项：让 due 队列与 StudyCard
    # 自然包含双义项（与生产一致）。
    _add_second_sense(alpha_main, user_id, study_day=day)
    _backdate_unreviewed_cards(day)
    session = _start(client, target=5)
    alpha = [c for c in session["cards"] if c["word"] == "alpha"][0]
    assert set(alpha["cardIds"]) == {alpha["cardId"], alpha["cardId"]} | set(
        c for c in alpha["cardIds"] if c != alpha["cardId"]
    )
    sibling_id = next(c for c in alpha["cardIds"] if c != alpha["cardId"])
    # 队列行只含主卡：兄弟义项卡不在 today_queue。
    with connect() as conn:
        in_queue = conn.execute(
            "select count(*) as c from today_queue where card_id = ?",
            (sibling_id,),
        ).fetchone()["c"]
    assert in_queue == 0, (
        "队列行应为词级 primary，兄弟义项卡不在 today_queue 里。"
        "若此不变式破了：_enter_repeat_pool 兄弟会入池、_decrement 会"
        "多推进计数 —— sibling-exclusion SQL 与词去重是兜底。"
    )

    at = datetime.combine(day, dtime(10, 2), tzinfo=_BEIJING)
    # 序：先评 bravo New（建立第二条池行 defer 3），再评 alpha New
    # （主卡+兄弟双 POST）。期望：bravo 池行 defer 被推进一次（2），不
    # 会被 alpha 兄弟的二次 POST 重复推进。
    session = _start(client, target=5)
    bravo = [c for c in session["cards"] if c["word"] == "bravo"][0]
    assert _review(client, bravo["cardId"], at, "unknown") == 200
    assert _pool_row(bravo["cardId"])["defer_remaining"] == 3

    # alpha 双 POST（前端对 cardIds 逐条提交 —— 既有行为）。
    assert _review(client, alpha["cardId"], at, "unknown") == 200
    assert _review(client, sibling_id, at, "unknown") == 200
    # alpha 主卡入池；兄弟义项未在队列里、不入池。
    assert _pool_row(alpha["cardId"])["status"] == "pending"
    assert _pool_row(sibling_id) is None
    # 关键不变式：bravo 池行 defer 从 3 推进到 2（一次，不是两次）。
    assert _pool_row(bravo["cardId"])["defer_remaining"] == 2

    # 会话读出：alpha 整词呈现一次（dedup-by-word 兜底生产无多池行），
    # 不计为两张。
    session = _start(client, target=5)
    assert _repeat_words_in(session).count("alpha") == 1
    # 间隔 3 张（释义 B：池卡弹出不算展示消耗；只前进队列卡的展示）：
    # bravo(2)→charlie→1→delta→0 → bravo 弹出；alpha(3)→charlie→2→
    # delta→1→bravo 不消耗→echo→0 → echo 后 alpha 出现。
    assert _words_in(session) == [
        "charlie", "delta", "bravo", "echo", "alpha",
    ]
    repeat_alpha = next(c for c in session["cards"] if c["word"] == "alpha")
    assert set(repeat_alpha["cardIds"]) == {alpha["cardId"], sibling_id}

    # 整卡 Got it：池端点只对主卡有意义（兄弟 404），alpha 主池行
    # cleared。Frontend 对 cardIds 逐条提交并容忍 404。
    status, _ = _pool_review(client, alpha["cardId"], "known")
    assert status == 200
    assert _pool_row(alpha["cardId"])["status"] == "cleared"
    status, _ = _pool_review(client, sibling_id, "known")
    assert status == 404  # 兄弟义项不在池里
    session = _start(client, target=5)
    assert _repeat_words_in(session) == ["bravo"]
