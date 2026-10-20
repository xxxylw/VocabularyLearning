"""P0 回归（2026-09-07 QA 第三轮）：today/start 重复调用必须幂等。

生产日志 18:50 出现
``sqlite3.IntegrityError: today_queue UNIQUE constraint failed
(user_id, book_id, study_date, card_id)``。根因（单线程即可复现，不是
并发竞态）：``_merge_new_cards_into_today_queue`` 只把「已以 new 类型
入队的卡」从追加名单里排除；一个多义词若以 review 类型入队、其主卡
（第一个 sense 的卡）又是未复习的新卡，merge 会把它当 fresh 新卡再插一
次 —— 同一 (user, book, date, card_id) 撞 ``idx_today_queue_card``
唯一索引直接 500。快照路径有同样的词级去重
（``review_card_ids`` 过滤），merge 路径漏了 review 侧。
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.db import connect


def _queue_rows(day: date) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "select card_id, queue_type, position from today_queue"
            " where study_date = ? order by position",
            (day.isoformat(),),
        ).fetchall()
    return [dict(row) for row in rows]


def _add_reviewed_sense_for_word(word: str, day: date) -> str:
    """Insert a second entry + a reviewed card for ``word``.

    The word then spans both pools: its primary card (sense 0, new) and a
    second-sense card that was reviewed yesterday and is due today — the
    exact shape that trips the merge path's dedup gap.
    """
    from uuid import uuid4

    with connect() as connection:
        word_row = connection.execute(
            "select id from words where normalized_text = ?", (word,)
        ).fetchone()
        entry_id = "e-" + uuid4().hex[:12]
        connection.execute(
            "insert into entries (id, word_id, sense_order, part_of_speech, sense_label,"
            " definition, definition_source, chinese_note, created_at, updated_at)"
            " values (?, ?, 2, 'noun', '', ?, 'fallback', ?, ?, ?)",
            (entry_id, word_row["id"], "a second sense of " + word, None, _iso_now(), _iso_now()),
        )
        card_id = "c-" + uuid4().hex[:12]
        connection.execute(
            "insert into cards (id, user_id, entry_id, status, stage, due_at, created_on,"
            " last_reviewed_at, ef, interval_days)"
            " values (?, ?, ?, 'learning', 1, ?, ?, ?, 2.5, 1)",
            (
                card_id,
                _super_user_id(connection),
                entry_id,
                day.isoformat(),
                (day - timedelta(days=3)).isoformat(),
                (day - timedelta(days=1)).isoformat() + "T09:00:00+00:00",
            ),
        )
        connection.commit()
    return card_id


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _super_user_id(connection) -> str:
    return connection.execute(
        "select id from users where is_super = 1 limit 1"
    ).fetchone()["id"]


def test_repeated_today_start_is_idempotent_for_dual_pool_word(
    tmp_path, monkeypatch
):
    today = date.today()
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    client = TestClient(create_app())

    csv_lines = "sequence_index,word\n1,mingle\n2,standby"
    response = client.post(
        "/api/book-words/import",
        files={"file": ("book_words.csv", csv_lines.encode(), "text/csv")},
        data={"sourceName": "IELTS Book", "replaceExisting": "false"},
    )
    assert response.status_code == 200

    # Prepare "mingle" once: word + entry(sense 0) + its per-user card (new).
    response = client.post(
        "/api/prepare-jobs",
        json={"scope": "next", "count": 1, "maxSensesPerWord": 5},
    )
    assert response.status_code == 200

    # Give "mingle" a second sense whose card was already reviewed and is
    # due today: the word enters the *review* queue while its primary card
    # is still an unreviewed new card.
    _add_reviewed_sense_for_word("mingle", today)

    first = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 5},
    )
    assert first.status_code == 200
    first_rows = _queue_rows(today)
    assert first_rows, "snapshot should queue the dual-pool word as review"
    assert first_rows[0]["queue_type"] == "review"

    # Regression: the retry must not raise the today_queue UNIQUE
    # constraint failure (QA saw this surface as a 500 after a gateway
    # timeout + browser retry).
    second = client.post(
        "/api/study/today/start",
        json={"date": today.isoformat(), "dailyNewWordTarget": 5},
    )
    assert second.status_code == 200

    # Queue is unchanged: same rows, no duplicate card_id.
    second_rows = _queue_rows(today)
    assert second_rows == first_rows
    card_ids = [row["card_id"] for row in second_rows]
    assert len(card_ids) == len(set(card_ids))
