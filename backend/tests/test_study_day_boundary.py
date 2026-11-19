"""学习日边界 02:00（PM 规格：每日学习刷新时间改为凌晨 2:00）.

覆盖规格第六章验收标准的后端部分：
1. 边界三时刻 01:59:59 / 02:00:00.000 / 02:00:01 归旧/新/新学习日。
2. 北京 00:30 完成队列：热点图亮点落自然日「昨天」对应的学习日。
3. 01:50 开始的会话 02:10 提交队列内最后几张：评分归快照学习日、
   dayCompleted=true、新学习日不受污染（进行中会话跨 02:00 不中断）。
4. 队列外提交 02:10：study_date=新学习日（跨书自由复习 / 直接 API）。
5. 篡改客户端时钟（reviewedDate / reviewedAt 乱填）不改变归日。
6. UTC 日界竞态回归：跨书共享词 02:00 边界窗口提交不再 409。
7. 断点续传回归：同学日中途退出重进从断点继续；跨 02:00 重进进入
   新学习日新队列（旧队列行不动）。
8. 新词额度跟随学习日：00:30 不重算额度，02:00 后才重算。
9. 历史数据不变（D2 不迁移）：既有 reviews / check_in_overrides /
   today_queue 行在新的 02:00 口径会话中不被改写。

所有用例通过 monkeypatch study_clock.now 注入固定服务端时刻，
与 CI 机器时区 / 真实运行时刻解耦（conftest.pin_study_clock 默认
被本文件覆盖）。构造「学习日 D-1 的队列」时把未复习卡的
created_on / due_at 回填到 D-1（生产形态：卡片在学习日内创建）。
"""

from __future__ import annotations

import json
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
    """把服务端学习时钟钉到北京时刻 natural_day hh:mm:ss。"""
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
    """显式触发 prepare 建卡（生产形态：卡在学习日内由惰性 prepare
    创建，created_on 跟随真实运行日期）。"""
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
    """把尚无 review 的卡的 created_on / due_at 回填到 day（模拟卡片在
    学习日 day 内创建——生产形态；导入建卡的 created_on 跟随真实
    运行日期，不跟随注入时钟）。"""
    with connect() as conn:
        conn.execute(
            "update cards set created_on = ?, due_at = ? where last_reviewed_at is null",
            (day.isoformat(), day.isoformat()),
        )
        conn.commit()


def _start(client: TestClient, day: date | None, target: int = 20) -> dict:
    """start；day=None 表示不带显式 date（生产前端形态，走服务端口径）。"""
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
    reviewed_date: date | None = None,
    rating: str = "known",
) -> int:
    """提交一条 review，返回 HTTP 状态码。reviewed_at 为（可篡改的）
    客户端时刻；归日由服务端时钟决定。"""
    response = client.post(
        f"/api/cards/{card_id}/reviews",
        json={
            "rating": rating,
            "reviewedAt": reviewed_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            **(
                {"reviewedDate": reviewed_date.isoformat()}
                if reviewed_date is not None
                else {}
            ),
        },
    )
    return response.status_code


def _summary(client: TestClient, day: date) -> dict:
    response = client.get(
        "/api/study/today/summary", params={"date": day.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _check_ins(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/check-ins")
    assert response.status_code == 200, response.text
    return {record["date"]: record for record in response.json()["checkIns"]}


def _snapshot_days() -> set[str]:
    with connect() as conn:
        return {
            row["study_date"]
            for row in conn.execute(
                "select distinct study_date from today_queue_snapshots"
            ).fetchall()
        }


def _queue_rows(study_date: date) -> list[tuple]:
    with connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "select card_id, position, queue_type from today_queue"
                " where study_date = ? order by position",
                (study_date.isoformat(),),
            ).fetchall()
        ]


def _review_study_dates(card_id: str) -> list[str]:
    with connect() as conn:
        return [
            row["study_date"]
            for row in conn.execute(
                "select study_date from reviews where card_id = ? order by study_date",
                (card_id,),
            ).fetchall()
        ]


# ---------------------------------------------------------------------------
# 1. 边界三时刻（纯函数）。


def test_study_day_boundary_three_moments() -> None:
    """01:59:59 归旧学习日；02:00:00.000（含端点）/ 02:00:01 归新学习日。"""
    d = date.today()
    before = datetime.combine(d, dtime(1, 59, 59), tzinfo=_BEIJING)
    at_edge = datetime.combine(d, dtime(2, 0, 0, 0), tzinfo=_BEIJING)
    after = datetime.combine(d, dtime(2, 0, 1), tzinfo=_BEIJING)
    assert study_clock.study_day(before) == d - timedelta(days=1)
    assert study_clock.study_day(at_edge) == d
    assert study_clock.study_day(after) == d


def test_study_day_spans_midnight() -> None:
    """学习日 = [02:00, 次日 02:00)：00:30 时刻仍属前一自然日对应的
    学习日（「今天」在 00:00-02:00 指前一学习日）。"""
    d = date.today()
    assert study_clock.study_day(datetime.combine(d, dtime(0, 30), tzinfo=_BEIJING)) == d - timedelta(days=1)
    next_night = d + timedelta(days=1)
    assert study_clock.study_day(datetime.combine(next_night, dtime(1, 59, 59), tzinfo=_BEIJING)) == d


# ---------------------------------------------------------------------------
# 2. 默认学习日跟随口径（start / summary 不带显式 date）。


def test_start_defaults_to_study_day_across_boundary(tmp_path, monkeypatch):
    """00:30 进入 Today 归前一学习日；02:00:00 起进入归新学习日。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 2)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 0, 30)  # 北京 00:30 → 学习日 prev
    session = _start(client, None, 2)
    assert session["totalCards"] == 2
    assert _snapshot_days() == {prev.isoformat()}

    _pin(monkeypatch, d, 2, 0, 0)  # 北京 02:00:00.000 → 学习日 d
    _start(client, None, 2)
    assert _snapshot_days() == {prev.isoformat(), d.isoformat()}


def test_summary_defaults_to_study_day_across_boundary(tmp_path, monkeypatch):
    """summary 同口径：00:30 查询（不带 date）读前一学习日队列。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 2)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 0, 30)
    _start(client, None, 2)
    response = client.get("/api/study/today/summary")
    assert response.status_code == 200, response.text
    assert response.json()["totalCards"] == 2


# ---------------------------------------------------------------------------
# 3. 00:00-02:00 窗口内的完成：热点图亮点落前一学习日。


def test_completion_at_0030_lights_previous_study_day(tmp_path, monkeypatch):
    """验收：北京 00:30 完成队列，热点图亮点落自然日「昨天」。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 2)
    _backdate_unreviewed_cards(prev)

    # 学习日 prev 的白天开始会话，复习 1 张。
    _pin(monkeypatch, prev, 15, 0)
    session = _start(client, None, 2)
    assert session["totalCards"] == 2
    assert (
        _review(client, session["cards"][0]["cardId"], datetime.combine(prev, dtime(15, 5), tzinfo=_BEIJING))
        == 200
    )

    # 北京 00:30（学习日仍是 prev）完成最后一张。
    _pin(monkeypatch, d, 0, 30)
    assert (
        _review(client, session["cards"][1]["cardId"], datetime.combine(d, dtime(0, 35), tzinfo=_BEIJING))
        == 200
    )

    assert _summary(client, prev)["dayCompleted"] is True
    records = _check_ins(client)
    assert records[prev.isoformat()]["completedCards"] == 2
    assert d.isoformat() not in records  # 自然日「今天」不亮


# ---------------------------------------------------------------------------
# 4. 进行中会话跨 02:00：评分归快照学习日、dayCompleted、新学习日不受污染。


def test_inflight_session_crossing_2am_completes_previous_day(tmp_path, monkeypatch):
    """验收用例 3：01:50 开始的会话，02:10 提交队列内最后几张。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline", "appeal"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 3)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 1, 50)  # 学习日 prev
    session = _start(client, None, 3)
    assert session["totalCards"] == 3
    # 复习前两张（01:55）。
    for card in session["cards"][:2]:
        assert (
            _review(client, card["cardId"], datetime.combine(d, dtime(1, 55), tzinfo=_BEIJING)) == 200
        )
    assert _summary(client, prev)["dayCompleted"] is False

    # 02:10 提交最后一张：不 409、归快照学习日 prev。
    last_card = session["cards"][2]["cardId"]
    assert (
        _review(client, last_card, datetime.combine(d, dtime(2, 10), tzinfo=_BEIJING)) == 200
    )
    assert _review_study_dates(last_card) == [prev.isoformat()]
    assert _summary(client, prev)["dayCompleted"] is True

    # 新学习日不受污染：无 study_date=今天 的 review / 打卡亮点。
    with connect() as conn:
        polluted = conn.execute(
            "select count(*) as c from reviews where study_date = ?",
            (d.isoformat(),),
        ).fetchone()["c"]
    assert polluted == 0
    assert d.isoformat() not in _check_ins(client)


def test_inflight_session_dedup_still_applies_across_boundary(tmp_path, monkeypatch):
    """跨边界窗口内同一卡重复提交：同日去重仍生效（409）。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 2)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 1, 50)
    session = _start(client, None, 2)
    card_id = session["cards"][0]["cardId"]
    assert (
        _review(client, card_id, datetime.combine(d, dtime(1, 55), tzinfo=_BEIJING)) == 200
    )

    _pin(monkeypatch, d, 2, 10)
    assert (
        _review(client, card_id, datetime.combine(d, dtime(2, 15), tzinfo=_BEIJING)) == 409
    )


# ---------------------------------------------------------------------------
# 5. 队列外提交：02:10 归新学习日。


def test_queue_outside_review_after_2am_goes_to_new_day(tmp_path, monkeypatch):
    """验收用例 4：队列外（不经 start 直接 API 复习）02:10 提交 →
    study_date=新学习日。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 1)
    _backdate_unreviewed_cards(prev)

    # 学习日 prev 的会话已完成（队列完成，非进行中）。
    _pin(monkeypatch, prev, 15, 0)
    session = _start(client, None, 1)
    card_id = session["cards"][0]["cardId"]
    assert _review(client, card_id, datetime.combine(prev, dtime(15, 5), tzinfo=_BEIJING)) == 200

    # known 首评 interval=1 → 学习日 d 即 due。02:10（新学习日）队列外
    # 再复习（未 start 当日会话）→ 归新学习日 d。
    _pin(monkeypatch, d, 2, 10)
    assert (
        _review(client, card_id, datetime.combine(d, dtime(2, 12), tzinfo=_BEIJING)) == 200
    )
    assert _review_study_dates(card_id) == [prev.isoformat(), d.isoformat()]


# ---------------------------------------------------------------------------
# 6. 篡改客户端时钟不改变归日。


def test_client_clock_tampering_does_not_change_study_day(tmp_path, monkeypatch):
    """验收：多设备时钟偏差 / 篡改客户端时钟（reviewedAt、reviewedDate
    乱填）不影响服务端归日。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()

    _pin(monkeypatch, d, 2, 10)  # 服务端：学习日 d
    session = _start(client, None, 2)
    card_id = session["cards"][0]["cardId"]

    # 客户端时钟被拨到三天前 / reviewedDate 乱填 —— 归日仍为 d。
    tampered_at = datetime.combine(d - timedelta(days=3), dtime(9, 0), tzinfo=_BEIJING)
    assert _review(client, card_id, tampered_at, reviewed_date=d - timedelta(days=3)) == 200
    assert _review_study_dates(card_id) == [d.isoformat()]


# ---------------------------------------------------------------------------
# 7. UTC 日界竞态回归：跨书共享词不再 409。


def test_shared_card_in_second_book_queue_no_409(tmp_path, monkeypatch):
    """复刻 2026-09-08 生产事故形态：同一张卡出现在多本书的今日队列
    （直接落 today_queue 行模拟书 B 队列），02:25 边界窗口提交不再
    409，归快照学习日。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 1)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 1, 50)  # 学习日 prev，进行中会话
    session = _start(client, None, 1)
    card_id = session["cards"][0]["cardId"]

    # 模拟书 B 的同日队列行（生产事故：跨书共享词同卡多行队列；
    # today_queue.card_id 无外键，是既有设计）。
    with connect() as conn:
        conn.execute(
            "insert into today_queue (id, user_id, book_id, study_date, position,"
            " card_id, queue_type, created_at)"
            " values (?, (select id from users where is_super = 1), 'book-b', ?, 1,"
            " ?, 'new', ?)",
            (
                str(uuid4()),
                prev.isoformat(),
                card_id,
                "2026-09-10T00:00:00+00:00",
            ),
        )
        conn.commit()

    # 02:25（新学习日）提交：进行中会话归快照学习日，成功不 409。
    _pin(monkeypatch, d, 2, 25)
    assert (
        _review(client, card_id, datetime.combine(d, dtime(2, 25), tzinfo=_BEIJING)) == 200
    )
    assert _review_study_dates(card_id) == [prev.isoformat()]


# ---------------------------------------------------------------------------
# 8. 断点续传回归。


def test_resume_same_study_day_continues_from_breakpoint(tmp_path, monkeypatch):
    """同学日中途退出重进：reviewedCards 保持、pending 从断点继续。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline", "appeal"])
    d = date.today()

    _pin(monkeypatch, d, 10, 0)
    session = _start(client, None, 3)
    assert (
        _review(client, session["cards"][0]["cardId"], datetime.combine(d, dtime(10, 5), tzinfo=_BEIJING)) == 200
    )

    # 中途退出重进（同学日，仍走 start）。
    resumed = _start(client, None, 3)
    assert resumed["reviewedCards"] == 1
    assert resumed["totalCards"] == 3
    # pending 从第 2 张开始。
    assert resumed["cards"][0]["cardId"] == session["cards"][1]["cardId"]


def test_resume_after_2am_starts_new_study_day_queue(tmp_path, monkeypatch):
    """跨 02:00 重进：进入新学习日新队列，旧队列行不动（不被改写）。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline", "appeal"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 3)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 1, 50)
    session = _start(client, None, 3)
    assert (
        _review(client, session["cards"][0]["cardId"], datetime.combine(d, dtime(1, 55), tzinfo=_BEIJING)) == 200
    )
    old_rows = _queue_rows(prev)
    assert len(old_rows) == 3

    # 02:30 重进 → 新学习日 d 的新队列（新快照 + 新额度），旧队列不动。
    _pin(monkeypatch, d, 2, 30)
    new_session = _start(client, None, 3)
    assert d.isoformat() in _snapshot_days()
    assert _queue_rows(prev) == old_rows
    # 新学习日 reviewedCards 从 0 开始（新队列不含 prev 的完成态）。
    assert new_session["reviewedCards"] == 0
    # 旧会话的断点仍然可续：切回 prev 查询 summary 保持 1/3。
    assert _summary(client, prev)["reviewedCards"] == 1


# ---------------------------------------------------------------------------
# 9. 新词额度跟随学习日。


def test_new_word_quota_follows_study_day(tmp_path, monkeypatch):
    """00:30 再进 Today 不重算新词额度（同学习日）；02:00 后进入新
    学习日才按当日已学新词数重算配额。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline", "appeal", "betray", "candid"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 5)
    _backdate_unreviewed_cards(prev)

    # 学习日 prev 的下午：学习 2 个新词（额度 2 用满）。
    _pin(monkeypatch, prev, 15, 0)
    session = _start(client, None, 2)
    assert session["totalCards"] == 2
    for card in session["cards"]:
        assert (
            _review(client, card["cardId"], datetime.combine(prev, dtime(15, 5), tzinfo=_BEIJING)) == 200
        )

    # 同一学习日（北京 00:30）再进：额度已用完 → 不追加新卡。
    _pin(monkeypatch, d, 0, 30)
    resumed = _start(client, None, 2)
    assert resumed["totalCards"] == 2
    assert resumed["reviewedCards"] == 2

    # 02:30 新学习日：额度重算 → 队列含当日新词（appeal/betray），
    # 另含 prev 学过、按 SM-2 次日 due 的复习卡（charge/decline）。
    _pin(monkeypatch, d, 2, 30)
    new_session = _start(client, None, 2)
    assert new_session["reviewedCards"] == 0
    assert {c["word"] for c in new_session["cards"]} == {
        "appeal",
        "betray",
        "charge",
        "decline",
    }


# ---------------------------------------------------------------------------
# 10. 历史数据不变（D2：不迁移不重算）。


def test_historical_rows_untouched_under_new_regime(tmp_path, monkeypatch):
    """既有 reviews / check_in_overrides / today_queue 行在新的 02:00
    口径会话中不被改写；既有打卡亮点日期不变。"""
    client = _setup(tmp_path, monkeypatch)
    _import_words(client, ["charge", "decline"])
    d = date.today()
    prev = d - timedelta(days=1)
    _prepare(client, 2)
    _backdate_unreviewed_cards(prev)

    _pin(monkeypatch, d, 10, 0)  # 学习日 d
    session = _start(client, None, 1)
    card_id = session["cards"][0]["cardId"]

    # 播种历史数据：一条过去日期的 review（真实卡，满足 FK）、一条
    # 本地历史上报的 override（日期无 reviews → override 生效）、一行
    # 旧 today_queue（card_id 无 FK 是既有设计）。
    legacy_review_day = d - timedelta(days=30)
    override_day = d - timedelta(days=29)
    with connect() as conn:
        user_id = conn.execute(
            "select id from users where is_super = 1"
        ).fetchone()["id"]
        conn.execute(
            "insert into reviews (id, user_id, card_id, rating, reviewed_at,"
            " previous_stage, next_stage, next_due_at, study_date)"
            " values ('r-legacy', ?, ?, 'known', ?, 0, 1, ?, ?)",
            (
                user_id,
                card_id,
                f"{legacy_review_day.isoformat()}T21:00:00+00:00",
                (legacy_review_day + timedelta(days=1)).isoformat(),
                legacy_review_day.isoformat(),
            ),
        )
        conn.execute(
            "insert into user_settings (user_id, key, value) values (?, 'check_in_overrides', ?)"
            " on conflict(user_id, key) do update set value = excluded.value",
            (
                user_id,
                json.dumps(
                    {
                        override_day.isoformat(): {
                            "completedCards": 12,
                            "newCards": 8,
                            "reviewCards": 4,
                            "completedAt": f"{override_day.isoformat()}T21:00:00.000Z",
                        }
                    }
                ),
            ),
        )
        conn.execute(
            "insert into today_queue (id, user_id, book_id, study_date, position,"
            " card_id, queue_type, created_at)"
            " values ('q-legacy', ?, 'legacy-book', ?, 1, 'no-such-card', 'new', ?)",
            (user_id, legacy_review_day.isoformat(), "2026-08-10T00:00:00+00:00"),
        )
        conn.commit()

    with connect() as conn:
        before_reviews = [tuple(r) for r in conn.execute("select * from reviews order by reviewed_at")]
        before_overrides = conn.execute(
            "select value from user_settings where key = 'check_in_overrides'"
        ).fetchone()["value"]
        before_queue = [tuple(r) for r in conn.execute("select * from today_queue order by study_date, position")]

    # 在新口径下跑一轮学习（学习日 d，复习另一张卡）。
    other_card = _start(client, None, 2)["cards"][-1]["cardId"]
    assert (
        _review(client, other_card, datetime.combine(d, dtime(10, 5), tzinfo=_BEIJING)) == 200
    )

    with connect() as conn:
        after_reviews = [tuple(r) for r in conn.execute("select * from reviews order by reviewed_at")]
        after_overrides = conn.execute(
            "select value from user_settings where key = 'check_in_overrides'"
        ).fetchone()["value"]
        after_queue = [tuple(r) for r in conn.execute("select * from today_queue order by study_date, position")]

    # 历史行未被改写：旧的 review / override / 队列行原样保留。
    assert after_reviews[: len(before_reviews)] == before_reviews
    assert after_overrides == before_overrides
    assert after_queue[: len(before_queue)] == before_queue

    # 既有打卡亮点日期不变：legacy 日的 review 派生记录原样、
    # override 日仍来自本地历史上报（该日无服务端 reviews）。
    records = _check_ins(client)
    assert records[legacy_review_day.isoformat()]["completedCards"] == 1
    assert records[override_day.isoformat()]["completedCards"] == 12
    assert records[override_day.isoformat()]["newCards"] == 8
