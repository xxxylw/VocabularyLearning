#!/usr/bin/env python3
"""Build an offline open-source replacement vocabulary DB (stage 3 trial / stage 4 full).

Reads (all paths are CLI args):
  --kaikki-dir  *.jsonl  per-word wiktextract records (streamed + distilled)
  --ecdict      ECDICT stardict csv (streamed)
  --caps-db     optional sqlite with words/entries, used only to align per-word
                sense caps with an existing book; omit for a uniform cap of 5
  --schema      target schema.sql (default: ../app/schema.sql next to this script)

Writes:
  <out>.sqlite   new DB (schema-compatible; dictionary content tables only)
  <out>.build_report.json (or --report path)

Deterministic: same inputs => same outputs. Never touches the live DB.
Memory-safe on small sandboxes: kaikki lines are pre-filtered by word and
distilled to capped senses/IPAs on the fly; raw wiktextract JSON is discarded.
"""
from __future__ import annotations

import argparse, csv, glob, json, os, re, sqlite3, sys, unicodedata, uuid
from datetime import datetime, timezone

US_TAGS = {"US", "General-American"}
UK_TAGS = {"UK", "Received-Pronunciation", "British"}
POS_MAP = {
    "noun": "noun", "verb": "verb", "adj": "adjective", "adv": "adverb",
    "intj": "exclamation", "prep": "preposition", "pron": "pronoun",
    "det": "determiner", "conj": "conjunction", "name": "proper noun",
    "num": "number", "particle": "particle", "contraction": "contraction",
    "phrase": "phrase", "prep_phrase": "prepositional phrase",
    "article": "article", "postp": "postposition", "suffix": "suffix",
    "prefix": "prefix", "proverb": "proverb", "symbol": "symbol",
    "character": "character", "punct": "punctuation", "infix": "infix",
    "interfix": "interfix", "circumfix": "circumfix", "adv_phrase": "adverbial phrase",
}
MAX_EXAMPLES_PER_SENSE = 2
DEFAULT_SENSE_CAP = 5
WORD_LINE_RE = re.compile(r'^\{"word": "((?:[^"\\]|\\.)*)"')


def norm(w: str) -> str:
    return " ".join(w.strip().lower().split())


# NFKD 不分解的常用拉丁扩展字符（kaikki/词表可能带变音符，ECDICT 多为无符拼写）
_STRIP_MAP = str.maketrans({
    "æ": "ae", "œ": "oe", "ø": "o", "å": "a",
    "ð": "d", "þ": "th", "ł": "l", "đ": "d", "ß": "ss",
})


def strip_diacritics(w: str) -> str:
    """去掉变音符号用于 ECDICT 兜底匹配（naïve→naive、café→cafe）。"""
    decomposed = unicodedata.normalize("NFKD", w)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).translate(_STRIP_MAP)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


# ---------- loaders ----------

def load_sense_caps(db_path: str) -> dict[str, int]:
    caps: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for r in conn.execute(
        "select w.normalized_text as nw, count(e.id) as n "
        "from words w join entries e on e.word_id = w.id group by w.normalized_text"
    ):
        caps[r["nw"]] = max(1, min(DEFAULT_SENSE_CAP, r["n"]))
    conn.close()
    return caps


def _add_sense_dedup(bucket: list[dict], other: list[dict], seen: dict[str, list], row: dict,
                     is_form: bool) -> None:
    """同一词内相同定义只保留一条（QA 口径：同词内完全相同定义视为重复 entry）。

    wiktextract 常把同一 gloss 在多个 etymology/POS 小节重复列出（如 yoke/the
    各 5 条全同），且同一 gloss 可能一处无标签、另一处带 alt-of 标签（basement）。
    重复出现时只把未见过的例句并入已保留条（上限 MAX_EXAMPLES_PER_SENSE）；
    真实义项与形态义项撞定义时保留真实义项。
    """
    key = " ".join(row["definition"].split())
    prev = seen.get(key)
    if prev is not None:
        if prev[0] is other and not is_form:
            # 已保留的是形态义项，新来的是同定义真实义项：升级为真实义项，例句并入
            other.remove(prev[1])
            for sent in prev[1]["examples"]:
                if sent not in row["examples"] and len(row["examples"]) < MAX_EXAMPLES_PER_SENSE:
                    row["examples"].append(sent)
            seen[key] = [bucket, row]
            bucket.append(row)
            return
        kept = prev[1]
        for sent in row["examples"]:
            if sent not in kept["examples"] and len(kept["examples"]) < MAX_EXAMPLES_PER_SENSE:
                kept["examples"].append(sent)
        return
    seen[key] = [bucket, row]
    bucket.append(row)


def distill_kaikki(data: list[dict], cap: int) -> dict:
    """Extract only what the build needs from a raw wiktextract record."""
    us = uk = generic = audio = None
    senses: list[dict] = []
    form_senses: list[dict] = []  # form-of / alt-of 等形态说明义项，仅作填充
    seen: dict[str, list] = {}  # 去重键 → [所在桶, 行]，真实/形态两桶共用
    for e in data:
        for s in e.get("sounds") or []:
            tags = set(s.get("tags") or [])
            ipa = s.get("ipa")
            if ipa:
                if not us and tags & US_TAGS:
                    us = ipa
                if not uk and tags & UK_TAGS:
                    uk = ipa
                if not generic and not tags:
                    generic = ipa
            if not audio and (s.get("mp3_url") or s.get("ogg_url")):
                if not tags or tags & US_TAGS:
                    audio = s.get("mp3_url") or s.get("ogg_url")
        pos = POS_MAP.get(e.get("pos") or "", e.get("pos") or "word")
        for sense in e.get("senses") or []:
            glosses = sense.get("glosses") or []
            if not glosses:
                continue
            # wiktextract glosses 由外到内分层，首条常是 "As an auxiliary verb:" 这类
            # 目录头（冒号结尾、非完整 gloss）；优先取第一条非目录头 gloss
            gloss = glosses[0]
            for g in glosses:
                if not g.rstrip().endswith(":"):
                    gloss = g
                    break
            examples = [x["text"] for x in (sense.get("examples") or []) if x.get("text")]
            tags = sense.get("tags") or []
            label = ", ".join(tags[:2]) if tags else ""
            row = {
                "part_of_speech": pos,
                "definition": gloss,
                "sense_label": label,
                "examples": examples[:MAX_EXAMPLES_PER_SENSE],
            }
            if set(tags) & {"form-of", "alt-of", "alternative"}:
                _add_sense_dedup(form_senses, senses, seen, row, is_form=True)
            else:
                _add_sense_dedup(senses, form_senses, seen, row, is_form=False)
    # 先去重再截断：cap 作用于去重后的义项数；形态义项垫底填充
    senses = senses[:cap]
    for row in form_senses:
        if len(senses) >= cap:
            break
        senses.append(row)
    return {"ipa_us": us, "ipa_uk": uk, "ipa_generic": generic,
            "audio": audio, "senses": senses}


def load_kaikki(shard_dir: str, wanted: set[str], caps: dict[str, int]) -> dict[str, dict]:
    """Stream shards; parse only wanted words; keep distilled records only."""
    out: dict[str, dict] = {}
    scanned = parsed = 0
    for path in sorted(glob.glob(os.path.join(shard_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                scanned += 1
                m = WORD_LINE_RE.match(line)
                if not m:
                    continue
                try:
                    w = norm(json.loads('"%s"' % m.group(1)))
                except json.JSONDecodeError:
                    continue
                if w not in wanted or w in out:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                if rec.get("status") == "ok" and rec.get("data"):
                    out[w] = distill_kaikki(rec["data"], caps.get(w, DEFAULT_SENSE_CAP))
    print(f"load_kaikki: scanned={scanned} parsed={parsed} kept={len(out)}", file=sys.stderr)
    return out


def load_ecdict(path: str, wanted: set[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    """返回 (精确匹配索引, 去变音符兜底索引)。后者供 naïve→naive 这类词：
    词表带变音符而 ECDICT 只有无符拼写。精确命中优先于兜底。"""
    wanted_stripped = {strip_diacritics(w) for w in wanted}
    idx: dict[str, dict] = {}
    candidates: dict[str, dict] = {}  # 去符形 → ECDICT 行（原形不在 wanted 里的行）
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            w = norm(row["word"] or "")
            if not w:
                continue
            if w in wanted:
                if w not in idx:
                    idx[w] = row
                continue
            s = w if w.isascii() else strip_diacritics(w)
            if s in wanted_stripped and s not in candidates:
                candidates[s] = row
    stripped: dict[str, dict] = {}
    for cand in wanted:
        if cand in idx:
            continue
        s = strip_diacritics(cand)
        if s == cand:
            continue
        if s in idx:
            stripped[cand] = idx[s]
        elif s in candidates:
            stripped[cand] = candidates[s]
    print(f"load_ecdict: kept={len(idx)} stripped_fallback={len(stripped)}", file=sys.stderr)
    return idx, stripped


# ---------- per-word build ----------

def build_pronunciation(word: str, k: dict | None, erow: dict | None) -> tuple[dict | None, str]:
    """returns (response_json dict or None, grade) grade: dual|dual_ecdict|single|generic|ecdict|none"""
    us = uk = generic = audio = None
    if k:
        us, uk, generic, audio = k["ipa_us"], k["ipa_uk"], k["ipa_generic"], k["audio"]
    ec_phon = (erow or {}).get("phonetic", "").strip() or None
    grade = "none"
    if us and uk:
        grade = "dual"
    elif us and ec_phon:
        # kaikki 只给了美音；ECDICT 音标以英音为主，补 UK 槽位，构成真实双标
        uk = ec_phon
        grade = "dual_ecdict"
    elif us or uk:
        # 只有一个地域音标（含 kaikki 只给英音的情况：ECDICT 无法补美音，不假装双标）
        grade = "single"
    elif generic:
        grade = "generic"
    elif ec_phon:
        grade = "ecdict"
    else:
        return None, grade
    resp = {
        "word": norm(word),
        "ipa": us or uk or generic or ec_phon,
        "ipaUk": uk,
        "ipaUs": us,
        "audioUrl": audio,
        "sourceUrl": f"https://en.wiktionary.org/wiki/{word.replace(' ', '_')}#English",
        "status": "ready",
    }
    return {k2: v for k2, v in resp.items() if v is not None}, grade


def ecdict_lines(text: str) -> list[str]:
    """ECDICT definition/translation 用字面 \\n（两字符）分行，先还原再切。"""
    return [l.strip() for l in (text or "").replace("\\n", "\n").splitlines()]


def ecdict_senses(erow: dict, cap: int) -> list[dict]:
    out: list[dict] = []
    pos = (erow.get("pos") or "").split("/")[0].strip() or None
    pos_map = {"n": "noun", "v": "verb", "vt": "verb", "vi": "verb", "adj": "adjective",
               "a": "adjective", "adv": "adverb", "ad": "adverb", "prep": "preposition",
               "pron": "pronoun", "conj": "conjunction", "num": "number", "int": "exclamation"}
    pos = pos_map.get(pos, pos or "word")
    seen: set[str] = set()
    for line in ecdict_lines(erow.get("definition")):
        if not line or line.startswith("["):
            continue
        key = " ".join(line.split())
        if key in seen:
            continue
        seen.add(key)
        out.append({"part_of_speech": pos, "definition": line, "sense_label": "", "examples": []})
        if len(out) >= cap:
            break
    return out


def build_word(word: str, k: dict | None, erow: dict | None, cap: int) -> dict:
    result = {"word": word, "pronunciation": None, "pron_grade": "none",
              "entries": [], "chinese_note": None, "degraded": False, "source": None}
    if erow:
        lines = [l for l in ecdict_lines(erow.get("translation")) if l]
        if lines:
            result["chinese_note"] = lines[0]
    if k and k["senses"]:
        result["entries"] = k["senses"]
        result["source"] = "kaikki"
    if not result["entries"] and erow:
        result["entries"] = ecdict_senses(erow, cap)
        if result["entries"]:
            result["source"] = "ecdict"
    pron, grade = build_pronunciation(word, k, erow)
    result["pronunciation"] = pron
    result["pron_grade"] = grade
    if not result["entries"]:
        result["degraded"] = True
    return result


# ---------- DB writer ----------

def write_db(db_path: str, built: list[dict], schema_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
    with open(schema_path, encoding="utf-8") as f:
        schema = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    now = utc_now()
    src_id = new_id()
    conn.execute(
        "insert into sources (id, type, name, path_or_url, metadata_json, created_at) values (?,?,?,?,?,?)",
        (src_id, "open_dictionary", "kaikki+ecdict", "kaikki.org / ECDICT",
         json.dumps({
             "license": {
                 "wiktionary": "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/), via kaikki.org wiktextract",
                 "ecdict": "MIT (Copyright (c) 2025 Linwei)",
             },
             "notice": "Dictionary data excerpted and structured from Wiktionary (CC BY-SA 4.0) and ECDICT (MIT).",
         }, ensure_ascii=False), now))
    for bw in built:
        w = bw["word"]
        wid = new_id()
        conn.execute("insert into words (id, text, normalized_text, created_at, updated_at) values (?,?,?,?,?)",
                     (wid, w, norm(w), now, now))
        # 溯源标签：kaikki 义项标 open_api；ECDICT 兜底词标 fallback（schema CHECK 允许）
        src_tag = "fallback" if bw["source"] == "ecdict" else "open_api"
        for i, ent in enumerate(bw["entries"], start=1):
            eid = new_id()
            conn.execute(
                "insert into entries (id, word_id, sense_order, part_of_speech, sense_label, definition,"
                " definition_source, chinese_note, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)",
                (eid, wid, i, ent["part_of_speech"], ent["sense_label"], ent["definition"],
                 src_tag, bw["chinese_note"], now, now))
            for j, sent in enumerate(ent["examples"], start=1):
                conn.execute(
                    "insert into entry_examples (id, entry_id, example_order, sentence, source, is_primary,"
                    " created_at, updated_at) values (?,?,?,?,?,?,?,?)",
                    (new_id(), eid, j, sent, "imported", 1 if j == 1 else 0, now, now))
        if bw["pronunciation"]:
            conn.execute(
                "insert into pronunciation_cache (normalized_word, response_json, status, retry_after, cached_at)"
                " values (?,?,?,?,?)",
                (norm(w), json.dumps(bw["pronunciation"], ensure_ascii=False), "ready", None, now))
    conn.commit()
    conn.close()


# ---------- Oxford residual scan ----------

def oxford_residual_scan(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    hits = 0
    for (val,) in conn.execute(
        "select definition from entries union all select sentence from entry_examples"
        " union all select response_json from pronunciation_cache"
    ):
        if val and "oxfordlearnersdictionaries" in val.lower():
            hits += 1
    bad_src = conn.execute(
        "select count(*) from entries where definition_source in ('oxford_api','experimental_html')"
    ).fetchone()[0]
    bad_src += conn.execute(
        "select count(*) from entry_examples where source in ('oxford_api','experimental_html')"
    ).fetchone()[0]
    conn.close()
    return {"oxford_url_hits": hits, "oxford_source_rows": bad_src, "pass": hits == 0 and bad_src == 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--words-file", required=True, help="one word per line (display form)")
    ap.add_argument("--out", required=True, help="output sqlite path")
    ap.add_argument("--report", default=None)
    ap.add_argument("--kaikki-dir", required=True, help="dir of per-word kaikki *.jsonl shards")
    ap.add_argument("--ecdict", required=True, help="path to ECDICT stardict csv")
    ap.add_argument("--caps-db", default=None, help="optional sqlite with words/entries for sense caps")
    ap.add_argument("--schema", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "schema.sql"))
    a = ap.parse_args()

    words = [l.rstrip("\n") for l in open(a.words_file, encoding="utf-8") if l.strip()]
    wanted = {norm(w) for w in words}
    if a.caps_db:
        caps = load_sense_caps(a.caps_db)
        print(f"caps loaded: {len(caps)}", file=sys.stderr)
    else:
        caps = {}
    kaikki = load_kaikki(a.kaikki_dir, wanted, caps)
    ecdict, ecdict_stripped = load_ecdict(a.ecdict, wanted)

    built, report_rows = [], []
    for w in words:
        nw = norm(w)
        cap = caps.get(nw, DEFAULT_SENSE_CAP)
        erow = ecdict.get(nw) or ecdict_stripped.get(nw)
        bw = build_word(w, kaikki.get(nw), erow, cap)
        built.append(bw)
        report_rows.append({
            "word": w, "source": bw["source"], "degraded": bw["degraded"],
            "pron_grade": bw["pron_grade"],
            "senses": len(bw["entries"]),
            "senses_with_examples": sum(1 for e in bw["entries"] if e["examples"]),
            "examples": sum(len(e["examples"]) for e in bw["entries"]),
            "has_audio": bool((bw["pronunciation"] or {}).get("audioUrl")),
        })

    write_db(a.out, built, a.schema)
    scan = oxford_residual_scan(a.out)
    n = len(built)
    # QA 口径的重复 entry 指标：同词内完全相同定义（去重应使其≈0），直接写进报告便于核销
    dup_entries = 0
    for bw in built:
        seen_defs: set[str] = set()
        for ent in bw["entries"]:
            key = " ".join(ent["definition"].split())
            dup_entries += 1 if key in seen_defs else 0
            seen_defs.add(key)
    summary = {
        "words": n,
        "by_source": {},
        "ecdict_fallback_words": sorted(r["word"] for r in report_rows if r["source"] == "ecdict"),
        "degraded": [r["word"] for r in report_rows if r["degraded"]],
        "duplicate_definitions": dup_entries,
        "pron_grades": {},
        "senses": sum(r["senses"] for r in report_rows),
        "senses_with_examples": sum(r["senses_with_examples"] for r in report_rows),
        "examples": sum(r["examples"] for r in report_rows),
        "with_audio": sum(1 for r in report_rows if r["has_audio"]),
        "oxford_scan": scan,
    }
    for r in report_rows:
        summary["by_source"][r["source"] or "none"] = summary["by_source"].get(r["source"] or "none", 0) + 1
        summary["pron_grades"][r["pron_grade"]] = summary["pron_grades"].get(r["pron_grade"], 0) + 1
    out = {"summary": summary, "rows": report_rows}
    report_path = a.report or (a.out + ".build_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not scan["pass"]:
        raise SystemExit("OXFORD RESIDUAL SCAN FAILED")


if __name__ == "__main__":
    main()
