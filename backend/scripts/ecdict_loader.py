"""Stream-load ECDICT ecdict.csv into an in-memory {word_norm: row_dict} index."""
from __future__ import annotations
import csv
from typing import Iterable


def load_ecdict(path: str, wanted: Iterable[str] | None = None) -> dict[str, dict]:
    """Returns {word_norm: row_dict}.  If wanted is provided, only keep those words.

    Optimization: use csv.reader (faster than DictReader) and only construct the
    dict for rows we actually want to keep.
    """
    wanted_set = set(wanted) if wanted is not None else None
    if wanted_set is not None:
        # Also accept underscore variants
        wanted_dash = {w.replace(" ", "_") for w in wanted_set}
    else:
        wanted_dash = None
    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        # sniff header
        header = f.readline().rstrip("\n").split(",")
        # ECDICT header: word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio
        # We need: word, phonetic, definition, translation, pos
        col_idx = {name: i for i, name in enumerate(header)}
        w_col = col_idx["word"]
        p_col = col_idx.get("phonetic", 1)
        d_col = col_idx.get("definition", 2)
        t_col = col_idx.get("translation", 3)
        pos_col = col_idx.get("pos", 4)
        for row in csv.reader(f):
            if w_col >= len(row):
                continue
            w = row[w_col].strip().lower()
            if not w:
                continue
            if wanted_dash is not None and w not in wanted_set and w not in wanted_dash:
                continue
            out[w] = {
                "word": w,
                "phonetic": (row[p_col].strip() if p_col < len(row) else "") or "",
                "definition": (row[d_col].strip() if d_col < len(row) else "") or "",
                "translation": (row[t_col].strip() if t_col < len(row) else "") or "",
                "pos": (row[pos_col].strip() if pos_col < len(row) else "") or "",
            }
    return out


def ecdict_lines(text: str) -> list[str]:
    return [l.strip() for l in (text or "").replace("\\n", "\n").splitlines()]


def ecdict_senses(row: dict, cap: int = 5) -> list[dict]:
    out: list[dict] = []
    pos = (row.get("pos") or "").split("/")[0].strip() or None
    pos_map = {"n": "noun", "v": "verb", "vt": "verb", "vi": "verb", "adj": "adjective",
               "a": "adjective", "adv": "adverb", "ad": "adverb", "prep": "preposition",
               "pron": "pronoun", "conj": "conjunction", "num": "number", "int": "exclamation"}
    pos = pos_map.get(pos, pos or "word")
    seen: set[str] = set()
    for line in ecdict_lines(row.get("definition")):
        if not line or line.startswith("["):
            continue
        key = " ".join(line.split())
        if key in seen:
            continue
        seen.add(key)
        out.append({"pos": pos, "definition": line, "examples": []})
        if len(out) >= cap:
            break
    return out