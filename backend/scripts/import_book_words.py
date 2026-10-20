from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.repositories import import_book_words_markdown  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an ordered numbered Markdown vocabulary list into book_words."
    )
    parser.add_argument("markdown_path", type=Path)
    parser.add_argument(
        "--source-name",
        default="IELTS Vocabulary Book",
        help="Source name stored in SQLite.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite path. Defaults to ./data/vocabulary.sqlite from the current working directory.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace book_words for this source before importing.",
    )
    args = parser.parse_args()

    if args.db_path is not None:
        os.environ["VOCAB_DB_PATH"] = str(args.db_path)

    result = import_book_words_markdown(
        args.markdown_path.read_bytes(),
        source_name=args.source_name,
        replace_existing=args.replace_existing,
    )
    print(f"source_id={result.sourceId}")
    print(f"imported={result.imported}")
    print(f"skipped={result.skipped}")
    print(f"needs_review={result.needsReview}")


if __name__ == "__main__":
    main()
