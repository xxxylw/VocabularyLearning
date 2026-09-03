from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db import connect  # noqa: E402
from app.models import PrepareJobRequest  # noqa: E402
from app.repositories import get_book_progress  # noqa: E402
from app.services import prepare_book_words  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch prepare book_words into entries, examples, and cards."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite path. Defaults to ./data/vocabulary.sqlite from the current working directory.",
    )
    parser.add_argument(
        "--source",
        choices=["oxford", "fallback"],
        default="oxford",
        help="Definition/example provider.",
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-senses", type=int, default=5)
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Maximum words to prepare. Omit to process all pending/needs_review words.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Rebuild entries/examples/cards for each selected word.",
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Mark all book_words as needs_review before preparing.",
    )
    parser.add_argument(
        "--book-id",
        default=None,
        help=(
            "Target book for the batch job (PRD ch.10). Defaults to the "
            "current book; the current-book pointer is never touched."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to wait between batches.",
    )
    args = parser.parse_args()

    if args.db_path is not None:
        os.environ["VOCAB_DB_PATH"] = str(args.db_path)
    os.environ["VOCAB_ENRICHMENT_SOURCE"] = args.source

    if args.refresh_all:
        with connect() as connection:
            connection.execute("update book_words set import_status = 'needs_review'")

    remaining_budget = args.count
    total_processed = 0
    total_ready_cards = 0
    batch_number = 0

    while True:
        progress = get_book_progress()
        if progress.nextSequenceIndex is None:
            break
        if remaining_budget is not None and remaining_budget <= 0:
            break

        batch_count = args.batch_size
        if remaining_budget is not None:
            batch_count = min(batch_count, remaining_budget)

        batch_number += 1
        result = prepare_book_words(
            PrepareJobRequest(
                scope="next",
                count=batch_count,
                maxSensesPerWord=args.max_senses,
                overwriteExisting=args.overwrite_existing,
                bookId=args.book_id,
            )
        )
        total_processed += result.processedWords
        total_ready_cards += result.readyCards
        if remaining_budget is not None:
            remaining_budget -= result.processedWords

        print(
            "batch={batch} processed={processed} ready_cards={ready_cards} "
            "next_sequence={next_sequence}".format(
                batch=batch_number,
                processed=result.processedWords,
                ready_cards=result.readyCards,
                next_sequence=get_book_progress().nextSequenceIndex,
            )
        )

        if result.processedWords == 0:
            break
        if args.sleep:
            time.sleep(args.sleep)

    print(f"total_processed={total_processed}")
    print(f"total_ready_cards={total_ready_cards}")
    print(f"next_sequence={get_book_progress().nextSequenceIndex}")


if __name__ == "__main__":
    main()
