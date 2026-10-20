"""2026-09-12 内置第四~第八本词书导入（PRD ch.15 + ch.16）.

Acceptance criteria covered here (task 2026-09-12 五本新内置词书入库):
1. 各书落库词数 == 终表词数: importing the five PM-final word lists lands
   exactly 5,461 / 5,584 / 4,666 / 5,814 / 3,961 rows per book.
2. 导入幂等 (PRD ch.15/16 数据安全): re-running without --replace-existing
   imports nothing new and leaves counts / sequence_index unchanged; with
   --replace-existing the book's rows are cleanly rebuilt.
3. 隔离/不污染: the pre-existing default book's rows and aggregates are
   untouched; book rows appear exactly when words land (数据未就绪不入架).
4. 书架顺序: the five new books append to the shelf tail in import order
   (托福 → 六级 → 四级 → 专四 → 专八, list_books orders by created_at, id).
5. description 按落库实测词量生成（PRD ch.15/16 交互规则 1）.
"""

import importlib.util
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# The batch importer lives in backend/scripts/ (no package); load it by
# path. Loading executes its sys.path bootstrap for backend/ which pytest
# already provides, so this is safe under the existing rootdir layout.
_SPEC = importlib.util.spec_from_file_location(
    "import_builtin_books", BACKEND_DIR / "scripts" / "import_builtin_books.py"
)
import_builtin_books = importlib.util.module_from_spec(_SPEC)
# Register before exec: the module's frozen dataclass resolves its class
# namespace through sys.modules[cls.__module__].
sys.modules[_SPEC.name] = import_builtin_books
_SPEC.loader.exec_module(import_builtin_books)

from app.db import connect  # noqa: E402
from app.repositories import import_book_words_csv  # noqa: E402

DATA_DIR = BACKEND_DIR.parent / "data"

# PM 终表词量（2026-09-11 放量验收口径；导入落库必须逐书相等）。
EXPECTED_COUNTS = {
    "toefl-zhenjing-2026": 5461,
    "cet6-shanguo-2026": 5584,
    "cet4-shanguo-2026": 4666,
    "tem4-ruyudeshui-2026": 5814,
    "tem8-ruyudeshui-2026": 3961,
}


def _import_default_book(words: list[str]) -> None:
    csv_lines = ["sequence_index,word"]
    csv_lines += [f"{index},{word}" for index, word in enumerate(words, start=1)]
    import_book_words_csv(
        "\n".join(csv_lines).encode(),
        source_name="IELTS Book",
        replace_existing=False,
    )


def _snapshot_books_and_default_words() -> tuple[list[tuple], list[tuple]]:
    with connect() as connection:
        books = connection.execute(
            "select id, title, created_at from vocabulary_books order by created_at, id"
        ).fetchall()
        default_words = connection.execute(
            "select sequence_index, word_text, normalized_text, import_status "
            "from book_words where book_id = 'default-book' order by sequence_index"
        ).fetchall()
    return [tuple(row) for row in books], [tuple(row) for row in default_words]


def _landed_counts() -> dict[str, int]:
    with connect() as connection:
        rows = connection.execute(
            "select book_id, count(*) as total from book_words group by book_id"
        ).fetchall()
    return {row["book_id"]: row["total"] for row in rows}


def test_import_five_builtin_books_matches_final_csv_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _import_default_book(["atmosphere", "carbon", "habitat"])

    for spec in import_builtin_books.BOOK_SPECS:
        stats = import_builtin_books.import_book(spec, DATA_DIR, replace_existing=False)
        assert stats["landed"] == EXPECTED_COUNTS[spec.book_id], spec.book_id
        assert stats["match"] == 1, spec.book_id

    # 1) 逐书落库词数 == 终表词数（PM 复核口径）
    landed = _landed_counts()
    for book_id, expected in EXPECTED_COUNTS.items():
        assert landed[book_id] == expected, book_id
    assert landed["default-book"] == 3

    # 2) 数据未就绪不入架 → 数据就绪即入架: exactly default + 5 new books
    with connect() as connection:
        rows = connection.execute(
            "select id, description from vocabulary_books order by created_at, id"
        ).fetchall()
    assert [row["id"] for row in rows] == [
        "default-book",
        "toefl-zhenjing-2026",
        "cet6-shanguo-2026",
        "cet4-shanguo-2026",
        "tem4-ruyudeshui-2026",
        "tem8-ruyudeshui-2026",
    ]

    # 3) description 按落库实测词量生成
    for row in rows[1:]:
        assert row["description"] is not None
        assert "公开词表整理版" in row["description"]
        assert "落库实测为准" in row["description"]

    # 4) 隔离: the default book's rows are untouched (导入前后逐项一致)
    books_snapshot, default_words_snapshot = _snapshot_books_and_default_words()
    assert default_words_snapshot == [
        (1, "atmosphere", "atmosphere", "pending"),
        (2, "carbon", "carbon", "pending"),
        (3, "habitat", "habitat", "pending"),
    ]
    assert len(books_snapshot) == 6


def test_reimport_is_idempotent_without_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _import_default_book(["atmosphere"])

    for spec in import_builtin_books.BOOK_SPECS:
        import_builtin_books.import_book(spec, DATA_DIR, replace_existing=False)
    before = _landed_counts()

    # 重跑（不带 --replace-existing）: nothing new lands, counts unchanged
    for spec in import_builtin_books.BOOK_SPECS:
        stats = import_builtin_books.import_book(spec, DATA_DIR, replace_existing=False)
        assert stats["imported"] == 0, spec.book_id
        assert stats["landed"] == EXPECTED_COUNTS[spec.book_id]
    assert _landed_counts() == before

    # sequence_index 无错乱: each book still has 1..N contiguous indices
    with connect() as connection:
        for book_id in EXPECTED_COUNTS:
            rows = connection.execute(
                "select sequence_index from book_words where book_id = ? "
                "order by sequence_index",
                (book_id,),
            ).fetchall()
            assert [row["sequence_index"] for row in rows] == list(
                range(1, EXPECTED_COUNTS[book_id] + 1)
            )


def test_reimport_with_replace_rebuilds_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _import_default_book(["atmosphere"])

    for spec in import_builtin_books.BOOK_SPECS:
        import_builtin_books.import_book(spec, DATA_DIR, replace_existing=False)
    before = _landed_counts()
    assert before["default-book"] == 1

    # --replace-existing 只重建本 book+source 的行，不触碰其它书
    for spec in import_builtin_books.BOOK_SPECS:
        stats = import_builtin_books.import_book(spec, DATA_DIR, replace_existing=True)
        assert stats["imported"] == EXPECTED_COUNTS[spec.book_id]
        assert stats["landed"] == EXPECTED_COUNTS[spec.book_id]

    after = _landed_counts()
    assert after == before  # 全部书计数不变（含 default-book）


def test_main_cli_exit_code_and_shelf_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    monkeypatch.setattr(
        sys, "argv", ["import_builtin_books.py", "--db-path", str(tmp_path / "cli.sqlite")]
    )
    assert import_builtin_books.main() == 0
    output = capsys.readouterr().out
    # migrate() provisions the default book row on the fresh DB (its
    # created_at lands slightly after the first book's pre-connect `now`
    # snapshot on a fresh DB) → 6 total books, default included.
    assert "books_total=6" in output
    assert "MISMATCH" not in output
    # 书架序输出：五本新书按导入序（= created_at 序）追加在尾部
    order = [
        line.split("|")[0].strip().replace("shelf: ", "")
        for line in output.splitlines()
        if line.startswith("  shelf:")
    ]
    assert "default-book" in order
    assert [book_id for book_id in order if book_id != "default-book"] == [
        "toefl-zhenjing-2026",
        "cet6-shanguo-2026",
        "cet4-shanguo-2026",
        "tem4-ruyudeshui-2026",
        "tem8-ruyudeshui-2026",
    ]



def test_coexists_with_preexisting_dagang_books_no_overwrite(tmp_path, monkeypatch):
    """生产边界（任务 2026-09-12）：生产库已有 8 本书，其中「大学英语六级大纲
    词汇」「专四英语大纲词汇」与本次「六级词汇闪过」「如鱼得水记单词·专四」
    是不同书 —— 导入不得覆盖 / 干扰它们（书名相近但 id 不同）."""
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "vocabulary.sqlite"))
    _import_default_book(["atmosphere"])

    # 模拟生产既有的大纲词书（id/名称与本次五本均不同）
    from app.books import upsert_book

    dagang_books = [
        ("cet6-dagang-2026", "大学英语六级大纲词汇"),
        ("tem4-dagang-2026", "专四英语大纲词汇"),
    ]
    with connect() as connection:
        for book_id, title in dagang_books:
            upsert_book(connection, book_id, title=title)
    with connect() as connection:
        for book_id, _ in dagang_books:
            connection.execute(
                "insert into sources (id, type, name, path_or_url, metadata_json,"
                " created_at) values (?, 'csv', ?, NULL, NULL, '2026-01-01T00:00:00')",
                (f"{book_id}-src", f"{book_id}词表"),
            )
        for book_id, _ in dagang_books:
            for index in range(1, 4):
                connection.execute(
                    "insert into book_words (id, source_id, book_id, sequence_index,"
                    " word_text, normalized_text, import_status, created_at, updated_at)"
                    " values (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        f"{book_id}-{index}",
                        f"{book_id}-src",
                        book_id,
                        index,
                        f"dagang{index}",
                        f"dagang{index}",
                        "2026-01-01T00:00:00",
                        "2026-01-01T00:00:00",
                    ),
                )

    for spec in import_builtin_books.BOOK_SPECS:
        import_builtin_books.import_book(spec, DATA_DIR, replace_existing=False)

    landed = _landed_counts()
    # 既有大纲词书词数原样（各 3 词，未被动过）
    for book_id, _ in dagang_books:
        assert landed[book_id] == 3, book_id
    # 五本新书各自落满，与大纲书无串扰
    for book_id, expected in EXPECTED_COUNTS.items():
        assert landed[book_id] == expected, book_id

    # 书架：五本新书 created_at 晚于既有书 → 全部追加在尾部，按导入序
    with connect() as connection:
        shelf = [
            row["id"]
            for row in connection.execute(
                "select id from vocabulary_books order by created_at, id"
            )
        ]
    assert shelf[:3] == ["default-book", "cet6-dagang-2026", "tem4-dagang-2026"]
    assert shelf[3:] == [
        "toefl-zhenjing-2026",
        "cet6-shanguo-2026",
        "cet4-shanguo-2026",
        "tem4-ruyudeshui-2026",
        "tem8-ruyudeshui-2026",
    ]
