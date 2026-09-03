#!/usr/bin/env python3
"""Build the 考研英语红宝书 (Kaoyan English Red Book) word-list CSV.

PRD ch.10 (内置第二本词书): the second built-in book's word list must come
from a publicly available word list and enter the existing book_words import
pipeline as "one word per line, with an optional layer annotation column"
(一行一词，可带分层标注列). This script turns the public source into that
normalized CSV:

    sequence_index,word,layer

Source (公开词表整理版):
    https://github.com/3056810551/2027-kaoyan-english-redbook-json
    - words.json                — 6,550 entries: {page, index, word, meaning}
    - category_page_assign.json — same entries with the book section/unit
                                  (必考词UnitN / 基础词UnitN / 简单基础词 /
                                  超纲词) instead of the raw PDF page.

Notes:
- Only the word and its layer are kept. Dictionary definitions are NOT
  imported from this source: per PRD ch.10 the enrichment (English senses,
  IPA, examples) must come from the existing Oxford pipeline only, and no
  new copyright-risk source beyond Oxford may be introduced.
- Duplicate words (case-insensitive, whitespace-normalized) keep their
  first occurrence in book order; e.g. "march"/"March" and a repeated
  "passerby" across sections collapse to one row.
- Output is deterministic: same input files → identical CSV.

Usage:
    python scripts/build_hongbaoshu_wordlist.py \
        --words-json /path/words.json \
        --category-json /path/category_page_assign.json \
        --output data/wordlists/kaoyan_hongbaoshu_2027.csv

    Omit --words-json/--category-json to auto-download from the public repo.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

WORDS_URL = (
    "https://raw.githubusercontent.com/3056810551/"
    "2027-kaoyan-english-redbook-json/main/words.json"
)
CATEGORY_URL = (
    "https://raw.githubusercontent.com/3056810551/"
    "2027-kaoyan-english-redbook-json/main/category_page_assign.json"
)

LAYER_PATTERNS: list[tuple[str, str]] = [
    (r"^必考词", "必考词"),
    (r"^简单基础词", "简单基础词"),
    (r"^基础词", "基础词"),
    (r"^超纲词", "超纲词"),
]


def normalize_word(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def layer_for(section: str) -> str:
    for pattern, layer in LAYER_PATTERNS:
        if re.match(pattern, section):
            return layer
    raise ValueError(f"Unknown book section: {section!r}")


def load_json(path: Path | None, url: str) -> list[dict]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def build_rows(category_entries: list[dict]) -> list[tuple[str, str]]:
    """Return ordered (word, layer) rows, deduplicated in book order."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in category_entries:
        word = (entry.get("word") or "").strip()
        if not word:
            continue
        normalized = normalize_word(word)
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append((word, layer_for(entry["page"])))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--words-json", type=Path, default=None)
    parser.add_argument("--category-json", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/wordlists/kaoyan_hongbaoshu_2027.csv"),
    )
    args = parser.parse_args()

    # The category file carries the same entries as words.json plus the
    # section annotation, so it is the authoritative input; words.json is
    # loaded only to cross-check the entry count of the public source.
    category_entries = load_json(args.category_json, CATEGORY_URL)
    words_entries = load_json(args.words_json, WORDS_URL)
    if args.words_json is None and args.category_json is None and len(words_entries) not in (
        len(category_entries),
        len(category_entries) - 1,
    ):
        print(
            f"warning: source entry counts differ: words.json={len(words_entries)} "
            f"category_page_assign.json={len(category_entries)}",
            file=sys.stderr,
        )

    rows = build_rows(category_entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("sequence_index,word,layer\n")
        for sequence_index, (word, layer) in enumerate(rows, start=1):
            handle.write(f'{sequence_index},"{word}","{layer}"\n')

    layer_counts: dict[str, int] = {}
    for _word, layer in rows:
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
    print(f"total_words={len(rows)}")
    for layer in ("必考词", "基础词", "简单基础词", "超纲词"):
        print(f"layer_{layer}={layer_counts.get(layer, 0)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
