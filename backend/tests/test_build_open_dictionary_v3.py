"""Regression tests for scripts/build_open_dictionary_v3.py.

Covers:
- helper loaders (arpabet_ipa / wordnet_loader / ecdict_loader) smoke-level behavior;
- fill-order: kaikki (v2 reuse or fresh) → wordnet → ecdict fallback → degraded,
  including hyphen→space variant lookup;
- oxford residual scan on a freshly written DB;
- force_audio: prod words with no IPA still get a playable TTS audioUrl.

All fixtures are tiny inline records — no network, no large files.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.arpabet_ipa import arpabet_to_ipa, load_cmudict  # noqa: E402
from scripts.wordnet_loader import load_wordnet  # noqa: E402
from scripts.ecdict_loader import load_ecdict, ecdict_senses  # noqa: E402
from scripts.build_open_dictionary_v3 import (  # noqa: E402
    build_entries_for_word,
    build_pronunciation,
    lookup_variants,
    oxford_residual_scan,
    strip_diacritics,
    write_db,
)

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")


# ---------- helper smoke tests ----------

def test_arpabet_to_ipa_stress_and_mapping():
    assert arpabet_to_ipa("K AE1 T") == "kˈæt"
    assert arpabet_to_ipa("HH AH0 L OW1") == "həlˈoʊ"
    assert arpabet_to_ipa("") is None or arpabet_to_ipa("") == ""


def test_lookup_variants_forms():
    assert lookup_variants("fire-engine") == ["fire-engine", "fire engine"]
    assert lookup_variants("a_b") == ["a_b", "a b"]
    assert "cafe" in lookup_variants("café")
    assert lookup_variants("plain") == ["plain"]


def test_strip_diacritics():
    assert strip_diacritics("café") == "cafe"
    assert strip_diacritics("æon") == "aeon"


# ---------- fill-order ----------

def _kaikki(senses):
    return {"ipa_us": None, "ipa_uk": None, "ipa_generic": None,
            "audio": None, "senses": senses}


def test_fill_order_kaikki_beats_wordnet_and_ecdict():
    k3 = _kaikki([{"pos": "noun", "definition": "kaikki gloss", "examples": []}])
    wn = {"cat": [{"pos": "n", "gloss": "wordnet gloss", "example": ""}]}
    ec = {"cat": {"phonetic": "kæt", "translation": "n. 猫", "definition": "ecdict def"}}
    out = build_entries_for_word("cat", v2_word=None, kaikki_word=k3,
                                 wordnet_index=wn, ecdict_index=ec)
    assert out["primary_source"] == "kaikki"
    assert out["entries"][0]["definition"] == "kaikki gloss"
    assert out["entries"][0]["definition_source"] == "open_api"
    assert not out["degraded"]


def test_fill_order_v2_kaikki_reuse_beats_fresh_sources():
    v2w = {"entries": [{"pos": "noun", "sense_label": "", "definition": "v2 gloss",
                        "definition_source": "open_api", "examples": []}],
           "examples": {}, "original_source": "kaikki", "was_degraded": False}
    k3 = _kaikki([{"pos": "noun", "definition": "fresh gloss", "examples": []}])
    out = build_entries_for_word("cat", v2_word=v2w, kaikki_word=k3,
                                 wordnet_index={}, ecdict_index={})
    assert out["primary_source"] == "kaikki"
    assert out["entries"][0]["definition"] == "v2 gloss"


def test_fill_order_wordnet_when_kaikki_absent():
    wn = {"cat": [{"pos": "n", "gloss": "wordnet gloss", "example": "a cat sits"}]}
    ec = {"cat": {"phonetic": "kæt", "translation": "n. 猫", "definition": "ecdict def"}}
    out = build_entries_for_word("cat", v2_word=None, kaikki_word=None,
                                 wordnet_index=wn, ecdict_index=ec)
    assert out["primary_source"] == "wordnet"
    assert out["entries"][0]["definition"] == "wordnet gloss"
    assert out["entries"][0]["definition_source"] == "open_api"
    assert out["entries"][0]["examples"] == ["a cat sits"]


def test_fill_order_ecdict_fallback_with_hyphen_variant():
    ec = {"fire engine": {"phonetic": "", "translation": "n. 消防车",
                          "definition": "n. a vehicle for firefighting"}}
    out = build_entries_for_word("fire-engine", v2_word=None, kaikki_word=None,
                                 wordnet_index={}, ecdict_index=ec)
    assert out["primary_source"] == "ecdict_fallback"
    assert out["entries"]
    assert out["entries"][0]["definition_source"] == "fallback"


def test_fill_order_degraded_when_nothing_found():
    out = build_entries_for_word("zzznosuchword", v2_word=None, kaikki_word=None,
                                 wordnet_index={}, ecdict_index={})
    assert out["degraded"]
    assert out["entries"] == []
    assert out["primary_source"] is None


# ---------- force_audio ----------

def test_force_audio_gives_tts_url_without_ipa():
    pron, grade = build_pronunciation("babyboom", v2_pron=None, kaikki_word=None,
                                      ecdict_index={}, cmudict={}, tts_slug="babyboom",
                                      force_audio=True)
    assert grade == "audio_only"
    assert pron["audioUrl"] == "/audio_v3/babyboom.mp3"
    assert pron["audioSource"] == "tts"


def test_no_force_audio_returns_none_without_ipa():
    pron, grade = build_pronunciation("babyboom", v2_pron=None, kaikki_word=None,
                                      ecdict_index={}, cmudict={}, tts_slug="babyboom")
    assert pron is None
    assert grade == "none"


# ---------- oxford residual scan ----------

def _tiny_db(tmp_path, definition="a small domesticated carnivore"):
    db = str(tmp_path / "tiny.sqlite")
    write_db(db, words_in=[{
        "word": "cat",
        "entries": [{"pos": "noun", "definition": definition, "examples": ["the cat sat"],
                     "definition_source": "open_api"}],
        "chinese_note": None, "degraded": False, "primary_source": "kaikki",
        "pronunciation": {"word": "cat", "ipa": "kæt", "audioUrl": "/audio_v3/cat.mp3",
                          "sourceUrl": "https://en.wiktionary.org/wiki/cat#English",
                          "audioSource": "tts", "status": "ready"},
        "pron_grade": "single",
    }], schema_path=SCHEMA)
    return db


def test_oxford_scan_clean_db_passes(tmp_path):
    scan = oxford_residual_scan(_tiny_db(tmp_path))
    assert scan == {"oxford_url_hits": 0, "oxford_source_rows": 0, "pass": True}


def test_oxford_scan_detects_residual_url(tmp_path):
    db = _tiny_db(tmp_path, definition="see https://www.oxfordlearnersdictionaries.com/x")
    scan = oxford_residual_scan(db)
    assert scan["oxford_url_hits"] >= 1
    assert not scan["pass"]


# ---------- alias + empty-sense variant tests (v3 coverage fixes) ----------

def test_alias_resolves_to_wordnet_target():
    """An OCR-noise book word with a curated alias resolves via the alias target."""
    from scripts.build_open_dictionary_v3 import ALIASES
    target = ALIASES["babyboom"]
    wordnet_index = {target: [{"pos": "noun", "gloss": "a surge in births", "example": ""}]}
    ent = build_entries_for_word("babyboom", v2_word=None, kaikki_word=None,
                                 wordnet_index=wordnet_index, ecdict_index={})
    assert ent["primary_source"] == "wordnet"
    assert not ent["degraded"]
    assert ent["entries"][0]["definition"] == "a surge in births"


def test_ecdict_empty_definition_falls_through_to_variant():
    """A variant whose ECDICT row has no English definition must not shadow a
    later variant that does have one."""
    ecdict_index = {
        "circum": {"word": "circum", "phonetic": "", "definition": "", "translation": "", "pos": ""},
        "circum-": {"word": "circum-", "phonetic": "", "definition": "A Latin prefix.\\nsignifying around.",
                    "translation": "", "pos": ""},
    }
    ent = build_entries_for_word("circum", v2_word=None, kaikki_word=None,
                                 wordnet_index={}, ecdict_index=ecdict_index)
    assert ent["primary_source"] == "ecdict_fallback"
    assert not ent["degraded"]
    assert "Latin prefix" in ent["entries"][0]["definition"]


def test_alias_present_for_all_curated_words():
    """Every curated alias key is a non-empty lowercase-normalized string."""
    from scripts.build_open_dictionary_v3 import ALIASES
    assert len(ALIASES) >= 30
    for k, v in ALIASES.items():
        assert k and v and k == k.strip() and v == v.strip()
