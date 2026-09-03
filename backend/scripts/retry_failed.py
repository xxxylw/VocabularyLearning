#!/usr/bin/env python3
"""Retry pass for words that failed in the main scrape.

Root cause found: Oxford uses numbered-homonym URLs for words with multiple
entries (e.g. /definition/english/can1 -- the plain /can returns 404). The
main scraper's candidate list (plurals, -ed/-ing forms) never tried those.

This script re-attempts every word whose latest progress record is "failed",
trying in order: word, word1, word2, word3, plus the original candidate
derivations. Successful lookups append a fresh "ok" record to
oxford_progress.jsonl (the importer keeps the latest record per word, and
load_done_words skips any word already present, so this is safe).

Words that still fail get no new record -- their existing failed record
stands and the frontend simply renders no IPA / no examples for them.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from scrape_oxford import (
    BASE,
    PROGRESS_FILE,
    fetch_oxford_html,
    lookup_word_candidates,
    normalize_lookup_word,
    oxford_definition_url,
    parse_oxford_page,
)

WORDLIST_FILE = BASE / "wordlist.json"


def latest_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    with PROGRESS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            records[record["word"]] = record
    return records


# Known British/phrase equivalences not derivable by simple rules.
VARIANT_MAP = {
    "aluminum": "aluminium",
    "percent": "per-cent",
    "layoff": "lay-off",
    "okay": "ok",
    "carcase": "carcass",
    # hyphenation/spelling variants with identical pronunciation (verified per-word)
    "geographic": "geographical",
    "homegrown": "home-grown",
    "newsstand": "news-stand",
    "nonverbal": "non-verbal",
    "prewar": "pre-war",
    "tagline": "tag-line",
}

# American bases whose derived forms may also need or->our (behavioral -> behavioural)
AMERICAN_BASES = [
    "behavior", "color", "favor", "flavor", "honor", "humor",
    "labor", "neighbor", "odor", "rumor", "vigor",
]


def retry_word(word: str) -> dict | None:
    normalized = normalize_lookup_word(word)
    numbered = [f"{normalized}{n}" for n in (1, 2, 3)] + [
        f"{normalized}_{n}" for n in (1, 2, 3)
    ]
    if normalized.endswith("ll") and len(normalized) > 4:
        british = normalized[:-1]
        numbered += [british, f"{british}1", f"{british}_1"]
    if normalized.endswith("or") and len(normalized) >= 4:
        british = normalized[:-2] + "our"  # harbor -> harbour, odor -> odour
        numbered += [british, f"{british}_1"]
    if normalized.endswith("er") and len(normalized) > 4:
        british = normalized[:-2] + "re"  # fiber -> fibre
        numbered += [british, f"{british}_1"]
    if normalized.endswith("yze") and len(normalized) > 4:
        british = normalized[:-3] + "yse"  # analyze -> analyse
        numbered += [british, f"{british}_1"]
    if normalized.endswith("by") and len(normalized) > 5:
        hyphenated = normalized[:-2] + "-by"  # passerby -> passer-by
        numbered += [hyphenated, f"{hyphenated}_1"]
    if "ll" in normalized and not normalized.endswith("ll"):
        demoted = normalized.replace("ll", "l", 1)  # installment -> instalment
        numbered += [demoted, f"{demoted}_1"]
    if "our" in normalized:
        american = normalized.replace("our", "or", 1)  # humourous -> humorous
        numbered += [american, f"{american}_1"]
    numbered += [f"{normalized}-to"]  # ought -> ought to (phrase headword)
    numbered += [f"{normalized}-of"]  # irrespective -> irrespective of (phrase headword)
    if "'" in normalized:
        hyphenated = normalized.replace("'", "-")  # o'clock -> o-clock
        numbered += [hyphenated, f"{hyphenated}_1"]
    for base in AMERICAN_BASES:
        if base in normalized:
            british = normalized.replace(base, base[:-2] + "our", 1)
            numbered += [british, f"{british}_1"]
    if "-" in normalized and not normalized.endswith("-"):
        joined = normalized.replace("-", "")  # e-mail -> email
        numbered += [joined, f"{joined}_1"]
    variant = VARIANT_MAP.get(normalized)
    if variant:
        numbered += [variant, f"{variant}_1"]
    plain_candidates = lookup_word_candidates(normalized)
    candidates = [normalized] + [c for c in numbered if c not in plain_candidates]
    candidates += [c for c in plain_candidates[1:] if c not in candidates]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            html = fetch_oxford_html(candidate)
        except Exception as error:  # noqa: BLE001 - record and move on
            last_error = error
            continue
        result = parse_oxford_page(candidate, html)
        result["word"] = normalized
        if result["senses"] or result["ipaUk"] or result["ipaUs"]:
            return {"word": normalized, "status": "ok", **result}
    if last_error is not None:
        print(f"STILL_FAILED {word}: {type(last_error).__name__}: {last_error}")
    else:
        print(f"STILL_FAILED {word}: no usable page among candidates")
    return None


def main() -> None:
    records = latest_records()
    failed_words = sorted(w for w, r in records.items() if r.get("status") != "ok")
    print(f"retrying {len(failed_words)} failed words: {failed_words}")

    recovered = 0
    for word in failed_words:
        record = retry_word(word)
        if record is None:
            continue
        record["fetchedAt"] = datetime.now(timezone.utc).isoformat()
        record["retriedVia"] = "numbered_homonym_candidates"
        with PROGRESS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        recovered += 1
        print(f"RECOVERED {word} (ipaUk={record.get('ipaUk')} ipaUs={record.get('ipaUs')} senses={len(record.get('senses', []))})")
        time.sleep(1.2 + random.uniform(0.0, 0.4))

    print(f"DONE recovered={recovered}/{len(failed_words)}")


if __name__ == "__main__":
    import sys

    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for i in range(passes):
        main()
        if i < passes - 1:
            print("--- pause before next pass ---")
            time.sleep(20)
