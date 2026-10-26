#!/usr/bin/env python3
"""P1 fix: normalize unwrapped IPA values in pronunciation_cache.response_json.

Scans every pronunciation_cache row's payload for ipaUk / ipaUs / ipa fields
that are not wrapped in /.../ and repairs them:

  * value uses proper IPA characters, missing only the wrapping slashes
    (e.g. "ˌeɪˈɛm", "sˈeɪk")       -> wrap it: "/ˌeɪˈɛm/"
  * value is legacy ECDICT-style ASCII junk (e.g. "seik", "ә'biliti",
    "'eibi:'si:")                  -> set to null (hidden; a wrong-but-wrapped
                                     transcription would be worse)

Safety:
  * timestamped `cp` backup of the DB is made BEFORE any write;
  * fully idempotent — re-running repairs 0 rows;
  * writes a per-row before/after report (TSV) + summary (JSON);
  * only the three IPA display fields change; audio URLs, source URLs and
    statuses are untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime

DEFAULT_DB = "/home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite"
REPORT_DIR = "/home/ubuntu/deliveries"
FIELDS = ("ipaUk", "ipaUs", "ipa")
JUNK_CHARS = re.compile(r"[ә'\d:;?@\[\]\\^~]")
ASCII_VOWEL_PAIR = re.compile(r"[aeiouAEIOU]{2}")


def is_wrapped(v) -> bool:
    return isinstance(v, str) and v.startswith("/") and v.endswith("/") and len(v) > 2


def is_proper_ipa(v) -> bool:
    """Proper IPA characters, missing only the wrapping slashes."""
    if not isinstance(v, str) or not v:
        return False
    if JUNK_CHARS.search(v):
        return False
    if ASCII_VOWEL_PAIR.search(v):
        return False
    # Must contain at least one real (non-ASCII) IPA character; pure-ASCII
    # transcriptions in this dataset are always ECDICT-style junk.
    return any(ord(ch) > 127 for ch in v)


def fixed_value(v):
    """Returns (new_value, action) with action in {'keep', 'wrap', 'null'}."""
    if v is None or is_wrapped(v):
        return v, "keep"
    if is_proper_ipa(v):
        return f"/{v}/", "wrap"
    return None, "null"


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-ipafix-{ts}"
    shutil.copyfile(db_path, backup_path)
    print(f"backup: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("select normalized_word, response_json from pronunciation_cache").fetchall()

    stats = Counter({"rows_fixed": 0, "wrap": 0, "null": 0})
    report_rows = []
    for r in rows:
        word = r["normalized_word"]
        try:
            payload = json.loads(r["response_json"])
        except Exception:
            continue
        changes = []
        for field in FIELDS:
            if field not in payload:
                continue
            new_v, action = fixed_value(payload[field])
            if new_v != payload[field]:
                changes.append(f"{field}: {payload[field]!r} -> {new_v!r} ({action})")
                payload[field] = new_v
                stats[action] += 1
        if not changes:
            continue
        conn.execute(
            "update pronunciation_cache set response_json=? where normalized_word=?",
            (json.dumps(payload, ensure_ascii=False), word),
        )
        stats["rows_fixed"] += 1
        report_rows.append((word, " | ".join(changes)))

    conn.commit()
    print(f"rows fixed: {stats['rows_fixed']} (wrap {stats['wrap']}, null {stats['null']})")

    report_dir = os.path.join(REPORT_DIR, f"ipa-fix-{ts}")
    os.makedirs(report_dir, exist_ok=True)
    tsv_path = os.path.join(report_dir, "fixed-rows.tsv")
    with open(tsv_path, "w") as fh:
        fh.write("word\tchanges\n")
        for word, changes in report_rows:
            fh.write(f"{word}\t{changes}\n")
    summary = {
        "timestamp": ts,
        "db": db_path,
        "backup": backup_path,
        "rows_fixed": stats["rows_fixed"],
        "field_wrap": stats["wrap"],
        "field_null": stats["null"],
        "report": tsv_path,
    }
    summary_path = os.path.join(report_dir, "summary.json")
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
