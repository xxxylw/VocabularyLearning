"""Import an ordered word-list CSV as a vocabulary book (v3 P0, V3-05).

Server-side one-shot importer for the third bookshelf slot (考研词汇闪过
and any future word list): reads a UTF-8 CSV with ``sequence_index`` /
``word`` (+ optional ``layer``) headers and routes it through the same
``import_book_words_csv`` path as the super-only HTTP endpoint, so the
bookshelf rule 「数据未就绪不入架」holds — the book row appears exactly
when its words land.

Usage (from the backend/ directory or with backend on sys.path):

    python scripts/import_book_csv.py data/kaoyan_shanguo_word_list.csv \
        --book-id kaoyan-shanguo-2027 \
        --title 考研词汇闪过 \
        --source "PM 数据准备（词表 2026-09-06）" \
        [--replace]

Idempotency: re-running with --replace re-imports the list; without it,
already-present (source, sequence_index) rows are skipped and reported.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="word-list CSV (headers: sequence_index, word, layer?)")
    parser.add_argument("--book-id", required=True, help="stable book id, e.g. kaoyan-shanguo-2027")
    parser.add_argument("--title", required=True, help="bookshelf display title")
    parser.add_argument("--description", default="", help="optional book description")
    parser.add_argument("--source", default="csv", help="source name recorded in sources table")
    parser.add_argument("--replace", action="store_true", help="replace existing rows for the source")
    args = parser.parse_args()

    backend_dir = Path(__file__).resolve().parent.parent / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.repositories import import_book_words_csv  # noqa: E402

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        print(f"ERROR: {csv_path} not found", file=sys.stderr)
        return 1

    result = import_book_words_csv(
        csv_path.read_bytes(),
        source_name=args.source,
        replace_existing=args.replace,
        book_id=args.book_id,
        book_title=args.title,
        book_description=args.description or None,
    )
    print(
        f"imported={result.imported} skipped={result.skipped} "
        f"needs_review={result.needsReview} source_id={result.sourceId}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
