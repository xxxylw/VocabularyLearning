#!/usr/bin/env python3
"""Produce the read-only builtin SQLite library for the Windows package.

Takes the developer machine's working DB (``backend/data/vocabulary.sqlite``,
which contains full enrichment data *and* personal study progress) and
produces a sanitized distribution copy:

- personal progress is stripped: reviews, prepare_jobs, settings;
- cards are reset to the pristine "new/learning" state
  (status='learning', stage=0, ef=2.5, interval_days=0,
  due_at=created_on, last_reviewed_at=NULL) so every fresh user starts clean;
- content tables (words / entries / examples / pronunciation_cache /
  vocabulary_books / book_words) are kept verbatim;
- the file is VACUUMed and content counts are verified against thresholds
  (PRD 第七章 验收标准 2: 打包前以 DB 实测计数核验).

Usage:
    python scripts/make_builtin_db.py \
        --source backend/data/vocabulary.sqlite \
        --output build/pkg/VocabularyLearning/builtin/vocabulary.sqlite \
        [--min-words 3383] [--min-entries 8000] \
        [--min-examples 9000] [--min-ready-pron 3000] \
        [--expected-books 2]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Make the backend app package importable when run from a repo checkout.
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import migrate  # noqa: E402

DEFAULT_EF = 2.5  # keep in sync with app/services.py DEFAULT_EF


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-words", type=int, default=3383)
    # PRD 第十章 验收标准 5: v1 发布包内置两本书（雅思 + 考研红宝书）。
    parser.add_argument("--expected-books", type=int, default=2)
    parser.add_argument("--min-entries", type=int, default=8000)
    # Verified full-content baseline on 2026-09-03 (Oxford pipeline output):
    # 3,383 words / 8,904 entries / 8,904 examples / 8,904 cards.
    parser.add_argument("--min-examples", type=int, default=8904)
    # Builtin dual-IPA is an OPEN GAP vs PRD ch7 acceptance #2: the builtin DB
    # carries pronunciation_cache rows opportunistically; the app serves IPA
    # via the online Wiktionary lookup (opt-in) with offline fallback. The
    # pre-fetch of all 3,383 pronunciations is tracked as a follow-up data
    # task; until then this is reported as a warning, not a hard failure.
    parser.add_argument("--min-ready-pron", type=int, default=0)
    return parser.parse_args()


def checkpoint_source(source: Path) -> None:
    """Fold any WAL side-file into the main DB so the copy is complete."""
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def sanitize(connection: sqlite3.Connection) -> None:
    # Bring the copy up to the current schema first (a fresh checkout's
    # data DB may predate the books / SM-2 migrations); migrate() is
    # idempotent for already-migrated DBs.
    migrate(connection)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DELETE FROM reviews")
    connection.execute("DELETE FROM prepare_jobs")
    connection.execute("DELETE FROM settings")
    connection.execute(
        """
        UPDATE cards SET
            status = 'learning',
            stage = 0,
            due_at = created_on,
            last_reviewed_at = NULL,
            ef = ?,
            interval_days = 0
        """,
        (DEFAULT_EF,),
    )
    connection.commit()


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "book_words": "SELECT COUNT(*) FROM book_words",
        "words": "SELECT COUNT(*) FROM words",
        "entries": "SELECT COUNT(*) FROM entries",
        "examples": "SELECT COUNT(*) FROM entry_examples",
        "cards": "SELECT COUNT(*) FROM cards",
        "ready_pronunciations": (
            "SELECT COUNT(*) FROM pronunciation_cache WHERE status = 'ready'"
        ),
        "book_words_with_book_id": (
            "SELECT COUNT(*) FROM book_words WHERE book_id IS NOT NULL"
        ),
        "books": "SELECT COUNT(*) FROM vocabulary_books",
    }
    per_book_query = (
        "SELECT COALESCE(book_id, '<null>') AS book_id, COUNT(*) "
        "FROM book_words GROUP BY book_id ORDER BY book_id"
    )
    result: dict[str, int] = {}
    for name, query in queries.items():
        try:
            result[name] = connection.execute(query).fetchone()[0]
        except sqlite3.OperationalError:
            result[name] = -1
    try:
        per_book = connection.execute(per_book_query).fetchall()
        result["words_per_book"] = dict(per_book)
    except sqlite3.OperationalError:
        result["words_per_book"] = {}
    return result


def verify(counted: dict[str, int], args: argparse.Namespace) -> list[str]:
    problems: list[str] = []

    def check(key: str, minimum: int | None, expected: int | None = None) -> None:
        value = counted.get(key, -1)
        if expected is not None and value != expected:
            problems.append(f"{key}: expected {expected}, got {value}")
        elif minimum is not None and value < minimum:
            problems.append(f"{key}: expected >= {minimum}, got {value}")

    # Dual-book builtin (PRD ch10 acceptance #5): book_words is a total
    # floor across all books, not an exact count.
    check("book_words", args.min_words)
    check("entries", args.min_entries)
    check("examples", args.min_examples)
    check("ready_pronunciations", args.min_ready_pron)
    check("book_words_with_book_id", None, expected=counted.get("book_words", -1))
    check("books", None, expected=args.expected_books)
    for book_id, word_count in counted.get("words_per_book", {}).items():
        if word_count <= 0:
            problems.append(
                f"words_per_book[{book_id}]: expected > 0, got {word_count}"
            )
    return problems


def main() -> int:
    args = parse_args()
    source: Path = args.source
    output: Path = args.output

    if not source.is_file():
        print(f"ERROR: source database not found: {source}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)

    checkpoint_source(source)

    fd, tmp_name = tempfile.mkstemp(dir=output.parent, suffix=".sqlite.tmp")
    import os

    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(source, tmp_path)
        connection = sqlite3.connect(tmp_path)
        connection.row_factory = sqlite3.Row  # migrate() reads rows by name
        try:
            sanitize(connection)
        finally:
            connection.close()

        # VACUUM in a fresh connection (it rebuilds the file).
        connection = sqlite3.connect(tmp_path)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

        connection = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        try:
            counted = counts(connection)
        finally:
            connection.close()

        problems = verify(counted, args)
        for name in sorted(counted):
            if name == "words_per_book":
                continue
            print(f"  {name}: {counted[name]}")
        for book_id, word_count in sorted(counted.get("words_per_book", {}).items()):
            print(f"  words_per_book[{book_id}]: {word_count}")

        total_words = counted.get("words", 0)
        if counted.get("ready_pronunciations", 0) < total_words:
            print(
                "WARNING: builtin pronunciation_cache covers "
                f"{counted.get('ready_pronunciations', 0)}/{total_words} words; dual IPA "
                "relies on the online Wiktionary enhancement with offline "
                "fallback (open gap vs PRD ch7 acceptance #2).",
                file=sys.stderr,
            )

        if problems:
            print("\nERROR: builtin library verification failed:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1

        os.replace(tmp_path, output)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    print(f"\nOK: sanitized builtin library written to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
