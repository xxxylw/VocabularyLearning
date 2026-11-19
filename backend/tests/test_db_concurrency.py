"""并发复现测试（2026-09-07 线上 database is locked 事故回归）。

事故链：每个 connect() 都跑 migrate()（executescript + 写语句，读路径的
连接也要抢写锁）× prepare_book_words 巨型单事务（事务内逐词 Oxford HTTP
拉取，写锁被持有分钟级）→ busy timeout 耗尽 → 触 DB 端点全部 502/500 约
1 小时。修复三件套：busy_timeout ≥5s + WAL、migrate 每库每进程一次、
enrichment 逐词提交（HTTP 在事务外）。以下用例锁死这三个行为。
"""

import threading
import uuid

import pytest

from app import db as db_module
from app.db import connect
from app.enrichment import PreparedSense
from app.models import PrepareJobRequest
from app.services import prepare_book_words

# 事故当天的默认书 + 一个测试 source（book_words.source_id 非空外键）。
_TEST_SOURCE_SQL = (
    "insert into sources (id, type, name, path_or_url, metadata_json, created_at)"
    " values ('src-concurrency-test', 'csv', 'concurrency-test', null, null,"
    " '2026-09-07T00:00:00+00:00')"
)


def _use_db(monkeypatch, tmp_path):
    path = tmp_path / "concurrency.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(path))
    return path


def _seed_pending_words(words):
    with connect() as connection:
        connection.execute(_TEST_SOURCE_SQL)
        for sequence, word in enumerate(words, start=1):
            connection.execute(
                """
                insert into book_words (
                    id,
                    source_id,
                    book_id,
                    sequence_index,
                    word_text,
                    normalized_text,
                    import_status,
                    created_at,
                    updated_at
                )
                values (?, ?, 'default-book', ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    f"book-word-{word}",
                    "src-concurrency-test",
                    sequence,
                    word,
                    word,
                    "2026-09-07T00:00:00+00:00",
                    "2026-09-07T00:00:00+00:00",
                ),
            )


def _super_user_id():
    with connect() as connection:
        row = connection.execute(
            "select id from users where is_super = 1 order by created_at limit 1"
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _simple_senses(word):
    return [
        PreparedSense(
            part_of_speech="word",
            sense_label="general use",
            definition=f"definition of {word}",
        )
    ]


def test_connect_sets_busy_timeout_and_wal(tmp_path, monkeypatch):
    """读连接必须带 busy_timeout ≥5s，且库在 WAL 模式（读写不互斥）。"""
    _use_db(monkeypatch, tmp_path)

    with connect() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        timeout_ms = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert mode.lower() == "wal"
    assert timeout_ms >= 5000
    assert db_module.BUSY_TIMEOUT_MS >= 5000


def test_migrate_runs_once_per_database_file(tmp_path, monkeypatch):
    """migrate() 对同一 DB 文件每进程只跑一次；换库文件仍要迁移。"""
    _use_db(monkeypatch, tmp_path)
    calls = []
    real_migrate = db_module.migrate

    def counting_migrate(connection):
        calls.append(connection)
        real_migrate(connection)

    monkeypatch.setattr(db_module, "migrate", counting_migrate)

    for _ in range(5):
        with connect() as connection:
            connection.execute("select count(*) from users").fetchone()
    assert len(calls) == 1

    # 同进程内切换到另一个库文件（测试按 VOCAB_DB_PATH 换 tmp 库 / 脚本
    # 连接别的文件）：新文件必须完整迁移。
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "second.sqlite"))
    with connect() as connection:
        connection.execute("select count(*) from users").fetchone()
    assert len(calls) == 2


def test_reads_and_small_writes_survive_background_bulk_writer(
    tmp_path, monkeypatch
):
    """事故复现：多线程读 + 后台大写入，断言全程无 OperationalError。

    4 个读线程在后台批量写（分批事务，每批 200 行 × 15 批）期间持续
    connect() 读 users 并做少量小写（settings）；修复后（WAL + 读连接
    busy_timeout）任何线程都不应见到 database is locked。
    """
    _use_db(monkeypatch, tmp_path)

    with connect() as connection:
        connection.executescript(
            "create table if not exists stress_writes"
            " (id integer primary key, payload text not null)"
        )

    errors = []
    stop = threading.Event()
    batches, rows_per_batch = 15, 200

    def bulk_writer():
        try:
            for _ in range(batches):
                with connect() as connection:
                    for _ in range(rows_per_batch):
                        connection.execute(
                            "insert into stress_writes (payload) values (?)",
                            ("x" * 128,),
                        )
        except Exception as exc:  # noqa: BLE001 — 记录后统一断言
            errors.append(("writer", repr(exc)))
        finally:
            stop.set()

    def reader(index):
        try:
            iterations = 0
            while not stop.is_set():
                with connect() as connection:
                    connection.execute("select count(*) from users").fetchone()
                if iterations % 8 == 7:
                    with connect() as connection:
                        connection.execute(
                            "insert into settings (key, value) values (?, ?)",
                            (f"reader-{index}-{uuid.uuid4()}", "1"),
                        )
                iterations += 1
        except Exception as exc:  # noqa: BLE001 — 记录后统一断言
            errors.append(("reader", index, repr(exc)))

    threads = [threading.Thread(target=bulk_writer)]
    threads += [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "并发线程未收敛"
    assert errors == [], f"并发期间出现异常: {errors}"

    with connect() as connection:
        total = connection.execute(
            "select count(*) from stress_writes"
        ).fetchone()[0]
    assert total == batches * rows_per_batch


def test_enrichment_provider_runs_outside_any_write_transaction(
    tmp_path, monkeypatch
):
    """Oxford 拉取窗口（provider.prepare）必须发生在写事务之外。

    provider 在拉取窗口内通过第二个连接写库：若 prepare 仍运行在
    prepare_book_words 的巨型单事务里（事故形态），第二个连接的写入会
    排队等满 busy_timeout 后抛 OperationalError，本测试随之失败。
    """
    _use_db(monkeypatch, tmp_path)
    words = ["alpha", "beta", "gamma", "delta"]
    _seed_pending_words(words)
    user_id = _super_user_id()
    probed = []

    class ProbingProvider:
        def prepare(self, word, max_senses):
            probe_key = f"probe-{word}-{uuid.uuid4()}"
            with connect() as connection:
                connection.execute(
                    "insert into settings (key, value) values (?, ?)",
                    (probe_key, "ok"),
                )
            probed.append(word)
            return _simple_senses(word)

    import app.services as services_module

    monkeypatch.setattr(
        services_module, "_create_enrichment_provider", lambda: ProbingProvider()
    )

    response = prepare_book_words(
        user_id,
        PrepareJobRequest(scope="next", count=len(words), maxSensesPerWord=5),
        is_super=True,
    )

    assert response.processedWords == len(words)
    assert response.readyCards == len(words)
    assert probed == words
    with connect() as connection:
        pending = connection.execute(
            "select count(*) from book_words where import_status = 'pending'"
        ).fetchone()[0]
    assert pending == 0


def test_prepare_commits_per_word_so_partial_progress_persists(
    tmp_path, monkeypatch
):
    """enrichment 分批提交：第 3 个词失败时前两词已持久化。

    旧实现整批一个事务，中途失败全部回滚（重试等于从头再来，8722 词的
    书每次失败都白干）；新实现逐词提交，重跑幂等续作。
    """
    _use_db(monkeypatch, tmp_path)
    words = ["alpha", "beta", "gamma", "delta"]
    _seed_pending_words(words)
    user_id = _super_user_id()

    class FlakyProvider:
        def __init__(self):
            self.calls = 0

        def prepare(self, word, max_senses):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("simulated enrichment failure")
            return _simple_senses(word)

    import app.services as services_module

    monkeypatch.setattr(
        services_module, "_create_enrichment_provider",
        lambda: FlakyProvider(),
    )

    with pytest.raises(RuntimeError, match="simulated enrichment failure"):
        prepare_book_words(
            user_id,
            PrepareJobRequest(scope="next", count=len(words), maxSensesPerWord=5),
            is_super=True,
        )

    with connect() as connection:
        ready = connection.execute(
            "select count(*) from book_words where import_status = 'ready'"
        ).fetchone()[0]
        cards = connection.execute(
            "select count(*) from cards where user_id = ?", (user_id,)
        ).fetchone()[0]
        jobs = connection.execute(
            "select count(*) from prepare_jobs"
        ).fetchone()[0]

    assert ready == 2
    assert cards == 2  # 每词 1 条词条 × 1 卡片
    assert jobs == 0  # 失败的批次不写 completed job 行
