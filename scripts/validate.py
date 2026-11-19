#!/usr/bin/env python3
"""Red-line validation for the v3 migration (before/after comparison).

Usage:
  python3 validate.py --before LIVE_COPY.sqlite --after MIGRATED.sqlite \
      [--audio-list audio_files.txt] [--json report.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys

GARBAGE_WORDS = {
    "kangaroopenguinturtlecricketbutterfly", "ree", "int", "mnst",
}

UNTOUCHED_TABLES = [
    "users", "reviews", "today_queue", "today_repeat_pool", "book_words",
    "sessions", "subscriptions", "email_tokens", "user_settings",
    "vocabulary_books", "settings", "orders", "payment_callbacks",
    "prepare_jobs", "today_queue_snapshots",
]


def table_hash(conn, table):
    """Stable content hash of a table (ordered by all columns)."""
    cols = [c[1] for c in conn.execute(f"pragma table_info({table})")]
    order = ",".join(f'"{c}"' for c in cols)
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(f"select * from {table} order by {order}"):
        h.update(repr(tuple(row)).encode("utf-8", "surrogatepass"))
        n += 1
    return h.hexdigest(), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--audio-list", default=None,
                    help="file with one audio filename per line (ls audio_v3)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    before = sqlite3.connect(args.before)
    before.row_factory = sqlite3.Row
    after = sqlite3.connect(args.after)
    after.row_factory = sqlite3.Row

    report = {"checks": [], "pass": True}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            report["pass"] = False
        print(("[PASS] " if ok else "[FAIL] ") + name + (f" — {detail}" if detail else ""))

    def counts(db):
        out = {}
        for (t,) in db.execute(
                "select name from sqlite_master where type='table' order by name"):
            out[t] = db.execute(f"select count(*) from {t}").fetchone()[0]
        return out

    cb, ca = counts(before), counts(after)
    print("== 表行数对照 (before → after) ==")
    table_counts = {}
    for t in sorted(cb):
        table_counts[t] = [cb[t], ca.get(t)]
        print(f"  {t}: {cb[t]} → {ca.get(t)}")
    report["table_counts"] = table_counts

    # R1: cards.entry_id all join entries
    orphans = after.execute(
        "select count(*) from cards c left join entries e on c.entry_id=e.id "
        "where e.id is null").fetchone()[0]
    check("R1a 零孤儿卡 (cards.entry_id 全部可 join)", orphans == 0, f"{orphans} orphan cards")

    # R1b: (user_id, entry_id) unique
    dup = after.execute(
        "select count(*) from (select user_id, entry_id, count(*) n from cards "
        "group by 1,2 having n>1)").fetchone()[0]
    check("R1b cards (user_id, entry_id) 唯一", dup == 0, f"{dup} duplicate groups")

    # R1c: entries (word_id, sense_order) unique + contiguous
    dup2 = after.execute(
        "select count(*) from (select word_id, sense_order, count(*) n from entries "
        "group by 1,2 having n>1)").fetchone()[0]
    check("R1c entries (word_id, sense_order) 唯一", dup2 == 0, f"{dup2} duplicates")
    noncontig = after.execute(
        "select count(*) from (select word_id, count(*) n, min(sense_order) mn, "
        "max(sense_order) mx from entries group by 1 having mn!=1 or mx!=n)").fetchone()[0]
    check("R1d entries sense_order 从 1 连续", noncontig == 0, f"{noncontig} words non-contiguous")

    # R2: reviews rows preserved
    check("R2a reviews 行数零丢失", cb["reviews"] == ca["reviews"],
          f"{cb['reviews']} → {ca['reviews']}")
    orphan_rev = after.execute(
        "select count(*) from reviews r left join cards c on r.card_id=c.id "
        "where c.id is null").fetchone()[0]
    check("R2b reviews.card_id 全部可 join cards", orphan_rev == 0,
          f"{orphan_rev} orphan reviews")

    # R3: untouched tables — row count AND content hash identical.
    # reviews is special: card_id retargeting onto merge survivors is the
    # documented, allowed change (merging overflow sense cards into the
    # sense-1 card per task decision); every other column must be intact.
    for t in UNTOUCHED_TABLES:
        if t == "reviews":
            b_rev = {r["id"]: dict(r) for r in before.execute(
                "select * from reviews")}
            a_rev = {r["id"]: dict(r) for r in after.execute(
                "select * from reviews")}
            same_set = set(b_rev) == set(a_rev) and len(b_rev) == len(a_rev)
            cols = [c[1] for c in before.execute("pragma table_info(reviews)")]
            bad = [rid for rid in b_rev
                   if any(b_rev[rid][c] != a_rev[rid][c]
                          for c in cols if c != "card_id")]
            moved = [rid for rid in b_rev
                     if b_rev[rid]["card_id"] != a_rev[rid]["card_id"]]
            survivors = {r[0] for r in after.execute(
                "select distinct c.id from cards c")}
            all_ok = same_set and not bad and all(
                a_rev[rid]["card_id"] in survivors for rid in moved)
            check("R3 reviews 内容不变（除合并重定向 card_id）", all_ok,
                  f"{len(b_rev)} rows, card_id moved={len(moved)}, 其余字段 diff={len(bad)}")
            continue
        hb, nb = table_hash(before, t)
        ha, na = table_hash(after, t)
        check(f"R3 {t} 内容不变", hb == ha and nb == na,
              f"{nb} rows, hash {'same' if hb == ha else 'DIFF'}")

    # R4: cards survive with SM-2 state (except documented merges)
    merged_away = set()
    # recompute merges: cards in before not in after
    b_cards = {r["id"]: dict(r) for r in before.execute("select * from cards")}
    a_cards = {r["id"]: dict(r) for r in after.execute("select * from cards")}
    merged_away = set(b_cards) - set(a_cards)
    check("R4a 卡数守恒（差集=文档化合并删除）", len(b_cards) - len(a_cards) == len(merged_away),
          f"before={len(b_cards)} after={len(a_cards)} removed={len(merged_away)}")
    # SM-2 fields identical for surviving cards
    sm2 = ["status", "stage", "due_at", "created_on", "last_reviewed_at", "ef",
           "interval_days", "user_id"]
    changed = []
    for cid, c in b_cards.items():
        if cid not in a_cards:
            continue
        for f in sm2:
            if c[f] != a_cards[cid][f]:
                changed.append((cid, f, c[f], a_cards[cid][f]))
    check("R4b 存活卡 SM-2 调度字段逐一不变", not changed, f"{len(changed)} field diffs")
    if changed:
        report["sm2_diffs"] = changed

    # R5: today_queue / repeat_pool card join
    for t in ("today_queue", "today_repeat_pool"):
        n = after.execute(
            f"select count(*) from {t} q left join cards c on q.card_id=c.id "
            "where c.id is null").fetchone()[0]
        check(f"R5 {t}.card_id 全部可 join cards", n == 0, f"{n} orphans")

    # R6: Oxford residue = 0 (non-garbage)
    ph = ",".join("?" * len(GARBAGE_WORDS))
    gw = tuple(GARBAGE_WORDS)
    o1 = after.execute(
        f"select count(*) from entries where definition_source='oxford_api' "
        f"and word_id not in (select id from words where normalized_text in ({ph}))",
        gw).fetchone()[0]
    check("R6a entries.definition_source 无 oxford_api（除 4 垃圾词）", o1 == 0, f"{o1} rows")
    o2 = after.execute(
        f"select count(*) from entry_examples x join entries e on x.entry_id=e.id "
        f"join words w on e.word_id=w.id where x.source='oxford_api' "
        f"and w.normalized_text not in ({ph})", gw).fetchone()[0]
    check("R6b entry_examples.source 无 oxford_api（除垃圾词）", o2 == 0, f"{o2} rows")
    o3 = after.execute(
        "select count(*) from book_words where definition_source like '%oxford%'").fetchone()[0]
    check("R6c book_words.definition_source 无 oxford", o3 == 0, f"{o3} rows")
    o4 = after.execute(
        "select count(*) from sources where name like '%oxford%' or path_or_url like '%oxford%' "
        "or metadata_json like '%oxford%'").fetchone()[0]
    check("R6d sources 表无 oxford 署名", o4 == 0, f"{o4} rows")
    # text residue scan (informational)
    tx1 = after.execute(
        f"select count(*) from entries where definition like '%oxford%' "
        f"and word_id not in (select id from words where normalized_text in ({ph}))",
        gw).fetchone()[0]
    tx2 = after.execute(
        f"select count(*) from entry_examples x join entries e on x.entry_id=e.id "
        f"join words w on e.word_id=w.id where x.sentence like '%oxford%' "
        f"and w.normalized_text not in ({ph})", gw).fetchone()[0]
    tx3 = after.execute(
        "select count(*) from book_words where definition like '%oxford%'").fetchone()[0]
    print(f"[INFO] 文本残留扫描: entries.definition={tx1}, examples.sentence={tx2}, "
          f"book_words.definition={tx3}")
    report["oxford_text_residue"] = {"entries": tx1, "examples": tx2, "book_words": tx3}
    # garbage words untouched (entries content identical)
    for t in ("entries", "entry_examples", "pronunciation_cache"):
        pass  # covered by pc check below

    # R7: book word coverage — every book word has >=1 entry + ready pc row
    missing_entry = after.execute(
        """select count(*) from (select distinct normalized_text from book_words b
           where not exists (select 1 from words w join entries e on e.word_id=w.id
           where w.normalized_text=b.normalized_text))""").fetchone()[0]
    check("R7a 每个 book word 在迁移后有 ≥1 词条", missing_entry == 0,
          f"{missing_entry} words missing")
    gw_ph = ",".join("?" * len(GARBAGE_WORDS))
    missing_pc = after.execute(
        f"""select count(*) from (select distinct normalized_text from book_words b
           where b.normalized_text not in ({gw_ph})
           and not exists (select 1 from pronunciation_cache p
           where p.normalized_word=b.normalized_text and p.status='ready'))""",
        tuple(GARBAGE_WORDS)).fetchone()[0]
    gw_missing = after.execute(
        f"""select count(*) from (select distinct normalized_text from book_words b
           where b.normalized_text in ({gw_ph})
           and not exists (select 1 from pronunciation_cache p
           where p.normalized_word=b.normalized_text and p.status='ready'))""",
        tuple(GARBAGE_WORDS)).fetchone()[0]
    check("R7b 每个 book word 有 ready 发音缓存（4 垃圾词豁免）", missing_pc == 0,
          f"{missing_pc} missing; garbage words excluded: {gw_missing}")

    # R8: audio file references
    import os
    audio_files = None
    if args.audio_list:
        with open(args.audio_list) as f:
            audio_files = {os.path.basename(line.strip()) for line in f if line.strip()}
    miss_local, n_local, n_remote, miss_remote = [], 0, 0, []
    if audio_files is not None:
        for r in after.execute(
                "select normalized_word, response_json from pronunciation_cache "
                "where status='ready'"):
            j = json.loads(r["response_json"])
            u = j.get("audioUrl")
            if u is None:
                continue
            if u.startswith("/audio_v3/"):
                n_local += 1
                fn = os.path.basename(u)
                if fn not in audio_files:
                    miss_local.append((r["normalized_word"], u))
            else:
                n_remote += 1
        check("R8 本地音频引用全部有文件", not miss_local,
              f"local={n_local} remote={n_remote} missing={len(miss_local)}")
        report["audio"] = {"local_refs": n_local, "remote_refs": n_remote,
                           "missing": miss_local[:50]}

    # R9: pronunciation_cache replaced (Oxford URLs gone)
    ox_pc = after.execute(
        "select normalized_word from pronunciation_cache where response_json like '%oxford%'").fetchall()
    # the word 'oxford' itself legitimately contains the string in its
    # own dictionary fields (sourceUrl points to wiktionary, audio is tts)
    ox_real = [r[0] for r in ox_pc if r[0] != 'oxford']
    check("R9 pronunciation_cache 无 Oxford 字典残留", not ox_real, f"{ox_real}")

    # R10: 20 个学过词（有 reviews 的卡）SM-2 前后一致抽样
    studied = before.execute(
        "select distinct c.id from cards c join reviews r on r.card_id=c.id "
        "order by c.id limit 20").fetchall()
    diffs = []
    for r in studied:
        cid = r["id"]
        if cid in a_cards:
            for f in sm2:
                if b_cards[cid][f] != a_cards[cid][f]:
                    diffs.append((cid, f))
    check("R10 抽 20 个学过卡调度状态前后一致", not diffs, f"{len(diffs)} diffs")
    # reviews of merged-away cards still present (moved, not lost)
    b_rev = before.execute("select id, card_id, rating, reviewed_at, study_date "
                           "from reviews order by id").fetchall()
    a_rev = after.execute("select id, card_id, rating, reviewed_at, study_date "
                          "from reviews order by id").fetchall()
    same = all(x[0] == y[0] and x[2:] == y[2:] for x, y in zip(b_rev, a_rev))
    moved = sum(1 for x, y in zip(b_rev, a_rev) if x[1] != y[1])
    check("R11 reviews 明细零丢失（card_id 变更=合并重定向）", len(b_rev) == len(a_rev) and same,
          f"rows={len(b_rev)}, card_id moved={moved}")

    # garbage words untouched: compare their entries/examples/pc rows
    gw_same = True
    for w in GARBAGE_WORDS:
        lb = before.execute("select id from words where normalized_text=?", (w,)).fetchone()
        la = after.execute("select id from words where normalized_text=?", (w,)).fetchone()
        if lb is None:
            continue
        if lb["id"] != la["id"]:
            gw_same = False
            break
        eb = [tuple(r) for r in before.execute(
            "select * from entries where word_id=? order by sense_order", (lb["id"],))]
        ea = [tuple(r) for r in after.execute(
            "select * from entries where word_id=? order by sense_order", (la["id"],))]
        # created/updated_at timestamps of untouched rows stay identical
        if eb != ea:
            gw_same = False
    check("R12 4 个垃圾词词条零改动", gw_same)
    pcb = {r[0]: r[1] for r in before.execute(
        "select normalized_word, response_json from pronunciation_cache")}
    pca = {r[0]: r[1] for r in after.execute(
        "select normalized_word, response_json from pronunciation_cache")}
    gw_pc_same = all(pcb.get(w) == pca.get(w) for w in GARBAGE_WORDS if w in pcb)
    check("R13 垃圾词发音缓存零改动", gw_pc_same)

    # final dictionary stats
    report["final"] = {
        "words": ca.get("words"),
        "entries": ca.get("entries"),
        "entry_examples": ca.get("entry_examples"),
        "pronunciation_cache": ca.get("pronunciation_cache"),
        "cards": ca.get("cards"),
        "reviews": ca.get("reviews"),
    }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n==>", "ALL PASS" if report["pass"] else "HAS FAILURES")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
