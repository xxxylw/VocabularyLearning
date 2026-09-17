"""Unit tests for scripts/build_open_dictionary.py (offline open-source dictionary builder).

All fixtures are tiny inline wiktextract/ECDICT records — no network, no large files.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.build_open_dictionary import (  # noqa: E402
    DEFAULT_SENSE_CAP,
    MAX_EXAMPLES_PER_SENSE,
    build_pronunciation,
    build_word,
    distill_kaikki,
    ecdict_lines,
    ecdict_senses,
    load_ecdict,
    norm,
    oxford_residual_scan,
    strip_diacritics,
    write_db,
)

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")


def kaikki_entry(pos="noun", sounds=None, senses=None):
    return {"pos": pos, "sounds": sounds or [], "senses": senses or []}


def sense(glosses, examples=None, tags=None):
    return {
        "glosses": glosses,
        "examples": [{"text": t} for t in (examples or [])],
        "tags": tags or [],
    }


class TestDistillKaikki:
    def test_dual_ipa_and_us_audio_preferred(self):
        data = [kaikki_entry(sounds=[
            {"ipa": "/uk/", "tags": ["Received-Pronunciation"]},
            {"ipa": "/us/", "tags": ["US"], "mp3_url": "https://commons/us.mp3"},
            {"ipa": "/gen/"},
        ])]
        d = distill_kaikki(data, cap=5)
        assert d["ipa_us"] == "/us/"
        assert d["ipa_uk"] == "/uk/"
        assert d["ipa_generic"] == "/gen/"
        assert d["audio"] == "https://commons/us.mp3"

    def test_general_american_counts_as_us(self):
        data = [kaikki_entry(sounds=[{"ipa": "/ga/", "tags": ["General-American"]}])]
        assert distill_kaikki(data, 5)["ipa_us"] == "/ga/"

    def test_header_gloss_skipped_for_real_gloss(self):
        data = [kaikki_entry(senses=[sense(
            ["As an auxiliary verb:", "Used to form the passive voice."],
        )])]
        d = distill_kaikki(data, 5)
        assert d["senses"][0]["definition"] == "Used to form the passive voice."

    def test_form_of_senses_deprioritized(self):
        data = [kaikki_entry(senses=[
            sense(["present participle of outstand"], tags=["form-of"]),
            sense(["Exceptionally good."], examples=["an outstanding move"]),
            sense(["Projecting outwards."]),
        ])]
        d = distill_kaikki(data, cap=3)
        defs = [s["definition"] for s in d["senses"]]
        assert defs[0] == "Exceptionally good."
        assert defs[-1] == "present participle of outstand"  # filler at the tail

    def test_form_of_fills_when_not_enough_real_senses(self):
        data = [kaikki_entry(senses=[
            sense(["present participle of outstand"], tags=["form-of"]),
            sense(["Exceptionally good."]),
        ])]
        d = distill_kaikki(data, cap=3)
        assert len(d["senses"]) == 2

    def test_cap_and_examples_limit(self):
        senses = [sense([f"gloss {i}"], examples=["e1", "e2", "e3"]) for i in range(10)]
        d = distill_kaikki([kaikki_entry(senses=senses)], cap=3)
        assert len(d["senses"]) == 3
        assert all(len(s["examples"]) == MAX_EXAMPLES_PER_SENSE for s in d["senses"])

    def test_pos_mapped(self):
        d = distill_kaikki([kaikki_entry(pos="adj", senses=[sense(["good"])])], 5)
        assert d["senses"][0]["part_of_speech"] == "adjective"

    def test_duplicate_glosses_across_entries_deduped(self):
        # QA 缺陷3：同一 gloss 在多个 etymology/POS 小节重复（yoke/the 各 5 条全同）
        e1 = kaikki_entry(pos="noun", senses=[sense(["Senses relating to a frame around the neck."], examples=["ex1"])])
        e2 = kaikki_entry(pos="noun", senses=[sense(["Senses relating to a frame around the neck."], examples=["ex2"])])
        d = distill_kaikki([e1, e2], cap=5)
        assert len(d["senses"]) == 1
        assert d["senses"][0]["examples"] == ["ex1", "ex2"]  # 重复义项的例句并入首条

    def test_dedupe_happens_before_cap(self):
        dup = kaikki_entry(senses=[sense(["same gloss"])] )
        reals = kaikki_entry(senses=[sense([f"distinct {i}"]) for i in range(5)])
        d = distill_kaikki([dup, dup, dup, reals], cap=5)
        defs = [s["definition"] for s in d["senses"]]
        assert defs == ["same gloss"] + [f"distinct {i}" for i in range(4)]

    def test_whitespace_variants_treated_as_duplicates(self):
        data = [kaikki_entry(senses=[sense(["a  b"]), sense(["a b"])])]
        assert len(distill_kaikki(data, 5)["senses"]) == 1

    def test_same_gloss_untagged_and_alt_of_kept_once(self):
        # 真实案例 basement：同一 gloss 一处无标签、另一处带 alt-of 标签
        data = [kaikki_entry(senses=[
            sense(["A floor of a building below ground level."], examples=["a dark basement"]),
            sense(["A floor of a building below ground level."], tags=["abbreviation", "alt-of", "ellipsis"]),
        ])]
        d = distill_kaikki(data, 5)
        assert len(d["senses"]) == 1
        assert d["senses"][0]["examples"] == ["a dark basement"]

    def test_form_of_first_then_real_sense_upgrades(self):
        # 形态义项先出现、真实义项后出现同定义：保留真实义项
        data = [kaikki_entry(senses=[
            sense(["Initialism of compact disc."], tags=["alt-of"], examples=["form ex"]),
            sense(["Initialism of compact disc."], examples=["real ex"]),
        ])]
        d = distill_kaikki(data, 5)
        assert len(d["senses"]) == 1
        assert "real ex" in d["senses"][0]["examples"]


class TestBuildPronunciation:
    def erow(self, phonetic=""):
        return {"phonetic": phonetic} if phonetic else None

    def test_dual(self):
        _, grade = build_pronunciation("w", {"ipa_us": "/u/", "ipa_uk": "/k/", "ipa_generic": None, "audio": None}, None)
        assert grade == "dual"

    def test_us_plus_ecdict_fills_uk(self):
        resp, grade = build_pronunciation("w", {"ipa_us": "/u/", "ipa_uk": None, "ipa_generic": None, "audio": None}, self.erow("ecUK"))
        assert grade == "dual_ecdict"
        assert resp["ipaUk"] == "ecUK"
        assert resp["ipaUs"] == "/u/"

    def test_uk_only_is_single_not_fake_dual(self):
        # ECDICT 音标以英音为主，不能拿它补美音槽位假装双标
        resp, grade = build_pronunciation("w", {"ipa_us": None, "ipa_uk": "/k/", "ipa_generic": None, "audio": None}, self.erow("ecUK"))
        assert grade == "single"
        assert "ipaUs" not in resp

    def test_generic_then_ecdict_then_none(self):
        _, g1 = build_pronunciation("w", {"ipa_us": None, "ipa_uk": None, "ipa_generic": "/g/", "audio": None}, self.erow("ec"))
        _, g2 = build_pronunciation("w", None, self.erow("ec"))
        resp3, g3 = build_pronunciation("w", None, None)
        assert (g1, g2, g3) == ("generic", "ecdict", "none")
        assert resp3 is None


class TestEcdict:
    def test_literal_newlines_restored(self):
        assert ecdict_lines("a\\nb\\n c ") == ["a", "b", "c"]

    def test_senses_skip_bracket_lines_and_cap(self):
        erow = {"pos": "vt/50", "definition": "[习语] x\\nto sort\\nto classify\\nthird"}
        out = ecdict_senses(erow, cap=2)
        assert [s["definition"] for s in out] == ["to sort", "to classify"]
        assert out[0]["part_of_speech"] == "verb"

    def test_chinese_note_first_line(self):
        erow = {"translation": "v. 是, 表示\\n[计] 后端", "definition": "d", "pos": "v"}
        bw = build_word("be", None, erow, DEFAULT_SENSE_CAP)
        assert bw["chinese_note"] == "v. 是, 表示"

    def test_senses_dedupe_identical_lines(self):
        erow = {"pos": "n", "definition": "a thing\\na thing\\nanother"}
        out = ecdict_senses(erow, cap=5)
        assert [s["definition"] for s in out] == ["a thing", "another"]


class TestStripDiacritics:
    def test_common_marks_removed(self):
        assert strip_diacritics("naïve") == "naive"
        assert strip_diacritics("café") == "cafe"
        assert strip_diacritics("résumé") == "resume"
        assert strip_diacritics("coup d'état") == "coup d'etat"

    def test_latin_extended_mapped(self):
        assert strip_diacritics("æon") == "aeon"
        assert strip_diacritics("œuvre") == "oeuvre"

    def test_ascii_unchanged(self):
        assert strip_diacritics("plain word") == "plain word"


class TestLoadEcdictFallback:
    def test_stripped_fallback_for_diacritic_words(self, tmp_path):
        csv_path = tmp_path / "ecdict.csv"
        csv_path.write_text(
            "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            "naive,nɑ:'i:v,adj. 天真的,adj. 天真的,adj,,,,,,,,\n"
            "cafe,kæ'fei,n. 咖啡馆,n. 咖啡馆,n,,,,,,,,\n",
            encoding="utf-8",
        )
        idx, stripped = load_ecdict(str(csv_path), {"naïve", "café", "naive"})
        assert "naive" in idx                      # 精确命中照常
        assert stripped["naïve"]["phonetic"] == "nɑ:'i:v"  # QA 缺陷5：带符词兜底命中无符 ECDICT 行
        assert "café" in stripped
        assert "naive" not in stripped             # 精确已命中，不进兜底索引

    def test_naive_build_gets_ecdict_pron_and_keeps_kaikki_audio(self):
        # naïve 的 kaikki 记录只有音频没有 IPA；ECDICT（naive 行）补音标
        k = {"ipa_us": None, "ipa_uk": None, "ipa_generic": None,
             "audio": "https://commons/naive.mp3",
             "senses": [{"part_of_speech": "adjective", "definition": "Lacking experience.",
                         "sense_label": "", "examples": []}]}
        erow = {"phonetic": "nɑ:'i:v", "definition": "", "translation": "adj. 天真的", "pos": "adj"}
        bw = build_word("naïve", k, erow, DEFAULT_SENSE_CAP)
        assert bw["pron_grade"] == "ecdict"
        assert bw["pronunciation"]["ipa"] == "nɑ:'i:v"
        assert bw["pronunciation"]["audioUrl"] == "https://commons/naive.mp3"
        assert bw["source"] == "kaikki"  # 义项仍来自 kaikki


class TestWriteDbAndScan:
    def _two_words(self):
        return [
            {"word": "café", "pronunciation": {"word": "café", "ipa": "/ˈkæfeɪ/", "ipaUk": "/ˈkæfeɪ/", "ipaUs": "/ˌkæˈfeɪ/", "status": "ready"},
             "pron_grade": "dual", "chinese_note": "n. 咖啡馆",
             "entries": [{"part_of_speech": "noun", "sense_label": "", "definition": "A coffee shop.", "examples": ["He sat in the cafe."]}],
             "degraded": False, "source": "kaikki"},
            {"word": "mnst", "pronunciation": None, "pron_grade": "none", "chinese_note": None,
             "entries": [], "degraded": True, "source": None},
        ]

    def test_roundtrip_and_scan_pass(self, tmp_path):
        db = str(tmp_path / "out.sqlite")
        write_db(db, self._two_words(), SCHEMA)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        w = conn.execute("select * from words where normalized_text='café'").fetchone()
        assert w is not None
        e = conn.execute("select * from entries where word_id=?", (w["id"],)).fetchone()
        assert e["definition_source"] == "open_api"
        ex = conn.execute("select * from entry_examples where entry_id=?", (e["id"],)).fetchone()
        assert ex["source"] == "imported"
        p = conn.execute("select * from pronunciation_cache where normalized_word='café'").fetchone()
        assert json.loads(p["response_json"])["ipaUs"] == "/ˌkæˈfeɪ/"
        src = conn.execute("select * from sources").fetchone()
        meta = json.loads(src["metadata_json"])
        assert "CC BY-SA" in meta["license"]["wiktionary"] and "MIT" in meta["license"]["ecdict"]
        conn.close()
        scan = oxford_residual_scan(db)
        assert scan == {"oxford_url_hits": 0, "oxford_source_rows": 0, "pass": True}

    def test_scan_flags_oxford_residue(self, tmp_path):
        db = str(tmp_path / "out.sqlite")
        words = self._two_words()
        words[0]["entries"][0]["definition"] = "see oxfordlearnersdictionaries.com/x"
        write_db(db, words, SCHEMA)
        scan = oxford_residual_scan(db)
        assert scan["oxford_url_hits"] == 1 and scan["pass"] is False

    def test_ecdict_source_word_tagged_fallback(self, tmp_path):
        # QA 缺陷2：ECDICT 兜底词的 entry 必须标 fallback，不能混进 open_api
        words = self._two_words()
        words.append({
            "word": "ecword", "pronunciation": None, "pron_grade": "ecdict",
            "chinese_note": "n. 兜底词",
            "entries": [{"part_of_speech": "noun", "sense_label": "", "definition": "A fallback def.", "examples": []}],
            "degraded": False, "source": "ecdict",
        })
        db = str(tmp_path / "out.sqlite")
        write_db(db, words, SCHEMA)
        conn = sqlite3.connect(db)
        tags = {r[0]: r[1] for r in conn.execute(
            "select w.normalized_text, e.definition_source from entries e join words w on w.id=e.word_id")}
        conn.close()
        assert tags["café"] == "open_api"
        assert tags["ecword"] == "fallback"


class TestMainEndToEnd:
    def test_tiny_build(self, tmp_path):
        kdir = tmp_path / "shards"
        kdir.mkdir()
        rec = {"word": "assort", "status": "ok", "form": "assort", "data": [kaikki_entry(
            pos="verb",
            sounds=[{"ipa": "/əˈsɔːt/", "tags": ["UK"]}, {"ipa": "/əˈsɔɹt/", "tags": ["US"]}],
            senses=[sense(["To sort according to characteristic."], examples=["assorted items"])],
        )]}
        (kdir / "kaikki_a.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        ecdict = tmp_path / "ecdict.csv"
        ecdict.write_text("word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
                          "assort,,\"vt. 把...分类\",\"vt. 分类\",vt,,,,,,,,\n"
                          "econly,ɪ'kɒnli,\"n. only in ecdict\",\"n. 仅 ECDICT\",n,,,,,,,,\n",
                          encoding="utf-8")
        words = tmp_path / "words.txt"
        words.write_text("assort\nmissingword\neconly\n", encoding="utf-8")
        out = tmp_path / "out.sqlite"
        report = tmp_path / "report.json"
        from scripts.build_open_dictionary import main
        argv = sys.argv
        try:
            sys.argv = ["x", "--words-file", str(words), "--out", str(out), "--report", str(report),
                        "--kaikki-dir", str(kdir), "--ecdict", str(ecdict)]
            main()
        finally:
            sys.argv = argv
        rep = json.loads(report.read_text(encoding="utf-8"))
        s = rep["summary"]
        assert s["words"] == 3
        assert s["by_source"] == {"kaikki": 1, "none": 1, "ecdict": 1}
        assert s["ecdict_fallback_words"] == ["econly"]
        assert s["degraded"] == ["missingword"]
        assert s["duplicate_definitions"] == 0
        assert s["pron_grades"]["dual"] == 1
        assert s["pron_grades"]["ecdict"] == 1
        assert s["oxford_scan"]["pass"] is True
        # ECDICT 兜底词入库标签为 fallback，kaikki 词保持 open_api
        conn = sqlite3.connect(out)
        tags = {r[0]: r[1] for r in conn.execute(
            "select w.normalized_text, e.definition_source from entries e join words w on w.id=e.word_id")}
        conn.close()
        assert tags == {"assort": "open_api", "econly": "fallback"}
