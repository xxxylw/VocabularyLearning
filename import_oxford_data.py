"""Import Oxford scrape results into the VocabularyLearning SQLite database.

Reads oxford_progress.jsonl (one JSON record per word, produced by
scrape_oxford.py) and applies:

1. pronunciation_cache — upserts a "ready" row per successfully scraped word
   with real UK/US IPA and the Oxford audio URL (PRD decision 1).
2. entry_examples — replaces the existing examples for each scraped word's
   entries with the real Oxford sense-level examples (PRD decision 2). Senses
   are aligned to entries by order (same parser/page as the original lookup)
   and cross-checked by definition text; words that cannot be aligned are
   skipped and reported.
3. Failed words — template ("fallback") examples are deleted so no made-up
   example text remains; no pronunciation row is written.

The script is idempotent and safe to re-run as scraping progresses.
By default it runs in dry-run mode; pass --apply to write.

Usage:
    python import_oxford_data.py --db path/to/vocabulary.sqlite \
        --progress oxford_progress.jsonl [--apply] [--report report.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

PRON_SOURCE = "oxford_import"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_definition(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return " ".join(text.split()).strip().lower()


def load_progress(path: Path) -> dict[str, dict]:
    """Latest record per word wins."""
    records: dict[str, dict] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records[record["word"]] = record
    return records


def build_pronunciation_response(record: dict) -> dict:
    ipa_uk = record.get("ipaUk")
    ipa_us = record.get("ipaUs")
    audio_us = record.get("audioUs")
    audio_uk = record.get("audioUk")
    response = {
        "word": record["word"],
        "ipa": ipa_us or ipa_uk,
        "ipaUk": ipa_uk,
        "ipaUs": ipa_us,
        "audioUrl": audio_us or audio_uk,
        "sourceUrl": record.get(
            "sourceUrl",
            "https://www.oxfordlearnersdictionaries.com/definition/english/"
            + record["word"],
        ),
        "status": "ready",
    }
    return {key: value for key, value in response.items() if value is not None}


def align_senses(entries: list[dict], senses: list[dict]) -> tuple[list[tuple[dict, dict]], list[str]]:
    """Return (matched pairs of (entry, sense), notes)."""
    notes: list[str] = []
    if not entries:
        return [], ["no entries in database"]
    if not senses:
        return [], ["no senses scraped"]

    if len(entries) == len(senses):
        pairs = list(zip(entries, senses))
        mismatches = sum(
            1
            for entry, sense in pairs
            if normalize_definition(entry["definition"])
            != normalize_definition(sense["definition"])
        )
        if mismatches:
            notes.append(f"order-aligned but {mismatches} definition text mismatch(es)")
        return pairs, notes

    # Lengths differ: fall back to definition text matching.
    by_definition: dict[str, list[dict]] = {}
    for sense in senses:
        by_definition.setdefault(normalize_definition(sense["definition"]), []).append(sense)
    pairs: list[tuple[dict, dict]] = []
    unmatched: list[dict] = []
    for entry in entries:
        candidates = by_definition.get(normalize_definition(entry["definition"]))
        if candidates:
            pairs.append((entry, candidates.pop(0)))
        else:
            unmatched.append(entry)
    if unmatched or any(candidates for candidates in by_definition.values()):
        notes.append(
            f"count mismatch ({len(entries)} entries vs {len(senses)} senses); "
            f"matched {len(pairs)} by definition text, skipped {len(unmatched)} entries"
        )
        return [], notes
    return pairs, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to vocabulary.sqlite")
    parser.add_argument("--progress", default="oxford_progress.jsonl")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    parser.add_argument("--report", help="Write a JSON report to this path")
    args = parser.parse_args()

    records = load_progress(Path(args.progress))
    ok_words = sorted(w for w, r in records.items() if r.get("status") == "ok")
    failed_words = sorted(w for w, r in records.items() if r.get("status") != "ok")
    print(
        f"progress: {len(ok_words)} ok words, {len(failed_words)} failed words; "
        f"mode={'apply' if args.apply else 'dry-run'}"
    )

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")

    stats = {
        "pronunciation_written": 0,
        "words_with_examples_replaced": 0,
        "examples_inserted": 0,
        "template_examples_deleted": 0,
        "failed_words_degraded": 0,
        "words_skipped_alignment": [],
        "words_left_fallback_definitions": [],
        "notes": [],
    }

    now = utc_now()

    for word in ok_words:
        record = records[word]
        row = connection.execute(
            "SELECT id FROM words WHERE normalized_text = ?", (word.lower(),)
        ).fetchone()
        if row is None:
            stats["notes"].append(f"{word}: not found in words table, skipped")
            continue
        entries = [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM entries WHERE word_id = ? ORDER BY sense_order", (row["id"],)
            )
        ]

        # 1) pronunciation cache — only when real data exists; otherwise skip so
        # the backend's Wiktionary fallback can still serve the word later.
        response = build_pronunciation_response(record)
        if response.get("ipaUk") or response.get("ipaUs") or response.get("audioUrl"):
            connection.execute(
                "INSERT INTO pronunciation_cache (normalized_word, response_json, status, retry_after, cached_at) "
                "VALUES (?, ?, 'ready', NULL, ?) "
                "ON CONFLICT(normalized_word) DO UPDATE SET response_json=excluded.response_json, "
                "status=excluded.status, retry_after=NULL, cached_at=excluded.cached_at",
                (word.lower(), json.dumps(response, ensure_ascii=False), now),
            )
            stats["pronunciation_written"] += 1

        # 2) examples per sense
        pairs, notes = align_senses(entries, record.get("senses", []))
        for note in notes:
            stats["notes"].append(f"{word}: {note}")
        if not pairs:
            stats["words_skipped_alignment"].append(word)
            # still drop template examples so no made-up text remains
            for entry in entries:
                deleted = connection.execute(
                    "DELETE FROM entry_examples WHERE entry_id = ? AND source IN ('template', 'fallback')",
                    (entry["id"],),
                ).rowcount
                stats["template_examples_deleted"] += max(deleted, 0)
            continue

        any_fallback = any(e["definition_source"] == "fallback" for e, _ in pairs)
        if any_fallback:
            stats["words_left_fallback_definitions"].append(word)

        replaced_entries = 0
        for entry, sense in pairs:
            connection.execute(
                "DELETE FROM entry_examples WHERE entry_id = ? AND source != 'manual'",
                (entry["id"],),
            )
            for order, sentence in enumerate(sense.get("examples", [])[:2]):
                connection.execute(
                    "INSERT INTO entry_examples (id, entry_id, example_order, sentence, source, is_primary, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'oxford_api', ?, ?, ?)",
                    (
                        f"oxford-{entry['id']}-{order}",
                        entry["id"],
                        order,
                        sentence,
                        1 if order == 0 else 0,
                        now,
                        now,
                    ),
                )
                stats["examples_inserted"] += 1
            replaced_entries += 1
        if replaced_entries:
            stats["words_with_examples_replaced"] += 1

    for word in failed_words:
        row = connection.execute(
            "SELECT id FROM words WHERE normalized_text = ?", (word.lower(),)
        ).fetchone()
        if row is None:
            continue
        deleted = connection.execute(
            "DELETE FROM entry_examples WHERE source IN ('template', 'fallback') AND entry_id IN "
            "(SELECT id FROM entries WHERE word_id = ?)",
            (row["id"],),
        ).rowcount
        stats["template_examples_deleted"] += max(deleted, 0)
        stats["failed_words_degraded"] += 1
        stats["notes"].append(f"{word}: scrape failed — no IPA written, template examples cleared")

    if args.apply:
        connection.commit()
        print("changes committed")
    else:
        connection.rollback()
        print("dry run — no changes written (pass --apply to write)")

    print(json.dumps({k: (v if not isinstance(v, list) else v[:10]) for k, v in stats.items()}, indent=2, ensure_ascii=False))
    if args.report:
        Path(args.report).write_text(
            json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
