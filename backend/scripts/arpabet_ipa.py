"""ARPAbet → IPA conversion (per CMU Pronouncing Dictionary; CC BY-SA / BSD-licensed in spirit).

Handles stress markers 0/1/2 by stripping them.  Context-free rules;
some phones (AH, ER) ideally require context but we use single tokens.
"""
from __future__ import annotations

ARPA_TO_IPA: dict[str, str] = {
    "AA": "ɑ", "AE": "æ", "AH": "ə", "AO": "ɔ", "AW": "aʊ",
    "AY": "aɪ", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð",
    "EH": "ɛ", "ER": "ɚ", "EY": "eɪ", "F": "f", "G": "ɡ",
    "HH": "h", "IH": "ɪ", "IY": "i", "JH": "dʒ", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "ŋ", "OW": "oʊ",
    "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "UH": "ʊ", "UW": "u", "V": "v",
    "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}

_PRIMARY_STRESS = "ˈ"
_SECONDARY_STRESS = "ˌ"


def arpabet_to_ipa(phones: str) -> str:
    """phones: ARPAbet phoneme string from cmudict (digits optional).

    CMUDICT convention: the stress digit (1=primary, 2=secondary, 0=none)
    is attached to the *vowel* it modifies. In IPA, the stress mark must
    precede the stressed vowel. We emit the marker before that vowel's IPA.
    """
    out: list[str] = []
    pending_stress: str | None = None
    for tok in phones.split():
        digits = "".join(c for c in tok if c in "012")
        base = "".join(c for c in tok if c.isalpha())
        ipa = ARPA_TO_IPA.get(base)
        if ipa is None:
            continue
        if digits == "1":
            pending_stress = _PRIMARY_STRESS
        elif digits == "2":
            pending_stress = _SECONDARY_STRESS
        # digits "0" or absent → unstressed, do not consume pending marker
        if pending_stress:
            out.append(pending_stress)
            pending_stress = None
        out.append(ipa)
    return "".join(out)


def load_cmudict(path: str) -> dict[str, str]:
    """returns {word_lower: arpabet_string (digits kept)}"""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith(";;;"):
                continue
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            word = parts[0]
            base = word.split("(")[0].lower()
            out.setdefault(base, " ".join(parts[1:]))
    return out


if __name__ == "__main__":
    print(arpabet_to_ipa("AH0 B AE1 K AH0 S"))  # → "əˈbækəs"