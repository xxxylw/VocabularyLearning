#!/usr/bin/env python3
"""Switch the vocabulary DB content zone between the Wiktionary variant and the
Oxford->WordNet->Wiktionary variant.

Direction ``oxford`` rewrites content tables in place (entries / entry_examples /
pronunciation_cache / vocabulary_books) with this per-word priority:
  1. Oxford (oxford_progress_cloud.jsonl): senses, examples, UK/US IPA, audio.
  2. WordNet 3.0 (raw index.*/data.* files): definitions + examples.
     Pronunciation rows are left untouched (WordNet has no IPA/audio).
  3. Wiktionary: rows are left untouched.

Direction ``wiktionary`` restores the four content tables verbatim from the
pre-oxford backup DB.

Invariants honored:
  - User-data tables (users/cards/reviews/sessions/subscriptions/orders/
    settings/today_queue ...) are never written.
  - entry ids are never deleted in the oxford direction: existing entries are
    updated in place so cards.entry_id keeps resolving. Extra Oxford/WordNet
    senses get fresh UUID ids. Leftover entries (existing count > new sense
    count) keep their original Wiktionary content untouched — per-sense
    fallback, so every card's entry keeps resolving with honest content.
  - Writes commit in small batches (per ~25 words) so a live app's writers
    are only blocked briefly.
  - definition_source / example source use the schema CHECK vocabulary:
    oxford -> 'oxford_api', wordnet -> 'imported', wiktionary -> 'open_api'
    (frontend never renders these).

A timestamped backup copy of the target DB is always made before writing
(unless --no-backup). Use --apply to write; default is dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

CONTENT_TABLES = ("entries", "entry_examples", "pronunciation_cache", "vocabulary_books")

BOOK_TITLES_EN = {
    "cet4-shanguo-2026": "CET-4 Vocabulary Sprint",
    "cet6-dagang-2026": "CET-6 Syllabus Vocabulary",
    "cet6-shanguo-2026": "CET-6 Vocabulary Sprint",
    "default-book": "IELTS Vocabulary Classic",
    "gaokao-kechengbiao-2026": "High School Curriculum Vocabulary 3500",
    "gre-3000": "GRE Core Vocabulary 3000",
    "ielts-advanced-2026": "IELTS Advanced Vocabulary",
    "kaoyan-hongbaoshu-2027": "Kaoyan English Red Book",
    "kaoyan-shanguo-2027": "Kaoyan Vocabulary Sprint",
    "tem4-dagang-2026": "TEM-4 Syllabus Vocabulary",
    "tem4-ruyudeshui-2026": "Like Fish in Water: TEM-4",
    "tem8-ruyudeshui-2026": "Like Fish in Water: TEM-8",
    "toefl-zhenjing-2026": "TOEFL Vocabulary Classic",
}

WORDNET_MAX_SENSES = 8
WORDNET_MAX_EXAMPLES_PER_SENSE = 2
OXFORD_MAX_EXAMPLES_PER_SENSE = 3


def now_iso(conn) -> str:
    row = conn.execute("select created_at from entries limit 1").fetchone()
    sample = row[0] if row else None
    ts = datetime.now(timezone.utc)
    if sample and "T" in sample:
        return ts.isoformat(timespec="seconds").replace("+00:00", "Z") if sample.endswith("Z") else ts.isoformat(timespec="seconds")
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- wordnet ----
class WordNet:
    POS_FILES = (("noun", "n"), ("verb", "v"), ("adj", "a"), ("adv", "r"))

    def __init__(self, wndir: str):
        self.wndir = wndir
        self.index = {}  # lemma -> list of (pos, offset)
        for name, pos in self.POS_FILES:
            path = os.path.join(wndir, f"index.{name}")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("  "):
                        continue
                    parts = line.split()
                    if len(parts) < 6:
                        continue
                    lemma = parts[0].replace("_", " ").lower()
                    synset_cnt = int(parts[2])
                    p_cnt = int(parts[3])
                    offsets = parts[6 + p_cnt: 6 + p_cnt + synset_cnt]
                    self.index.setdefault(lemma, []).extend((pos, int(o)) for o in offsets)
        self._data_fh = {}
        for name, pos in self.POS_FILES:
            path = os.path.join(wndir, f"data.{name}")
            if os.path.exists(path):
                self._data_fh[pos] = open(path, encoding="utf-8", errors="replace")

    def _gloss(self, pos: str, offset: int) -> str | None:
        fh = self._data_fh.get(pos)
        if fh is None:
            return None
        fh.seek(offset)
        line = fh.readline()
        if " | " not in line:
            return None
        return line.split(" | ", 1)[1].strip()

    def senses(self, word: str) -> list[dict]:
        key = word.strip().lower()
        hits = self.index.get(key)
        if not hits:
            return []
        pos_label = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}
        order = {"n": 0, "v": 1, "a": 2, "s": 2, "r": 3}
        out = []
        for pos, offset in sorted(hits, key=lambda h: order.get(h[0], 9)):
            gloss = self._gloss(pos, offset)
            if not gloss:
                continue
            segments = gloss.split(";")
            definition = segments[0].strip()
            examples = []
            for seg in segments[1:]:
                seg = seg.strip()
                if len(seg) >= 2 and seg.startswith('"') and seg.endswith('"'):
                    ex = seg[1:-1].strip()
                    if ex:
                        examples.append(ex[0].upper() + ex[1:])
            if not definition:
                continue
            out.append({
                "partOfSpeech": pos_label.get(pos, pos),
                "definition": definition,
                "examples": examples[:WORDNET_MAX_EXAMPLES_PER_SENSE],
            })
            if len(out) >= WORDNET_MAX_SENSES:
                break
        return out


# ----------------------------------------------------------------- oxford ----
def load_oxford(path: str) -> dict:
    data = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("status") == "ok" and d.get("senses"):
                data[d["word"].strip().lower()] = d
    return data


def audio_file_name(word: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "'-") else "_" for ch in word.strip().lower())
    return f"{safe}.mp3"


# ------------------------------------------------------------------ main ----
def backup_db(db_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.bak-{ts}"
    shutil.copyfile(db_path, dst)
    return dst


def table_counts(conn) -> dict:
    return {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in CONTENT_TABLES}


def switch_to_oxford(conn, oxford: dict, wn: WordNet, audio_dir: str | None, apply: bool) -> dict:
    stats = {"oxford": 0, "wordnet": 0, "wiktionary": 0,
             "entries_updated": 0, "entries_inserted": 0, "leftover_untouched": 0,
             "examples_deleted": 0, "examples_inserted": 0,
             "pron_updated": 0, "pron_inserted": 0,
             "audio_local": 0, "audio_hotlink": 0, "audio_none": 0,
             "books_renamed": 0}
    ts = now_iso(conn)
    words = conn.execute("select id, text, normalized_text from words").fetchall()
    tx = conn
    if apply:
        tx.execute("pragma synchronous=NORMAL")
    pending = 0

    for wid, text, ntext in words:
        key = (ntext or text or "").strip().lower()
        ox = oxford.get(key)
        senses = None
        source = None
        if ox:
            senses = [{"partOfSpeech": s.get("partOfSpeech") or "",
                       "definition": s.get("definition") or "",
                       "examples": (s.get("examples") or [])[:OXFORD_MAX_EXAMPLES_PER_SENSE]}
                      for s in ox["senses"] if s.get("definition")]
            source = "oxford_api" if senses else None
            if not senses:
                ox = None
        if not senses:
            wn_senses = wn.senses(key)
            if wn_senses:
                senses = wn_senses
                source = "imported"
        if not senses:
            stats["wiktionary"] += 1
            continue
        stats["oxford" if source == "oxford_api" else "wordnet"] += 1

        rows = tx.execute(
            "select id, created_at from entries where word_id=? order by sense_order", (wid,)
        ).fetchall()
        m = len(senses)
        n = len(rows)
        if n > m:
            stats["leftover_untouched"] += n - m
        if apply:
            # 1) update in place (never delete -> cards keep resolving)
            for i, (eid, created_at) in enumerate(rows[:m]):
                s = senses[i]
                tx.execute(
                    "update entries set sense_order=?, part_of_speech=?, sense_label='',"
                    " definition=?, definition_source=?, chinese_note=null, updated_at=?"
                    " where id=?",
                    (i + 1, s["partOfSpeech"], s["definition"], source, ts, eid),
                )
                stats["entries_updated"] += 1
                tx.execute("delete from entry_examples where entry_id=?", (eid,))
                stats["examples_deleted"] += 1
                for j, ex in enumerate(s["examples"]):
                    tx.execute(
                        "insert into entry_examples (id, entry_id, example_order, sentence,"
                        " source, is_primary, created_at, updated_at) values (?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), eid, j + 1, ex, source, 1 if j == 0 else 0, ts, ts),
                    )
                    stats["examples_inserted"] += 1
            # 2) insert extra senses
            for i in range(n, m):
                s = senses[i]
                eid = str(uuid.uuid4())
                tx.execute(
                    "insert into entries (id, word_id, sense_order, part_of_speech, sense_label,"
                    " definition, definition_source, chinese_note, created_at, updated_at)"
                    " values (?,?,?,?,?,?,?,null,?,?)",
                    (eid, wid, i + 1, s["partOfSpeech"], "", s["definition"], source, ts, ts),
                )
                stats["entries_inserted"] += 1
                for j, ex in enumerate(s["examples"]):
                    tx.execute(
                        "insert into entry_examples (id, entry_id, example_order, sentence,"
                        " source, is_primary, created_at, updated_at) values (?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), eid, j + 1, ex, source, 1 if j == 0 else 0, ts, ts),
                    )
                    stats["examples_inserted"] += 1

        # 3) pronunciation: oxford only
        if ox and apply:
            fname = audio_file_name(key)
            local_ok = bool(audio_dir) and os.path.exists(os.path.join(audio_dir, fname))
            if local_ok:
                audio_url = f"/audio_oxford/{fname}"
                stats["audio_local"] += 1
            else:
                audio_url = ox.get("audioUk") or ox.get("audioUs")
                stats["audio_hotlink" if audio_url else "audio_none"] += 1
            row = tx.execute(
                "select response_json from pronunciation_cache where normalized_word=?",
                (key,),
            ).fetchone()
            payload = {}
            if row:
                try:
                    payload = json.loads(row[0])
                except Exception:
                    payload = {}
            payload.update({
                "word": key,
                "ipa": ox.get("ipaUk") or ox.get("ipaUs") or payload.get("ipa"),
                "ipaUk": ox.get("ipaUk"),
                "ipaUs": ox.get("ipaUs"),
                "audioUrl": audio_url,
                "audioSource": "oxford" if audio_url else payload.get("audioSource"),
                "sourceUrl": ox.get("sourceUrl"),
                "status": "ready",
            })
            js = json.dumps(payload, ensure_ascii=False)
            if row:
                tx.execute(
                    "update pronunciation_cache set response_json=?, status='ready',"
                    " retry_after=null, cached_at=? where normalized_word=?",
                    (js, ts, key),
                )
                stats["pron_updated"] += 1
            else:
                tx.execute(
                    "insert into pronunciation_cache (normalized_word, response_json, status,"
                    " retry_after, cached_at) values (?,?, 'ready', null, ?)",
                    (key, js, ts),
                )
                stats["pron_inserted"] += 1
        elif ox:
            fname = audio_file_name(key)
            local_ok = bool(audio_dir) and os.path.exists(os.path.join(audio_dir, fname))
            stats["audio_local" if local_ok else "audio_hotlink"] += 1

        if apply:
            pending += 1
            if pending >= 25:
                tx.commit()
                pending = 0

    if apply:
        for bid, title_en in BOOK_TITLES_EN.items():
            cur = tx.execute("update vocabulary_books set title=?, updated_at=? where id=?",
                             (title_en, ts, bid))
            stats["books_renamed"] += cur.rowcount
    return stats


def switch_to_wiktionary(conn, backup_path: str, apply: bool) -> dict:
    stats = {}
    if not apply:
        bak = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        stats["backup_counts"] = table_counts(bak)
        bak.close()
        return stats
    conn.execute("attach database ? as bak", (backup_path,))
    try:
        for t in CONTENT_TABLES:
            conn.execute(f"delete from {t}")
            conn.execute(f"insert into {t} select * from bak.{t}")
            stats[t] = conn.execute(f"select count(*) from {t}").fetchone()[0]
    finally:
        conn.commit()  # close the write transaction before DETACH
        conn.execute("detach database bak")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--direction", choices=("oxford", "wiktionary"), required=True)
    ap.add_argument("--oxford-jsonl", default="/home/ubuntu/oxford-data-inputs/oxford_progress_cloud.jsonl")
    ap.add_argument("--wordnet-dir", default="/tmp/wn/wordnet")
    ap.add_argument("--backup-db", default="/home/ubuntu/vocabularylearning/backend/data/vocabulary.sqlite.bak-20261019-pre-oxford",
                    help="pre-oxford backup used by direction=wiktionary")
    ap.add_argument("--audio-dir", default=None,
                    help="dir with mirrored oxford mp3s; existing files -> local /audio_oxford/ URLs")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--no-backup", action="store_true", help="skip the timestamped DB backup")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    before_path = args.db
    if args.apply and not args.no_backup:
        bak = backup_db(args.db)
        print(f"[backup] {bak}")

    conn = sqlite3.connect(args.db)
    before = table_counts(conn)

    if args.direction == "oxford":
        oxford = load_oxford(args.oxford_jsonl)
        wn = WordNet(args.wordnet_dir)
        stats = switch_to_oxford(conn, oxford, wn, args.audio_dir, args.apply)
    else:
        stats = switch_to_wiktionary(conn, args.backup_db, args.apply)

    if args.apply:
        conn.commit()
    after = table_counts(conn)
    result = {"db": before_path, "direction": args.direction, "applied": bool(args.apply),
              "before": before, "after": after, "stats": stats,
              "pos_sample": [r[0] for r in conn.execute("select distinct part_of_speech from entries limit 15")],
              "ts_sample": conn.execute("select created_at from entries limit 1").fetchone()[0]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
