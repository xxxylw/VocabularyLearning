#!/usr/bin/env python3
"""Mirror Oxford mp3 audio files for DB words to a local static dir.

Downloads audioUk (fallback audioUs) for every word present both in the DB and
in oxford_progress_cloud.jsonl. Polite: serial with a small delay. Existing
files are skipped (idempotent, resumable). Use --limit for a trial run.

Output file name: <normalized word with unsafe chars -> _>.mp3  (same rule as
build_oxford_content.audio_file_name).
"""
import argparse
import json
import os
import sqlite3
import time
import urllib.request


def audio_file_name(word: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "'-") else "_" for ch in word.strip().lower())
    return f"{safe}.mp3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--oxford-jsonl", default="/home/ubuntu/oxford-data-inputs/oxford_progress_cloud.jsonl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--delay", type=float, default=0.25)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db_words = {r[0].strip().lower() for r in conn.execute("select normalized_text from words")}
    conn.close()

    todo = []
    with open(args.oxford_jsonl, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("status") != "ok" or not d.get("senses"):
                continue
            key = d["word"].strip().lower()
            if key not in db_words:
                continue
            url = d.get("audioUk") or d.get("audioUs")
            if not url:
                continue
            dst = os.path.join(args.out_dir, audio_file_name(key))
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                continue
            todo.append((key, url, dst))

    if args.limit:
        todo = todo[: args.limit]
    print(f"to download: {len(todo)}")
    ok = fail = 0
    failures = []
    for i, (key, url, dst) in enumerate(todo, 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vocab-audio-mirror/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp, open(dst + ".tmp", "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(dst + ".tmp", dst)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            failures.append({"word": key, "url": url, "error": str(exc)[:200]})
            if os.path.exists(dst + ".tmp"):
                os.remove(dst + ".tmp")
        if i % 100 == 0:
            print(f"progress {i}/{len(todo)} ok={ok} fail={fail}", flush=True)
        time.sleep(args.delay)
    print(f"DONE ok={ok} fail={fail} total={len(todo)}")
    if failures:
        with open(os.path.join(args.out_dir, "_failures.json"), "w", encoding="utf-8") as fh:
            json.dump(failures, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
