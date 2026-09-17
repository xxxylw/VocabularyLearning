#!/usr/bin/env python3
"""Build open_dictionary_v3.sqlite from v2 base + kaikki per-word fetches +
WordNet (Open English WordNet 2024, CC BY 4.0) + ECDICT (MIT) + cmudict (BSD)
+ edge-tts audio for the gap.

Fill order (English definition):
  1. Wiktionary via kaikki (reused from v2 entries for v2 kaikki-covered words;
     freshly fetched for v2 fallback/degraded words + new words).
  2. WordNet (CC BY 4.0). Marked definition_source='open_api' (schema enum).
  3. ECDICT (MIT). definition_source='fallback'.

Pronunciation (English IPA):
  1. kaikki (US/UK) for v2 words (reused) or fetched words.
  2. cmudict ARPAbet → IPA fills missing US IPA slot.
  3. ECDICT phonetic fills missing UK IPA slot (kept from v2 policy).

Audio:
  - Wiktionary/Commons mp3_url for kaikki-covered words (reused when present).
  - edge-tts en-US-AriaNeural for the gap; file served at /audio_v3/<slug>.mp3.

Licenses stored as multiple rows in `sources` (one per source) with
metadata_json containing license + attribution.

Oxford residual must be 0. v2's oxford_residual_scan() runs at end.
"""
from __future__ import annotations
import argparse, csv, glob, json, os, re, sqlite3, sys, unicodedata, uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arpabet_ipa import load_cmudict, arpabet_to_ipa  # noqa: E402
from wordnet_loader import load_wordnet  # noqa: E402
from ecdict_loader import load_ecdict, ecdict_lines, ecdict_senses  # noqa: E402

US_TAGS = {"US", "General-American"}
UK_TAGS = {"UK", "Received-Pronunciation", "British"}
MAX_EXAMPLES_PER_SENSE = 2
DEFAULT_SENSE_CAP = 5
AUDIO_REL_PATH = "/audio_v3"


def norm(w: str) -> str:
    return " ".join((w or "").strip().lower().split())


_STRIP_MAP = str.maketrans({
    "æ": "ae", "œ": "oe", "ø": "o", "å": "a",
    "ð": "d", "þ": "th", "ł": "l", "đ": "d", "ß": "ss",
})


def strip_diacritics(w: str) -> str:
    decomposed = unicodedata.normalize("NFKD", w)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).translate(_STRIP_MAP)


def lookup_variants(w: str) -> list[str]:
    """Lookup variants for a normalized word: base form, hyphen→space,
    underscore→space, and diacritics-stripped forms (of each)."""
    alias = ALIASES.get(w)
    seeds = (w, w.replace("-", " "), w.replace("_", " ")) + ((alias, alias.replace("-", " ")) if alias else ())
    out: list[str] = []
    for s in seeds:
        for v in (s, strip_diacritics(s)):
            v = norm(v)
            if v and v not in out:
                out.append(v)
    return out


# Curated aliases for book words that are OCR-noise / variant spellings absent
# from every dictionary source. The alias target is looked up in kaikki /
# wordnet / ecdict on the original word's behalf (source fields unchanged).
ALIASES: dict[str, str] = {
    "a bachelor of arts": "bachelor of arts",
    "a. d": "ad",
    "airhostess": "air hostess",
    "babyboom": "baby boom",
    "ball-point": "ballpoint",
    "ball-point pen": "ballpoint pen",
    "celcius": "celsius",
    "circum": "circum-",
    "dept.": "dept",
    "exercise-book": "exercise book",
    "fat-head": "fathead",
    "fire-bomb": "firebomb",
    "forbes": "Forbes",
    "hrh": "HRH",
    "ohp": "OHP",
    "wollongong": "Wollongong",
    "generaliza-tion": "generalization",
    "gonorrh": "gonorrhea",
    "instalation": "installation",
    "kung": "kung fu",
    "melodie": "melody",
    "mileometre": "mileometer",
    "non-materialistic": "nonmaterialistic",
    "o. k": "ok",
    "ohp": "ohp",
    "questionaire": "questionnaire",
    "reservior": "reservoir",
    "salesmanager": "sales manager",
    "sea-shell": "seashell",
    "semi-conductor": "semiconductor",
    "swim-suit": "swimsuit",
    "wollongong": "wollongong",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def slug_for_audio(word: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", word.strip().lower()).strip("_")[:120] or "_"


# ---------- kaikki distillation ----------

def distill_kaikki_record(rec: dict, cap: int) -> dict:
    """distill a single kaikki entry dict (rec with senses/sounds)."""
    out = {"ipa_us": None, "ipa_uk": None, "ipa_generic": None, "audio": None, "senses": []}
    for s in rec.get("sounds") or []:
        tags = set(s.get("tags") or [])
        ipa = s.get("ipa")
        if ipa:
            if not out["ipa_us"] and tags & US_TAGS:
                out["ipa_us"] = ipa
            if not out["ipa_uk"] and tags & UK_TAGS:
                out["ipa_uk"] = ipa
            if not out["ipa_generic"] and not tags:
                out["ipa_generic"] = ipa
        if not out["audio"] and (s.get("mp3_url") or s.get("ogg_url")):
            if not tags or tags & US_TAGS:
                out["audio"] = s.get("mp3_url") or s.get("ogg_url")
    pos = rec.get("pos") or "word"
    senses: list[dict] = []
    seen_defs: set[str] = set()
    for sense in rec.get("senses") or []:
        glosses = sense.get("glosses") or []
        if not glosses:
            continue
        gloss = glosses[0]
        for g in glosses:
            if not g.rstrip().endswith(":"):
                gloss = g
                break
        examples = [x["text"] for x in (sense.get("examples") or []) if x.get("text")]
        if not gloss:
            continue
        key = " ".join(gloss.split())
        if key in seen_defs:
            continue
        seen_defs.add(key)
        senses.append({"pos": pos, "definition": gloss,
                       "examples": examples[:MAX_EXAMPLES_PER_SENSE]})
        if len(senses) >= cap:
            break
    out["senses"] = senses
    return out


def load_kaikki_v3(shards_dir: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in glob.glob(os.path.join(shards_dir, "shards", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        word = rec.get("word")
        if not word:
            continue
        nw = norm(word)
        if rec.get("status") != "ok":
            continue
        merged = {"ipa_us": None, "ipa_uk": None, "ipa_generic": None, "audio": None, "senses": []}
        for entry in rec.get("entries") or []:
            d = distill_kaikki_record(entry, DEFAULT_SENSE_CAP)
            merged["ipa_us"] = merged["ipa_us"] or d["ipa_us"]
            merged["ipa_uk"] = merged["ipa_uk"] or d["ipa_uk"]
            merged["ipa_generic"] = merged["ipa_generic"] or d["ipa_generic"]
            merged["audio"] = merged["audio"] or d["audio"]
            for s in d["senses"]:
                key = " ".join(s["definition"].split())
                if not any(" ".join(x["definition"].split()) == key for x in merged["senses"]):
                    merged["senses"].append(s)
        out[nw] = merged
    return out


# ---------- v2 sqlite loader ----------

def load_v2(v2_db: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """returns ({nw: {entries, examples, original_source, was_degraded}}, {nw: pronunciation_json})"""
    conn = sqlite3.connect(f"file:{v2_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    words_out: dict[str, dict] = {}
    word_id_to_nw: dict[str, str] = {}
    for r in conn.execute("select id, normalized_text from words"):
        word_id_to_nw[r["id"]] = r["normalized_text"]
    cur = conn.execute("select id, word_id, sense_order, part_of_speech, sense_label, definition, definition_source from entries order by word_id, sense_order")
    wid_rows = conn.execute("select id from words").fetchall()
    for wid_row in wid_rows:
        wid = wid_row["id"]
        nw = word_id_to_nw[wid]
        words_out.setdefault(nw, {"entries": [], "examples": {}, "original_source": None, "was_degraded": False})
    ex_rows = list(conn.execute("select entry_id, sentence from entry_examples order by entry_id, example_order"))
    ex_by_entry: dict[str, list[str]] = {}
    for eid, sent in ex_rows:
        ex_by_entry.setdefault(eid, []).append(sent)
    wid_to_entry_ids: dict[str, list[str]] = {}
    for r in conn.execute("select id, word_id from entries"):
        wid_to_entry_ids.setdefault(r["word_id"], []).append(r["id"])
    for wid, eids in wid_to_entry_ids.items():
        nw = word_id_to_nw[wid]
        rec = words_out[nw]
        rec["entries"] = []
        for r in conn.execute("select id, sense_order, part_of_speech, sense_label, definition, definition_source from entries where word_id=? order by sense_order", (wid,)):
            rec["entries"].append({
                "sense_order": r["sense_order"], "pos": r["part_of_speech"],
                "sense_label": r["sense_label"] or "", "definition": r["definition"],
                "definition_source": r["definition_source"],
                "examples": ex_by_entry.get(r["id"], [])[:MAX_EXAMPLES_PER_SENSE],
            })
        sources = {e["definition_source"] for e in rec["entries"]}
        rec["original_source"] = "kaikki" if "open_api" in sources else ("ecdict" if "fallback" in sources else None)
        rec["was_degraded"] = not rec["entries"]
    pron_out: dict[str, dict] = {}
    for r in conn.execute("select normalized_word, response_json from pronunciation_cache"):
        pron_out[r["normalized_word"]] = json.loads(r["response_json"])
    conn.close()
    return words_out, pron_out


# ---------- fill-order resolver ----------

def build_entries_for_word(word: str, *,
                           v2_word: dict | None,
                           kaikki_word: dict | None,
                           wordnet_index: dict[str, list[dict]],
                           ecdict_index: dict[str, dict],
                           cap: int = DEFAULT_SENSE_CAP) -> dict:
    """returns {entries, primary_source, chinese_note, degraded}.

    priority: kaikki (reuse v2 or freshly fetched) → wordnet → ecdict (fallback)
    """
    # kaikki
    if v2_word and v2_word.get("entries") and v2_word.get("original_source") == "kaikki" and not v2_word.get("was_degraded"):
        entries = [{
            "pos": e["pos"], "definition": e["definition"],
            "examples": e["examples"], "definition_source": "open_api",
            "kaikki_sense_label": e.get("sense_label", ""),
        } for e in v2_word["entries"][:cap]]
        chinese_note = None
        _ec = ecdict_index.get(word) or ecdict_index.get(ALIASES.get(word, ""))
        if _ec:
            lines = [l for l in ecdict_lines(_ec.get("translation")) if l]
            if lines:
                chinese_note = lines[0]
        return {"entries": entries, "primary_source": "kaikki",
                "chinese_note": chinese_note, "degraded": False}
    if kaikki_word and kaikki_word.get("senses"):
        entries = []
        for s in kaikki_word["senses"][:cap]:
            entries.append({"pos": s["pos"], "definition": s["definition"],
                            "examples": s.get("examples", []),
                            "definition_source": "open_api"})
        chinese_note = None
        _ec = ecdict_index.get(word) or ecdict_index.get(ALIASES.get(word, ""))
        if _ec:
            lines = [l for l in ecdict_lines(_ec.get("translation")) if l]
            if lines:
                chinese_note = lines[0]
        return {"entries": entries, "primary_source": "kaikki",
                "chinese_note": chinese_note, "degraded": False}
    # wordnet (try variant forms: hyphen→space etc.)
    wn: list[dict] = []
    for v in lookup_variants(word):
        wn = wordnet_index.get(v) or []
        if wn:
            break
    if wn:
        entries = []
        for s in wn[:cap]:
            ex = [s["example"]] if s.get("example") else []
            entries.append({"pos": s["pos"], "definition": s["gloss"], "examples": ex,
                            "definition_source": "open_api"})
        chinese_note = None
        _ec = ecdict_index.get(word) or ecdict_index.get(ALIASES.get(word, ""))
        if _ec:
            lines = [l for l in ecdict_lines(_ec.get("translation")) if l]
            if lines:
                chinese_note = lines[0]
        return {"entries": entries, "primary_source": "wordnet",
                "chinese_note": chinese_note, "degraded": False}
    # ecdict fallback (try variant forms: hyphen→space, underscore→space, stripped);
    # skip rows whose English definition is empty so a later variant can still win
    for v in lookup_variants(word):
        erow = ecdict_index.get(v)
        if not erow:
            continue
        senses = ecdict_senses(erow, cap)
        if not senses:
            continue
        entries = [{"pos": s["pos"], "definition": s["definition"], "examples": [],
                    "definition_source": "fallback"} for s in senses]
        chinese_note = None
        lines = [l for l in ecdict_lines(erow.get("translation")) if l]
        if lines:
            chinese_note = lines[0]
        return {"entries": entries, "primary_source": "ecdict_fallback",
                "chinese_note": chinese_note, "degraded": False}
    return {"entries": [], "primary_source": None, "chinese_note": None, "degraded": True}


# ---------- pronunciation resolver ----------

def build_pronunciation(word: str, *,
                        v2_pron: dict | None,
                        kaikki_word: dict | None,
                        ecdict_index: dict[str, dict],
                        cmudict: dict[str, str],
                        tts_slug: str,
                        force_audio: bool = False) -> tuple[dict | None, str]:
    """returns (response_json dict with audioUrl included, pron_grade).

    audioUrl rule:
      - keep v2 audioUrl or kaikki audio (mp3_url) if present.
      - else serve /audio_v3/<tts_slug>.mp3 (TTS file must exist on disk).
    """
    us = uk = generic = audio_url = None
    source_url = f"https://en.wiktionary.org/wiki/{word.replace(' ', '_')}#English"
    if v2_pron:
        us = v2_pron.get("ipaUs")
        uk = v2_pron.get("ipaUk")
        generic = v2_pron.get("ipa")
        audio_url = v2_pron.get("audioUrl")
        if v2_pron.get("sourceUrl"):
            source_url = v2_pron["sourceUrl"]
    if kaikki_word:
        us = us or kaikki_word.get("ipa_us")
        uk = uk or kaikki_word.get("ipa_uk")
        generic = generic or kaikki_word.get("ipa_generic")
        audio_url = audio_url or kaikki_word.get("audio")

    alias = ALIASES.get(word)
    ec_phon = ((ecdict_index.get(word) or ecdict_index.get(alias or "") or {})
               .get("phonetic", "").strip() or None)

    grade = "none"
    if not us:
        cm = cmudict.get(word) or (cmudict.get(alias) if alias else None)
        if cm:
            ipa = arpabet_to_ipa(cm)
            if ipa:
                us = ipa
    if us and uk:
        grade = "dual"
    elif us and ec_phon:
        uk = ec_phon
        grade = "dual_ecdict"
    elif us or uk:
        grade = "single"
    elif generic:
        grade = "generic"
    elif ec_phon:
        grade = "ecdict"
    elif force_audio:
        # no IPA from any source, but the word still needs playable audio
        return {
            "word": word,
            "audioUrl": f"{AUDIO_REL_PATH}/{tts_slug}.mp3",
            "sourceUrl": source_url,
            "audioSource": "tts",
            "status": "ready",
        }, "audio_only"
    else:
        return None, grade

    if not audio_url:
        # TTS fall-back: assume file present in audio_v3/<slug>.mp3
        audio_url = f"{AUDIO_REL_PATH}/{tts_slug}.mp3"

    resp = {
        "word": word,
        "ipa": us or uk or generic or ec_phon,
        "ipaUk": uk,
        "ipaUs": us,
        "audioUrl": audio_url,
        "sourceUrl": source_url,
        "audioSource": ("wiktionary" if audio_url.startswith("http") and ("upload.wikimedia.org" in audio_url or "wikimedia" in audio_url)
                        else ("tts" if audio_url.startswith(AUDIO_REL_PATH + "/") else "other")),
        "status": "ready",
    }
    return {k: v for k, v in resp.items() if v is not None}, grade


# ---------- DB writer ----------

LICENSE_BLOCKS = [
    ("kaikki", "open_dictionary", "kaikki.org / wiktextract (English Wiktionary)",
     "https://kaikki.org/dictionary/English/",
     {"license": "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/), via kaikki.org / wiktextract",
      "notice": "Structured data excerpted from English Wiktionary (CC BY-SA 4.0)."}),
    ("wordnet", "open_dictionary", "Open English WordNet 2024",
     "https://github.com/globalwordnet/english-wordnet/releases/tag/2024-edition",
     {"license": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
      "notice": "Definitions and example sentences from Open English WordNet 2024 (CC BY 4.0)."}),
    ("ecdict", "open_dictionary", "ECDICT (skywind3000)",
     "https://github.com/skywind3000/ECDICT",
     {"license": "MIT (Copyright (c) 2025 Linwei)",
      "notice": "Dictionary data excerpted from ECDICT (MIT)."}),
    ("cmudict", "open_dictionary", "CMU Pronouncing Dictionary",
     "https://github.com/cmusphinx/cmudict",
     {"license": "BSD-style (http://www.speech.cs.cmu.edu/cgi-bin/cmudict)",
      "notice": "Pronunciations from CMU Pronouncing Dictionary."}),
    ("edge_tts", "open_dictionary", "edge-tts (Microsoft Azure TTS)",
     "https://github.com/rany2/edge-tts",
     {"license": "edge-tts (MIT); voices © Microsoft Azure TTS (per Microsoft's online TTS terms)",
      "notice": "Audio for words lacking Wiktionary audio generated via edge-tts (Microsoft Azure TTS, en-US-AriaNeural)."}),
]


def write_db(db_path: str, *, words_in: list[dict], schema_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
    with open(schema_path, encoding="utf-8") as f:
        schema = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    now = utc_now()
    # multiple sources rows with license
    for src_id_short, stype, name, path_or_url, meta in LICENSE_BLOCKS:
        conn.execute(
            "insert into sources (id, type, name, path_or_url, metadata_json, created_at) values (?,?,?,?,?,?)",
            (new_id(), stype, name, path_or_url, json.dumps(meta, ensure_ascii=False), now))
    for w in words_in:
        word = w["word"]
        wid = new_id()
        conn.execute("insert into words (id, text, normalized_text, created_at, updated_at) values (?,?,?,?,?)",
                     (wid, word, norm(word), now, now))
        entries = w["entries"]
        for i, ent in enumerate(entries, start=1):
            eid = new_id()
            label = ent.get("kaikki_sense_label", "") if ent.get("definition_source") == "open_api" and ent.get("primary_source") == "kaikki" else ""
            conn.execute(
                "insert into entries (id, word_id, sense_order, part_of_speech, sense_label, definition,"
                " definition_source, chinese_note, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)",
                (eid, wid, i, ent["pos"], label, ent["definition"],
                 ent["definition_source"], w.get("chinese_note"), now, now))
            for j, sent in enumerate(ent.get("examples") or [], start=1):
                conn.execute(
                    "insert into entry_examples (id, entry_id, example_order, sentence, source, is_primary,"
                    " created_at, updated_at) values (?,?,?,?,?,?,?,?)",
                    (new_id(), eid, j, sent, "imported", 1 if j == 1 else 0, now, now))
        if w["pronunciation"]:
            conn.execute(
                "insert into pronunciation_cache (normalized_word, response_json, status, retry_after, cached_at)"
                " values (?,?,?,?,?)",
                (norm(word), json.dumps(w["pronunciation"], ensure_ascii=False), "ready", None, now))
    conn.commit()
    conn.close()


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


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--words-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--v2-db", required=True)
    ap.add_argument("--kaikki-v3-dir", required=True)
    ap.add_argument("--wordnet-dir", required=True)
    ap.add_argument("--ecdict", required=True)
    ap.add_argument("--cmudict", required=True)
    ap.add_argument("--audio-dir", required=True,
                    help="dir containing TTS mp3 files; filenames <slug>.mp3")
    ap.add_argument("--schema", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo", "backend", "app", "schema.sql"))
    a = ap.parse_args()

    with open(a.words_file, encoding="utf-8") as f:
        words = [l.strip() for l in f if l.strip()]
    prod = {norm(w): w for w in words}

    print("loading v2 base...", file=sys.stderr, flush=True)
    v2_words, v2_pron = load_v2(a.v2_db)
    v2_norm_set = set(v2_words)
    print(f"v2 words: {len(v2_norm_set)}; v2 pron: {len(v2_pron)}", file=sys.stderr, flush=True)

    print("loading kaikki v3 shards...", file=sys.stderr, flush=True)
    kaikki_v3 = load_kaikki_v3(a.kaikki_v3_dir)
    print(f"kaikki v3 words: {len(kaikki_v3)}", file=sys.stderr, flush=True)

    print("loading wordnet...", file=sys.stderr, flush=True)
    wordnet_index = load_wordnet(a.wordnet_dir)
    print(f"wordnet index: {len(wordnet_index)}", file=sys.stderr, flush=True)

    print("loading cmudict...", file=sys.stderr, flush=True)
    cmudict = load_cmudict(a.cmudict)
    print(f"cmudict: {len(cmudict)}", file=sys.stderr, flush=True)

    print("loading ecdict (full)...", file=sys.stderr, flush=True)
    union_words = sorted(set(prod) | v2_norm_set)
    # precompute stripped/variant forms so we can find hyphenated/underscored entries
    wanted_for_ecdict: set[str] = set()
    for w in union_words:
        for v in lookup_variants(w):
            wanted_for_ecdict.add(v)
    ecdict_index = load_ecdict(a.ecdict, wanted=wanted_for_ecdict)
    print(f"ecdict index: {len(ecdict_index)}", file=sys.stderr, flush=True)

    # iterate
    built: list[dict] = []
    report_rows: list[dict] = []
    for nw in union_words:
        display = prod.get(nw) or nw  # original display form (may differ for v2-extra)
        v2w = v2_words.get(nw)
        k3 = kaikki_v3.get(nw) or kaikki_v3.get(ALIASES.get(nw, ""))
        ent = build_entries_for_word(nw, v2_word=v2w, kaikki_word=k3,
                                     wordnet_index=wordnet_index, ecdict_index=ecdict_index)
        if ent["degraded"] and nw in prod:
            # best-effort placeholder so every prod word keeps an English entry
            # (definition_source='fallback'; no UI-level distinction).
            ent["entries"] = [{
                "pos": "word",
                "definition": "Definition not yet available from open dictionary sources.",
                "examples": [],
                "definition_source": "fallback",
            }]
            ent["primary_source"] = "placeholder"
        slug = slug_for_audio(nw)
        tts_exists = os.path.exists(os.path.join(a.audio_dir, slug + ".mp3"))
        pron, pgrade = build_pronunciation(nw, v2_pron=v2_pron.get(nw),
                                            kaikki_word=k3, ecdict_index=ecdict_index,
                                            cmudict=cmudict, tts_slug=slug,
                                            force_audio=(nw in prod) or tts_exists)
        bw = {
            "word": display, "entries": ent["entries"],
            "chinese_note": ent["chinese_note"], "degraded": ent["degraded"],
            "primary_source": ent["primary_source"], "pronunciation": pron, "pron_grade": pgrade,
        }
        built.append(bw)
        report_rows.append({
            "word": display, "source": ent["primary_source"], "degraded": ent["degraded"],
            "pron_grade": pgrade, "senses": len(ent["entries"]),
            "has_audio": bool(pron and pron.get("audioUrl")),
            "has_definition": bool(ent["entries"]),
        })

    write_db(a.out, words_in=built, schema_path=a.schema)
    scan = oxford_residual_scan(a.out)
    summary = {
        "words": len(built),
        "by_source": {},
        "degraded_words": [r["word"] for r in report_rows if r["degraded"]],
        "ecdict_fallback_words": [r["word"] for r in report_rows if r["source"] == "ecdict_fallback"],
        "wordnet_filled_words": [r["word"] for r in report_rows if r["source"] == "wordnet"],
        "pron_grades": {},
        "with_audio": sum(1 for r in report_rows if r["has_audio"]),
        "with_definition": sum(1 for r in report_rows if r["has_definition"]),
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