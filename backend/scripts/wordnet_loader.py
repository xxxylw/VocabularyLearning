"""Parse Open English WordNet 2024 classic DB files (index.*/data.*) into a
word -> senses index, used by the v3 build to fill in gaps after kaikki.

Single pass over data.<pos> files; senses are kept in scan order, capped at
MAX_SENSES per word; satellite adjectives (s) are kept alongside (a) under
the 'adjective' POS.

Lemma lookup is normalized lowercase; both 'ice_cream' and 'ice cream' forms
are indexed for multiword entries.
"""
from __future__ import annotations
import os, re
from typing import Iterable

POS_FILES = {  # WN ss_type -> (filename, ui pos)
    "n": ("data.noun", "noun"),
    "v": ("data.verb", "verb"),
    "a": ("data.adj", "adjective"),
    "s": ("data.adj", "adjective"),  # satellites
    "r": ("data.adv", "adverb"),
}
MAX_SENSES = 5
EX_RE = re.compile(r';\s*"([^"]{4,200})"')

# strip trailing marker like "(a)" used for satellite senses
SAT_RE = re.compile(r"\s*\([a-zA-Z]+\)\s*$")


def _norm(word: str) -> str:
    return word.replace("_", " ").strip().lower()


def load_wordnet(wn_dir: str) -> dict[str, list[dict]]:
    """Returns {normalized_word: [sense_dict, ...]} sorted by scan order, capped.

    sense_dict = {pos, gloss, example_or_None, definition}
    """
    idx: dict[str, list[dict]] = {}
    for ss_type, (fname, pos) in POS_FILES.items():
        path = os.path.join(wn_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line or line[0].isdigit() is False:
                    continue
                if " | " not in line:
                    continue
                rel, gloss_raw = line.split(" | ", 1)
                gloss_raw = gloss_raw.strip()
                if not gloss_raw:
                    continue
                # parse rels: tokens[0]=offset, [1]=filenum, [2]=ss_type, [3]=w_cnt, then w_cnt pairs (word lex_id)
                toks = rel.split()
                if len(toks) < 4:
                    continue
                try:
                    w_cnt = int(toks[3])
                except ValueError:
                    continue
                words_in_syn: list[str] = []
                for i in range(w_cnt):
                    idx_word = 4 + i * 2
                    if idx_word >= len(toks):
                        break
                    w_raw = toks[idx_word]
                    # satellites include a head marker after the words: w_cnt triples may have an extra head token
                    w_clean = SAT_RE.sub("", w_raw)
                    words_in_syn.append(_norm(w_clean))
                if not words_in_syn:
                    continue
                # skip trailing junk before '|' (pointer markers for satellites)
                ex_match = EX_RE.search(gloss_raw)
                example = ex_match.group(1) if ex_match else None
                gloss = EX_RE.split(gloss_raw)[0].strip(" ;.") or gloss_raw[:200]
                sense = {
                    "pos": pos,
                    "gloss": gloss,
                    "example": example,
                }
                for w in words_in_syn:
                    if not w:
                        continue
                    bucket = idx.setdefault(w, [])
                    if len(bucket) < MAX_SENSES:
                        bucket.append(sense)
                    # also index the underscored form (already normalized to spaces)
                    underscored = w.replace(" ", "_")
                    if underscored != w:
                        bu = idx.setdefault(underscored, [])
                        if len(bu) < MAX_SENSES:
                            bu.append(sense)
    return idx


if __name__ == "__main__":
    import sys, json
    wn_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    idx = load_wordnet(wn_dir)
    print(f"indexed words: {len(idx)}", file=sys.stderr)
    sample = list(idx.items())[:5]
    print(json.dumps(sample, ensure_ascii=False, indent=1))