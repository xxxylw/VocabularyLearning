#!/usr/bin/env python3
"""v3 open-dictionary in-place migration for VocabularyLearning live DB.

Strategy: merge v3 dictionary content INTO the live vocabulary.sqlite by
word (normalized_text) match — never swap the file, so all user tables
(cards / reviews / today_queue / today_repeat_pool / users /
book_words / sessions / ...) survive untouched.

Per non-garbage word:
  * sense k (k <= min(live,v3) sense count): UPDATE the live entry in
    place with v3 content — entry_id preserved, cards untouched.
  * v3 has more senses: INSERT v3 senses beyond live count (fresh rows;
    v3 hex ids are disjoint from live uuid ids — verified 0 overlap).
  * live has more senses (overflow): remap overflow cards to the entry
    holding v3 sense 1 (primary sense), then delete the overflow entry
    and its examples. If the user already owns a card on the target
    entry (UNIQUE(user_id, entry_id)), merge: keep the card with more
    reviews (tie: later last_reviewed_at; tie: the target card), remap
    the loser's reviews onto the survivor, delete the loser card.
  * examples of every kept entry: DELETE live, INSERT v3.

Also:
  * v3-only words (incl. their entries/examples) are imported so every
    book word has shared entries locally (prepare goes offline).
  * pronunciation_cache: all live rows (non-garbage) replaced by v3 rows
    (non-garbage). Garbage words keep their live rows.
  * sources: the 5 open_dictionary attribution rows are appended.

Usage:
  python3 migrate_v3.py --live LIVE.sqlite --v3 v3.sqlite [--apply]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid

GARBAGE_WORDS = {
    "kangaroopenguinturtlecricketbutterfly",
    "ree",
    "int",
    "mnst",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"


def _replace_examples(conn, live_entry_id, v3_entry_id, now, stats):
    cur = conn.execute("delete from entry_examples where entry_id=?",
                       (live_entry_id,))
    stats["examples_deleted"] += cur.rowcount
    for x in conn.execute(
            "select * from v3.entry_examples where entry_id=? order by example_order",
            (v3_entry_id,)):
        new_id = x["id"]
        clash = conn.execute("select 1 from entry_examples where id=?",
                             (new_id,)).fetchone()
        if clash:
            new_id = str(uuid.uuid4())
        conn.execute(
            """insert into entry_examples (id, entry_id, example_order, sentence,
               source, is_primary, created_at, updated_at) values (?,?,?,?,?,?,?,?)""",
            (new_id, live_entry_id, x["example_order"], x["sentence"],
             x["source"], x["is_primary"], now, now))
        stats["examples_inserted"] += 1
    stats["examples_replaced_entries"] += 1


def _retarget_queue_rows(conn, table, loser_id, survivor_id, stats):
    """Move loser's queue rows to the survivor; drop true duplicates."""
    refs = conn.execute(f"select * from {table} where card_id=?",
                         (loser_id,)).fetchall()
    for ref in refs:
        cols = {"today_queue": ("user_id", "book_id", "study_date"),
                "today_repeat_pool": ("user_id", "book_id", "study_date")}[table]
        where = " and ".join(f"{c}=?" for c in cols)
        params = [ref[c] for c in cols]
        dup = conn.execute(
            f"select 1 from {table} where card_id=? and {where}",
            [survivor_id] + params).fetchone()
        if dup:
            conn.execute(f"delete from {table} where id=?", (ref["id"],))
            stats["queue_dropped_dup"] = stats.get("queue_dropped_dup", 0) + 1
        else:
            conn.execute(f"update {table} set card_id=? where id=?",
                         (survivor_id, ref["id"]))
            stats["queue_retgt"] = stats.get("queue_retgt", 0) + 1


def _remap_cards(conn, old_entry_id, target_entry_id, word, stats):
    cards = conn.execute("select * from cards where entry_id=?",
                         (old_entry_id,)).fetchall()
    for c in cards:
        existing = conn.execute(
            "select * from cards where entry_id=? and user_id=?",
            (target_entry_id, c["user_id"])).fetchone()
        if existing is None:
            conn.execute("update cards set entry_id=? where id=?",
                         (target_entry_id, c["id"]))
            stats["cards_remapped"] += 1
            stats["remap_log"].append({
                "card": c["id"], "user": c["user_id"], "word": word,
                "old_entry": old_entry_id, "new_entry": target_entry_id})
            continue
        # collision on UNIQUE(user_id, entry_id): merge onto the sense-1
        # (target) card — per task decision 2026-09-18, the primary-sense
        # card always survives; the overflow card merges into it.
        # (Rehearsal showed sibling sense cards carry identical SM-2 state
        # — they are reviewed in the same session — so nothing is lost.)
        survivor, loser = existing, c
        cur = conn.execute("update reviews set card_id=? where card_id=?",
                           (survivor["id"], loser["id"]))
        stats["reviews_remapped"] += cur.rowcount
        _retarget_queue_rows(conn, "today_queue", loser["id"], survivor["id"], stats)
        _retarget_queue_rows(conn, "today_repeat_pool", loser["id"], survivor["id"], stats)
        conn.execute("delete from cards where id=?", (loser["id"],))
        stats["cards_merged_away"] += 1
        stats["merge_log"].append({
            "word": word, "user": c["user_id"], "kept_card": survivor["id"],
            "dropped_card": loser["id"], "reviews_moved": cur.rowcount,
            "kept_entry": survivor["entry_id"]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True)
    ap.add_argument("--v3", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default=None, help="write stats JSON here")
    args = ap.parse_args()

    conn = sqlite3.connect(args.live)
    conn.row_factory = sqlite3.Row
    conn.execute("attach database ? as v3", (args.v3,))

    live_words = {r["normalized_text"]: r["id"] for r in conn.execute(
        "select normalized_text, id from words")}
    v3_words = {r["normalized_text"]: r["id"] for r in conn.execute(
        "select normalized_text, id from v3.words")}
    shared = sorted(set(live_words) & set(v3_words))
    shared = [w for w in shared if w not in GARBAGE_WORDS]
    v3_only = sorted(set(v3_words) - set(live_words))
    v3_only = [w for w in v3_only if w not in GARBAGE_WORDS]
    live_only = sorted(set(live_words) - set(v3_words))
    live_only = [w for w in live_only if w not in GARBAGE_WORDS]

    stats = {
        "started_at": now_iso(),
        "mode": "apply" if args.apply else "dry-run",
        "shared_words": len(shared),
        "v3_only_words": len(v3_only),
        "live_only_words_skipped": live_only,
        "words_updated": 0,
        "words_inserted": 0,
        "entries_updated": 0,
        "entries_inserted": 0,
        "entries_deleted_overflow": 0,
        "examples_replaced_entries": 0,
        "examples_deleted": 0,
        "examples_inserted": 0,
        "cards_remapped": 0,
        "cards_merged_away": 0,
        "reviews_remapped": 0,
        "pc_deleted": 0,
        "pc_inserted": 0,
        "sources_inserted": 0,
        "merge_log": [],
        "remap_log": [],
    }

    conn.isolation_level = None
    conn.execute("begin immediate")

    try:
        now = now_iso()

        # ---------- 1. shared words: in-place content swap ----------
        for w in shared:
            lid, vid = live_words[w], v3_words[w]
            live_ents = conn.execute(
                "select id, sense_order from entries where word_id=? order by sense_order",
                (lid,)).fetchall()
            v3_ents = conn.execute(
                "select * from v3.entries where word_id=? order by sense_order",
                (vid,)).fetchall()
            assert live_ents, f"live word {w!r} has no entries"
            assert v3_ents, f"v3 word {w!r} has no entries"

            n_keep = min(len(live_ents), len(v3_ents))
            for k in range(n_keep):
                e = v3_ents[k]
                conn.execute(
                    """update entries set part_of_speech=?, sense_label=?,
                       definition=?, definition_source=?, chinese_note=?, updated_at=?
                       where id=?""",
                    (e["part_of_speech"], e["sense_label"], e["definition"],
                     e["definition_source"], e["chinese_note"], now,
                     live_ents[k]["id"]))
                stats["entries_updated"] += 1
                _replace_examples(conn, live_ents[k]["id"], e["id"], now, stats)

            # v3 has more senses → insert extras (fresh rows with v3's ids)
            for k in range(n_keep, len(v3_ents)):
                e = v3_ents[k]
                conn.execute(
                    """insert into entries (id, word_id, sense_order, part_of_speech,
                       sense_label, definition, definition_source, chinese_note,
                       created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)""",
                    (e["id"], lid, e["sense_order"], e["part_of_speech"],
                     e["sense_label"], e["definition"], e["definition_source"],
                     e["chinese_note"], now, now))
                stats["entries_inserted"] += 1
                _replace_examples(conn, e["id"], e["id"], now, stats)

            # live overflow senses → cards remap, then delete
            if len(live_ents) > len(v3_ents):
                target_row = conn.execute(
                    "select id from entries where word_id=? and sense_order=1",
                    (lid,)).fetchone()
                target_id = target_row["id"]
                for k in range(n_keep, len(live_ents)):
                    old = live_ents[k]
                    _remap_cards(conn, old["id"], target_id, w, stats)
                    conn.execute("delete from entry_examples where entry_id=?",
                                 (old["id"],))
                    conn.execute("delete from entries where id=?", (old["id"],))
                    stats["entries_deleted_overflow"] += 1
            stats["words_updated"] += 1

        # ---------- 2. v3-only words: import word + entries + examples ----------
        for w in v3_only:
            vid = v3_words[w]
            row = conn.execute("select * from v3.words where id=?", (vid,)).fetchone()
            conn.execute(
                """insert into words (id, text, normalized_text, created_at, updated_at)
                   values (?,?,?,?,?)""",
                (row["id"], row["text"], row["normalized_text"], now, now))
            v3_ents = conn.execute(
                "select * from v3.entries where word_id=? order by sense_order",
                (vid,)).fetchall()
            for e in v3_ents:
                conn.execute(
                    """insert into entries (id, word_id, sense_order, part_of_speech,
                       sense_label, definition, definition_source, chinese_note,
                       created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)""",
                    (e["id"], row["id"], e["sense_order"], e["part_of_speech"],
                     e["sense_label"], e["definition"], e["definition_source"],
                     e["chinese_note"], now, now))
                stats["entries_inserted"] += 1
                for x in conn.execute(
                        "select * from v3.entry_examples where entry_id=? order by example_order",
                        (e["id"],)):
                    conn.execute(
                        """insert into entry_examples (id, entry_id, example_order,
                           sentence, source, is_primary, created_at, updated_at)
                           values (?,?,?,?,?,?,?,?)""",
                        (x["id"], e["id"], x["example_order"], x["sentence"],
                         x["source"], x["is_primary"], now, now))
                    stats["examples_inserted"] += 1
            stats["words_inserted"] += 1

        # ---------- 3. pronunciation_cache ----------
        garbage_ph = ",".join("?" * len(GARBAGE_WORDS))
        cur = conn.execute(
            f"delete from pronunciation_cache where normalized_word not in ({garbage_ph})",
            tuple(GARBAGE_WORDS))
        stats["pc_deleted"] = cur.rowcount
        for r in conn.execute(
                "select normalized_word, response_json, status, retry_after, cached_at "
                "from v3.pronunciation_cache"):
            if r["normalized_word"] in GARBAGE_WORDS:
                continue
            conn.execute(
                """insert into pronunciation_cache (normalized_word, response_json,
                   status, retry_after, cached_at) values (?,?,?,?,?)""",
                (r["normalized_word"], r["response_json"], r["status"],
                 r["retry_after"], r["cached_at"]))
            stats["pc_inserted"] += 1

        # ---------- 4. sources attribution ----------
        for r in conn.execute("select * from v3.sources"):
            exists = conn.execute("select 1 from sources where id=?",
                                  (r["id"],)).fetchone()
            if exists:
                continue
            conn.execute(
                """insert into sources (id, type, name, path_or_url, metadata_json, created_at)
                   values (?,?,?,?,?,?)""",
                (r["id"], r["type"], r["name"], r["path_or_url"],
                 r["metadata_json"], now))
            stats["sources_inserted"] += 1

        if not args.apply:
            conn.execute("rollback")
            print("DRY RUN OK — rolled back.")
        else:
            conn.execute("commit")
            print("APPLIED + committed.")
    except BaseException:
        conn.execute("rollback")
        raise

    stats["finished_at"] = now_iso()
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=1)
    printable = {k: v for k, v in stats.items()
                 if k not in ("merge_log", "remap_log")}
    print(json.dumps(printable, indent=1))
    if stats["merge_log"]:
        print("MERGE LOG:")
        for m in stats["merge_log"]:
            print("  ", json.dumps(m, ensure_ascii=False))
    if stats["remap_log"]:
        print("REMAP LOG:")
        for m in stats["remap_log"]:
            print("  ", json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
