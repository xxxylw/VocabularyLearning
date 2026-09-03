#!/usr/bin/env python3
"""Offline import of Oxford enrichment data for 考研英语红宝书 2027 (book_id=kaoyan-hongbaoshu-2027).

Reuses the exact data shape of the repo's prepare_book_words pipeline
(backend/app/services.py::prepare_book_words + OxfordEnrichmentProvider), but
sources senses/IPA from the pre-scraped progress file instead of live Oxford
requests — same parser, same tables, no new data source.

What it does per book_word (import_status pending/needs_review):
  1. upsert word into `words` (by normalized_text)
  2. skip if the word already has cards (marks book_word ready, idempotent)
  3. create entries + one example per sense (Oxford sense-level examples,
     first example per sense like the online provider) + one card per entry
  4. upsert pronunciation_cache with real dual IPA (uk/us) when available
  5. mark book_words.import_status = 'ready'
  6. write one prepare_jobs record (scope='next', status='completed')

Words with no Oxford page (noData records) fall back to the same fallback
sense the online pipeline produces (FallbackEnrichmentProvider), so behavior
matches running prepare_book_words.py online.

Usage (dry run by default; add --apply to write):
  python import_hongbaoshu_oxford.py \
      --db backend/data/vocabulary.sqlite \
      --progress kaoyan_hongbaoshu_2027_oxford_enrichment.jsonl \
      --book-id kaoyan-hongbaoshu-2027 \
      [--max-senses 5] [--apply] [--report import_report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

DEFAULT_EF = 2.5
FALLBACK_SENSE_LABEL = "general IELTS use"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_word(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_progress(path: Path) -> dict[str, dict]:
    """Latest record per word wins (append-only file, chronological)."""
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records[record["word"]] = record
    return records


def prepared_senses(record: dict | None, max_senses: int) -> list[dict]:
    """Mirror OxfordEnrichmentProvider.prepare + FallbackEnrichmentProvider."""
    senses = (record or {}).get("senses") or []
    if not senses:
        word = (record or {}).get("word", "")
        return [
            {
                "part_of_speech": "word",
                "sense_label": FALLBACK_SENSE_LABEL,
                "definition": f"A learner-friendly IELTS study meaning for '{word}'.",
                "example": None,
                "definition_source": "fallback",
                "example_source": None,
            }
        ]
    prepared = []
    for sense in senses[:max_senses]:
        definition = (sense.get("definition") or "").strip()
        if not definition:
            continue
        examples = sense.get("examples") or []
        example = examples[0].strip() if examples else None
        prepared.append(
            {
                "part_of_speech": (sense.get("partOfSpeech") or "word").strip() or "word",
                "sense_label": definition,
                "definition": definition,
                "example": example,
                "definition_source": "oxford_api",
                "example_source": "oxford_api" if example else None,
            }
        )
    return prepared[:max_senses] or prepared_senses(None, max_senses)


def build_pronunciation_response(record: dict) -> dict:
    """Same shape the Wiktionary pronunciation endpoint returns (pronunciation.py)."""
    ipa_uk = record.get("ipaUk")
    ipa_us = record.get("ipaUs")
    source_url = record.get("sourceUrl") or ""
    audio_file = record.get("audioFileName")
    return {
        "word": record["word"],
        "ipa": ipa_us or ipa_uk,
        "ipaUk": ipa_uk,
        "ipaUs": ipa_us,
        "audioUrl": None if not audio_file else f"https://audio.{audio_file}",
        "sourceUrl": source_url,
        "audioSourceUrl": None,
        "attribution": None,
        "license": None,
        "licenseUrl": None,
        "status": "ready" if (ipa_uk or ipa_us or audio_file) else "unavailable",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to vocabulary.sqlite")
    parser.add_argument("--progress", required=True, help="Oxford progress jsonl (6548 words)")
    parser.add_argument("--book-id", default="kaoyan-hongbaoshu-2027")
    parser.add_argument("--max-senses", type=int, default=5)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    parser.add_argument("--report", help="Write a JSON report to this path")
    args = parser.parse_args()

    records = load_progress(Path(args.progress))
    print(
        f"progress: {len(records)} words; book={args.book_id}; "
        f"mode={'apply' if args.apply else 'dry-run'}"
    )

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")

    book_row = connection.execute(
        "select id from vocabulary_books where id = ?", (args.book_id,)
    ).fetchone()
    if book_row is None:
        print(f"ERROR: book {args.book_id} not found in vocabulary_books", file=sys.stderr)
        connection.rollback()
        return 1

    book_words = connection.execute(
        """
        select id, word_text, normalized_text
        from book_words
        where import_status in ('pending', 'needs_review') and book_id = ?
        order by sequence_index
        """,
        (args.book_id,),
    ).fetchall()

    now = utc_now()
    today = date.today().isoformat()
    job_id = str(uuid4())

    stats = {
        "mode": "apply" if args.apply else "dry-run",
        "book_id": args.book_id,
        "total_book_words": len(book_words),
        "processed_words": 0,
        "ready_cards": 0,
        "entries_created": 0,
        "examples_created": 0,
        "pronunciation_written": 0,
        "already_had_cards": 0,
        "fallback_words": [],
        "missing_from_progress": [],
    }

    for book_word in book_words:
        word_text = book_word["word_text"]
        normalized_text = book_word["normalized_text"] or normalize_word(word_text)

        existing = connection.execute(
            "select id from words where normalized_text = ?", (normalized_text,)
        ).fetchone()
        if existing is not None:
            word_id = existing["id"]
        else:
            word_id = str(uuid4())
            connection.execute(
                "insert into words (id, text, normalized_text, created_at, updated_at)"
                " values (?, ?, ?, ?, ?)",
                (word_id, word_text, normalized_text, now, now),
            )

        card_count = connection.execute(
            "select count(*) from cards where entry_id in"
            " (select id from entries where word_id = ?)",
            (word_id,),
        ).fetchone()[0]
        if card_count > 0:
            connection.execute(
                "update book_words set import_status = 'ready', updated_at = ? where id = ?",
                (now, book_word["id"]),
            )
            stats["already_had_cards"] += 1
            stats["processed_words"] += 1
            continue

        record = records.get(normalized_text)
        if record is None:
            stats["missing_from_progress"].append(normalized_text)
        senses = prepared_senses(record, args.max_senses)
        if record is None or not (record.get("senses") or []):
            stats["fallback_words"].append(normalized_text)

        for sense_order, sense in enumerate(senses, start=1):
            entry_id = str(uuid4())
            connection.execute(
                """
                insert into entries (
                    id, word_id, sense_order, part_of_speech, sense_label,
                    definition, definition_source, chinese_note, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    word_id,
                    sense_order,
                    sense["part_of_speech"],
                    sense["sense_label"],
                    sense["definition"],
                    sense["definition_source"],
                    None,
                    now,
                    now,
                ),
            )
            stats["entries_created"] += 1

            if sense["example"]:
                connection.execute(
                    """
                    insert into entry_examples (
                        id, entry_id, example_order, sentence, source, is_primary,
                        created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        entry_id,
                        1,
                        sense["example"],
                        sense["example_source"] or "fallback",
                        1,
                        now,
                        now,
                    ),
                )
                stats["examples_created"] += 1

            connection.execute(
                """
                insert into cards (
                    id, entry_id, status, stage, due_at, created_on,
                    last_reviewed_at, ef, interval_days
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    entry_id,
                    "learning",
                    0,
                    today,
                    today,
                    None,
                    DEFAULT_EF,
                    0,
                ),
            )
            stats["ready_cards"] += 1

        # pronunciation cache — only when real IPA exists, so the backend's
        # Wiktionary fallback can still serve words without Oxford IPA.
        if record is not None:
            response = build_pronunciation_response(record)
            if response.get("ipaUk") or response.get("ipaUs") or response.get("audioUrl"):
                connection.execute(
                    "insert into pronunciation_cache"
                    " (normalized_word, response_json, status, retry_after, cached_at)"
                    " values (?, ?, 'ready', NULL, ?)"
                    " on conflict(normalized_word) do update set"
                    " response_json=excluded.response_json, status=excluded.status,"
                    " retry_after=NULL, cached_at=excluded.cached_at",
                    (normalized_text, json.dumps(response, ensure_ascii=False), now),
                )
                stats["pronunciation_written"] += 1

        connection.execute(
            "update book_words set import_status = 'ready', updated_at = ? where id = ?",
            (now, book_word["id"]),
        )
        stats["processed_words"] += 1

    connection.execute(
        """
        insert into prepare_jobs (
            id, scope, status, total_words, processed_words, ready_cards,
            needs_review, failed_words_json, created_at, updated_at
        ) values (?, 'next', 'completed', ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            job_id,
            stats["total_book_words"],
            stats["processed_words"],
            stats["ready_cards"],
            json.dumps([]),
            now,
            now,
        ),
    )

    if args.apply:
        connection.commit()
        print("APPLY committed")
    else:
        connection.rollback()
        print("DRY RUN rolled back (pass --apply to write)")

    print(json.dumps({k: v for k, v in stats.items() if k != "fallback_words"}, ensure_ascii=False))
    print(f"fallback words ({len(stats['fallback_words'])}):", " ".join(stats["fallback_words"][:50]))
    if stats["missing_from_progress"]:
        print(f"missing from progress ({len(stats['missing_from_progress'])}):",
              " ".join(stats["missing_from_progress"][:50]))

    if args.report:
        Path(args.report).write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
