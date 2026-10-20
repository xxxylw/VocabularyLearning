from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.books import upsert_book  # noqa: E402
from app.db import connect  # noqa: E402
from app.repositories import import_book_words_csv  # noqa: E402


def refresh_book_description(book_id: str) -> dict[str, int]:
    """Rewrite the book description from the imported book_words rows.

    PRD ch.10 交互规则 1: the description must state the per-layer word
    counts (必考词 X / 基础词 Y / 超纲词 Z…) with 落库实测 numbers. Runs
    after the import inside the same script so re-imports keep the numbers
    in sync.
    """
    with connect() as connection:
        total = connection.execute(
            "select count(*) as total from book_words where book_id = ?",
            (book_id,),
        ).fetchone()["total"]
        layer_rows = connection.execute(
            """
            select layer, count(*) as total
            from book_words
            where book_id = ? and layer is not null
            group by layer
            order by min(sequence_index)
            """,
            (book_id,),
        ).fetchall()
        title = connection.execute(
            "select title from vocabulary_books where id = ?", (book_id,)
        ).fetchone()["title"]

    layer_counts = {row["layer"]: row["total"] for row in layer_rows}
    if layer_counts:
        layer_text = " / ".join(
            f"{layer} {layer_counts[layer]}" for layer in layer_counts
        )
        description = f"{layer_text}（合计 {total} 词；词表来源：公开词表整理版）"
    else:
        description = f"共 {total} 词"
    with connect() as connection:
        upsert_book(
            connection,
            book_id,
            title=title,
            description=description,
            source=None,
        )
    return layer_counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import an ordered word-list CSV (sequence_index,word[,layer]) "
            "into book_words for a given book."
        )
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--title", required=True, help="vocabulary_books.title")
    parser.add_argument(
        "--source-name",
        default=None,
        help="sources.name for the CSV source. Defaults to '<title>词表'.",
    )
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace book_words for this source+book before importing.",
    )
    args = parser.parse_args()

    if args.db_path is not None:
        os.environ["VOCAB_DB_PATH"] = str(args.db_path)

    result = import_book_words_csv(
        args.csv_path.read_bytes(),
        source_name=args.source_name or f"{args.title}词表",
        replace_existing=args.replace_existing,
        book_id=args.book_id,
        book_title=args.title,
    )
    print(f"source_id={result.sourceId}")
    print(f"imported={result.imported}")
    print(f"skipped={result.skipped}")
    print(f"needs_review={result.needsReview}")

    layer_counts = refresh_book_description(args.book_id)
    for layer, count in layer_counts.items():
        print(f"layer_{layer}={count}")


if __name__ == "__main__":
    main()
