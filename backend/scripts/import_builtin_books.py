"""Import the 2026-09 batch of built-in word-list books (PRD ch.15 + ch.16).

Imports the five PM-final word lists (托福词汇真经 / 六级词汇闪过 / 四级词汇
闪过 / 如鱼得水记单词·专四 / 如鱼得水记单词·专八) into ``book_words`` via
the same ``import_book_words_csv`` pipeline as every prior built-in book, so
the bookshelf rule 「数据未就绪不入架」holds — each book row appears exactly
when its words land, and the import order below decides the shelf order
(书架按 created_at, id 排序 → 五本新书排在既有书之后).

Usage (server, from the repo root or the backend/ directory):

    python backend/scripts/import_builtin_books.py \
        --db-path /path/to/vocabulary.sqlite

    # re-import cleanly before any study activity happened on the books:
    python backend/scripts/import_builtin_books.py \
        --db-path /path/to/vocabulary.sqlite --replace-existing

Idempotency (PRD ch.15/16 数据安全): re-running without --replace-existing
skips already-present rows (imported=0) and rewrites nothing; with
--replace-existing it first deletes only this book's rows for this source
(never touching other books), then re-inserts. Descriptions are always
rewritten from the post-import measured counts, so re-runs keep the
「落库实测为准」word counts in sync.

Exit code 0 only when every book's landed word count equals its CSV word
count (task acceptance #1); otherwise 1.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.books import (  # noqa: E402
    CET4_SHANGUO_BOOK_ID,
    CET4_SHANGUO_BOOK_TITLE,
    CET6_SHANGUO_BOOK_ID,
    CET6_SHANGUO_BOOK_TITLE,
    TEM4_RUYUDESHUI_BOOK_ID,
    TEM4_RUYUDESHUI_BOOK_TITLE,
    TEM8_RUYUDESHUI_BOOK_ID,
    TEM8_RUYUDESHUI_BOOK_TITLE,
    TOEFL_BOOK_ID,
    TOEFL_BOOK_TITLE,
    upsert_book,
)
from app.db import connect  # noqa: E402
from app.repositories import import_book_words_csv  # noqa: E402


@dataclass(frozen=True)
class BookSpec:
    book_id: str
    title: str
    csv_name: str
    # description template with {total} = post-import measured word count.
    # PRD ch.15 交互规则 1 / ch.16 交互规则 1: 注明词量口径、结构编排与
    # 词表来源「公开词表整理版」；tier/chapter 标注来源未提供、按边界态
    # 置空不猜测补标（终表两列均为空），description 因此不含分层分布。
    description_template: str


# 导入顺序 = 书架展示顺序（services.list_books 按 created_at, id 排序，
# 五本新书 created_at 晚于生产既有书 → 追加在书架尾部）。
BOOK_SPECS: tuple[BookSpec, ...] = (
    BookSpec(
        book_id=TOEFL_BOOK_ID,
        title=TOEFL_BOOK_TITLE,
        csv_name="toefl_zhenjing_word_list.csv",
        description_template=(
            "核心词 {total}（按 22 章主题词群编排；公开词表整理版，落库实测为准）"
        ),
    ),
    BookSpec(
        book_id=CET6_SHANGUO_BOOK_ID,
        title=CET6_SHANGUO_BOOK_TITLE,
        csv_name="cet6_shanguo_word_list.csv",
        description_template=(
            "大纲全量 {total}（按考频分层编排；公开词表整理版，落库实测为准）"
        ),
    ),
    BookSpec(
        book_id=CET4_SHANGUO_BOOK_ID,
        title=CET4_SHANGUO_BOOK_TITLE,
        csv_name="cet4_shanguo_word_list.csv",
        description_template=(
            "大纲 + 补充词合计 {total}（按考频分层编排；公开词表整理版，落库实测为准）"
        ),
    ),
    BookSpec(
        book_id=TEM4_RUYUDESHUI_BOOK_ID,
        title=TEM4_RUYUDESHUI_BOOK_TITLE,
        csv_name="tem4_ruyudeshui_word_list.csv",
        description_template=(
            "核心词 {total}（Lesson 1~30 单元编排；公开词表整理版，落库实测为准）"
        ),
    ),
    BookSpec(
        book_id=TEM8_RUYUDESHUI_BOOK_ID,
        title=TEM8_RUYUDESHUI_BOOK_TITLE,
        csv_name="tem8_ruyudeshui_word_list.csv",
        description_template=(
            "核心词 {total}（Lesson 1~30 单元编排；公开词表整理版，落库实测为准）"
        ),
    ),
)


def count_csv_words(csv_path: Path) -> int:
    """Count data rows in an import CSV (headers: sequence_index,word,layer)."""
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        lines = [line for line in handle if line.strip()]
    # subtract the header line
    return max(len(lines) - 1, 0)


def import_book(spec: BookSpec, csv_dir: Path, replace_existing: bool) -> dict[str, int]:
    csv_path = csv_dir / spec.csv_name
    if not csv_path.is_file():
        raise FileNotFoundError(f"word list CSV not found: {csv_path}")
    expected = count_csv_words(csv_path)

    result = import_book_words_csv(
        csv_path.read_bytes(),
        source_name=f"{spec.title}词表",
        replace_existing=replace_existing,
        book_id=spec.book_id,
        book_title=spec.title,
    )

    # PRD ch.15/16 交互规则 1: description 注明落库实测词量 — rewrite from
    # the measured count so re-runs stay in sync (same pattern as
    # backend/scripts/import_book_csv.py refresh_book_description).
    with connect() as connection:
        landed = connection.execute(
            "select count(*) as total from book_words where book_id = ?",
            (spec.book_id,),
        ).fetchone()["total"]
        upsert_book(
            connection,
            spec.book_id,
            title=spec.title,
            description=spec.description_template.format(total=landed),
            source=None,
        )

    return {
        "expected": expected,
        "imported": result.imported,
        "skipped": result.skipped,
        "landed": landed,
        "match": int(landed == expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Target SQLite file. Defaults to the app's VOCAB_DB_PATH / "
        "backend/data/vocabulary.sqlite resolution (set the env var instead "
        "if you prefer).",
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=REPO_DIR / "data",
        help="Directory holding the *_word_list.csv files (default: repo data/).",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete this book's rows for the source before importing. Only "
        "safe before any study activity (cards/reviews) exists on the books.",
    )
    args = parser.parse_args()

    if args.db_path is not None:
        os.environ["VOCAB_DB_PATH"] = str(args.db_path)

    overall_ok = True
    for spec in BOOK_SPECS:
        stats = import_book(spec, args.csv_dir, args.replace_existing)
        ok = stats["match"] == 1
        overall_ok = overall_ok and ok
        status = "OK" if ok else "MISMATCH"
        print(
            f"[{status}] {spec.title} ({spec.book_id}): "
            f"expected={stats['expected']} landed={stats['landed']} "
            f"imported={stats['imported']} skipped={stats['skipped']}"
        )

    with connect() as connection:
        books = connection.execute(
            "select id, title from vocabulary_books order by created_at, id"
        ).fetchall()
    print(f"books_total={len(books)}")
    for row in books:
        print(f"  shelf: {row['id']} | {row['title']}")

    if not overall_ok:
        print("ERROR: some book's landed count != CSV word count", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
