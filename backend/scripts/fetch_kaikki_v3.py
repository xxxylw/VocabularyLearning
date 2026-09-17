#!/usr/bin/env python3
"""Fetch per-word kaikki (English Wiktionary) jsonl records for v3 build.

Reads  kaikki_fetch_targets.txt  (one word per line)
Writes  <out>/shards/<idx>.json   per-word record {"word","status","entries"}
        <out>/progress.jsonl      append-only progress ledger

Resumable: skips words already recorded in progress.jsonl.
"""
from __future__ import annotations
import json, os, sys, threading, time
from queue import Queue
from urllib.parse import quote

import httpx

WORKDIR = "/tmp/vl_t7686438212851502322"
TARGETS = os.path.join(WORKDIR, "kaikki_fetch_targets.txt")
OUT = os.path.join(WORKDIR, "kaikki_v3")
PROGRESS = os.path.join(OUT, "progress.jsonl")
BASE = "https://kaikki.org/dictionary/English/meaning/{}/{}/{}.jsonl"
WORKERS = 6
TIME_LIMIT = 2700


def urls_for(word: str) -> list[str]:
    fname = quote(word.replace(" ", "%20"), safe=".'-")
    cands = [BASE.format(word[:1], word[:2], fname)]
    stripped = "".join(c for c in word[:2] if c.isalnum())
    if stripped and stripped != word[:2]:
        cands.append(BASE.format(word[:1], stripped, fname))
    return cands


def fetch_word(client: httpx.Client, word: str) -> tuple[str, list]:
    """returns (status, entries); status in ok|not_found|error"""
    last_code = None
    for url in urls_for(word):
        try:
            r = client.get(url)
        except (httpx.TimeoutException, httpx.TransportError):
            return "error", []
        if r.status_code == 429:
            time.sleep(20)
            return "error", []
        last_code = r.status_code
        if r.status_code == 200:
            entries = []
            for line in r.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("lang_code") in (None, "en"):
                    entries.append(rec)
            return "ok", entries
    if last_code == 404:
        return "not_found", []
    return "error", []


def main() -> None:
    os.makedirs(os.path.join(OUT, "shards"), exist_ok=True)
    with open(TARGETS, encoding="utf-8") as f:
        targets = [l.strip() for l in f if l.strip()]
    done: set[str] = set()
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["word"])
                except Exception:
                    pass
    todo = [(i, w) for i, w in enumerate(targets) if w not in done]
    print(f"targets={len(targets)} done={len(done)} todo={len(todo)}", flush=True)
    if not todo:
        print("ALL_DONE", flush=True)
        return

    lock = threading.Lock()
    pfile = open(PROGRESS, "a", encoding="utf-8")
    stats = {"ok": 0, "not_found": 0, "error": 0}
    t0 = time.time()
    q: Queue = Queue()
    for item in todo:
        q.put(item)

    def worker() -> None:
        client = httpx.Client(timeout=30, follow_redirects=True,
                              headers={"User-Agent": "VocabularyLearning-dict-build/3.0"})
        while True:
            try:
                idx, word = q.get_nowait()
            except Exception:
                return
            status, entries = "error", []
            for attempt in range(3):
                status, entries = fetch_word(client, word)
                if status != "error":
                    break
                time.sleep(3 + attempt * 4)
            payload = {"word": word, "status": status, "entries": entries}
            shard_path = os.path.join(OUT, "shards", f"{idx}.json")
            tmp = shard_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, shard_path)
            with lock:
                pfile.write(json.dumps({"word": word, "idx": idx, "status": status}) + "\n")
                pfile.flush()
                stats[status] += 1
                n = sum(stats.values())
                if n % 50 == 0:
                    print(f"progress {n}/{len(todo)} {stats}", flush=True)
                if time.time() - t0 > TIME_LIMIT:
                    q.queue.clear()
            q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pfile.close()
    remaining = len(todo) - sum(stats.values())
    print(f"RUN_END done_this_run={sum(stats.values())} {stats} remaining={remaining}", flush=True)
    if remaining == 0:
        print("ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
