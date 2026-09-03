#!/usr/bin/env python3
"""Oxford Learner's Dictionaries batch scraper for VocabularyLearning task 3.

Design notes (PRD decisions):
- Captures UK + US IPA (from phons_br / phons_n_am regions) and ALL sense-level
  examples (span.x per li.sense), in the SAME sense order as backend
  app/lookup.py so scraped senses align with existing `entries.sense_order`.
- Serial, polite: one request at a time, ~1.1s+ interval, timeout <=20s,
  at most 2 retries per word, abort after 10 consecutive failures.
- Resumable: every finished word (ok or failed) is appended to
  oxford_progress.jsonl; reruns skip words already present.
- Time budget: exits cleanly after --max-seconds (default 480) so the caller
  can rerun to continue from where it stopped.
"""
from __future__ import annotations

import argparse
import http.client
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
PROGRESS_FILE = BASE / "oxford_progress.jsonl"
WORDLIST_FILE = BASE / "wordlist.json"
MAX_LOOKUP_SENSES = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


# --------------------------------------------------------------------------- #
# word candidates + URL logic: copied verbatim from backend/app/lookup.py so
# the pages we fetch are exactly the ones that produced existing DB entries.
# --------------------------------------------------------------------------- #
def lookup_word_candidates(word: str) -> list[str]:
    candidates = [word]
    candidate_set = {word}

    def add(candidate: str) -> None:
        if len(candidate) >= 2 and candidate not in candidate_set:
            candidates.append(candidate)
            candidate_set.add(candidate)

    if word.endswith("ies") and len(word) > 4:
        add(f"{word[:-3]}y")

    if word.endswith("isation") and len(word) > 8:
        add(f"{word[:-7]}ization")

    if word.endswith("ise") and len(word) > 4:
        add(f"{word[:-3]}ize")

    if word.endswith("es") and len(word) > 3:
        add(word[:-2])

    if word.endswith("s") and len(word) > 3:
        add(word[:-1])

    if word.endswith("ied") and len(word) > 4:
        add(f"{word[:-3]}y")

    if word.endswith("ed") and len(word) > 3:
        add(word[:-2])
        if len(word) > 4 and word[-3] == word[-4]:
            add(word[:-3])
        add(f"{word[:-1]}")

    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        add(base)
        if len(base) > 2 and base[-1] == base[-2]:
            add(base[:-1])
        add(f"{base}e")

    return candidates


def normalize_lookup_word(word: str) -> str:
    normalized = word.strip().lower()
    if not normalized:
        raise ValueError("word is required")

    allowed_chars = set("abcdefghijklmnopqrstuvwxyz-' ")
    if any(character not in allowed_chars for character in normalized):
        raise ValueError(
            "word must contain only English letters, spaces, apostrophes, or hyphens"
        )

    return " ".join(normalized.split())


def oxford_definition_url(word: str) -> str:
    path_word = quote(word.replace(" ", "-"))
    query_word = quote(word)
    return (
        "https://www.oxfordlearnersdictionaries.com/definition/english/"
        f"{path_word}?q={query_word}"
    )


# --------------------------------------------------------------------------- #
# page parser: same structure as backend lookup.py OxfordLookupHtmlParser,
# extended to collect (a) every example per sense and (b) UK/US IPA + audio.
# --------------------------------------------------------------------------- #
@dataclass
class ParsedSense:
    part_of_speech: str
    definition: str
    examples: list[str] = field(default_factory=list)


@dataclass
class DraftSense:
    part_of_speech: str
    definition: str = ""
    examples: list[str] = field(default_factory=list)


# Void elements never produce an endtag, so they must not participate in
# depth tracking (an <img> inside the idioms region used to leak +1 depth
# and made the parser swallow every later <li class="sense"> — depend,
# these, avail... lost their senses this way).
VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input",
     "link", "meta", "source", "track", "wbr"}
)


class OxfordPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.part_of_speech = ""
        self.senses: list[ParsedSense] = []
        self.ipa_uk: str | None = None
        self.ipa_us: str | None = None
        self.audio_uk: str | None = None
        self.audio_us: str | None = None
        self._current_sense: DraftSense | None = None
        self._is_in_idioms = False
        self._idioms_depth = 0
        self._active_field: str | None = None
        self._active_depth = 0
        self._field_text: list[str] = []
        # phonetics region tracking
        self._phon_region: str | None = None  # 'uk' | 'us'
        self._phon_depth = 0
        # <li> nesting depth: example sentences live in ul.examples > li
        # INSIDE li.sense, so the sense must only close on its own </li>.
        self._li_depth = 0
        self._sense_li_depth: int | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._li_depth += 1

        classes = self._classes(attrs)

        if self._is_in_idioms:
            if tag not in VOID_TAGS:
                self._idioms_depth += 1
            return

        if tag == "div" and "idioms" in classes:
            self._is_in_idioms = True
            self._idioms_depth = 1
            return

        if self._active_field:
            if tag not in VOID_TAGS:
                self._active_depth += 1

        # ---- phonetics regions (outside sense li) ----
        if tag == "div" and self._phon_region is not None and (
            "sound" in classes or "audio_play_button" in classes
        ):
            self._remember_audio(classes, attrs)
        if tag == "div" and self._phon_region is None:
            if "phons_br" in classes:
                self._phon_region = "uk"
                self._phon_depth = 1
                return
            if "phons_n_am" in classes:
                self._phon_region = "us"
                self._phon_depth = 1
                return
        elif self._phon_region is not None and tag == "div":
            self._phon_depth += 1

        if tag == "li" and "sense" in classes:
            self._current_sense = DraftSense(part_of_speech=self.part_of_speech)
            self._sense_li_depth = self._li_depth
            return

        if tag == "span" and "pos" in classes and not self.part_of_speech:
            self._start_field("pos")
            return

        # IPA inside the current phonetics region
        if (
            tag == "span"
            and "phon" in classes
            and self._phon_region is not None
            and self._active_field is None
        ):
            self._start_field(f"phon_{self._phon_region}")
            return

        if self._current_sense is None:
            return

        if tag == "span" and "def" in classes and not self._current_sense.definition:
            self._start_field("definition")
            return

        if tag == "span" and "x" in classes:
            self._start_field("example")

    def handle_endtag(self, tag):
        if tag == "li":
            is_outer_sense_li = (
                self._current_sense is not None
                and self._sense_li_depth == self._li_depth
            )
            self._li_depth -= 1
            if is_outer_sense_li:
                if self._current_sense.definition:
                    self.senses.append(
                        ParsedSense(
                            part_of_speech=self._current_sense.part_of_speech,
                            definition=self._current_sense.definition,
                            examples=self._current_sense.examples,
                        )
                    )
                self._current_sense = None
                self._sense_li_depth = None
                return

        if self._is_in_idioms:
            self._idioms_depth -= 1
            if self._idioms_depth <= 0:
                self._is_in_idioms = False
            return

        if self._phon_region is not None and tag == "div":
            self._phon_depth -= 1
            if self._phon_depth <= 0:
                self._phon_region = None

        if not self._active_field:
            return

        self._active_depth -= 1
        if self._active_depth > 0:
            return

        text = self._clean_text("".join(self._field_text))
        active = self._active_field
        self._active_field = None
        self._field_text = []

        if active == "pos" and text:
            self.part_of_speech = text
        elif active.startswith("phon_"):
            region = active.split("_", 1)[1]
            if text:
                if region == "uk" and self.ipa_uk is None:
                    self.ipa_uk = text
                elif region == "us" and self.ipa_us is None:
                    self.ipa_us = text
        elif active == "definition" and self._current_sense is not None:
            self._current_sense.definition = text
        elif active == "example" and self._current_sense is not None:
            if text:
                self._current_sense.examples.append(text)

    def handle_data(self, data):
        if self._active_field:
            self._field_text.append(data)

    def _remember_audio(self, classes, attrs):
        # the audio_play_button div inside the phonetics region carries mp3
        if "sound" in classes or "audio_play_button" in classes:
            for name, value in attrs:
                if name == "data-src-mp3" and value:
                    if self._phon_region == "uk" and self.audio_uk is None:
                        self.audio_uk = value
                    elif self._phon_region == "us" and self.audio_us is None:
                        self.audio_us = value

    def _start_field(self, field_name: str) -> None:
        self._active_field = field_name
        self._active_depth = 1
        self._field_text = []

    @staticmethod
    def _classes(attrs) -> set[str]:
        for name, value in attrs:
            if name == "class" and value:
                return set(value.split())
        return set()

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(text.split())


def parse_oxford_page(word: str, html: str) -> dict:
    parser = OxfordPageParser()
    parser.feed(html)
    senses = [
        {
            "partOfSpeech": sense.part_of_speech or parser.part_of_speech or "word",
            "definition": sense.definition,
            "examples": sense.examples[:2],
        }
        for sense in parser.senses
        if sense.definition
    ][:MAX_LOOKUP_SENSES]
    return {
        "word": word,
        "sourceUrl": oxford_definition_url(word),
        "ipaUk": parser.ipa_uk,
        "ipaUs": parser.ipa_us,
        "audioUk": parser.audio_uk,
        "audioUs": parser.audio_us,
        "senses": senses,
    }


def fetch_oxford_html(word: str) -> str:
    request = Request(oxford_definition_url(word), headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def lookup_word(word: str) -> dict:
    """Try the word and its derived candidates; return first result with senses."""
    normalized = normalize_lookup_word(word)
    candidates = lookup_word_candidates(normalized)
    # Recovery fallbacks, tried only after all primary candidates fail:
    # - "<cand>_1": multi-entry homographs (contrary, close, lower, sow...) now
    #   return a "Did you spell it correctly?" 404 page on the bare ?q= URL,
    #   but <word>_1 redirects to the first entry page (e.g. contrary1_1).
    # - British double-l demotion: fulfill -> fulfil.
    fallbacks = [f"{candidate}_1" for candidate in candidates]
    if normalized.endswith("ll") and len(normalized) > 4:
        british = normalized[:-1]
        if british not in candidates:
            fallbacks.extend([british, f"{british}_1"])
    last_result: dict | None = None
    last_error: Exception | None = None

    for candidate in [*candidates, *fallbacks]:
        try:
            html = fetch_oxford_html(candidate)
        except (HTTPError, URLError, OSError, TimeoutError, http.client.HTTPException) as error:
            last_error = error
            continue

        result = parse_oxford_page(candidate, html)
        result["word"] = normalized
        if result["senses"]:
            return result
        last_result = result

    if last_result is not None:
        return last_result
    if last_error is not None:
        raise last_error
    return {
        "word": normalized,
        "sourceUrl": oxford_definition_url(normalized),
        "ipaUk": None,
        "ipaUs": None,
        "audioUk": None,
        "audioUs": None,
        "senses": [],
    }


def load_done_words() -> set[str]:
    done: set[str] = set()
    if PROGRESS_FILE.exists():
        with PROGRESS_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("status") in ("ok", "failed"):
                    done.add(record["word"])
    return done


def append_progress(record: dict) -> None:
    record["fetchedAt"] = datetime.now(timezone.utc).isoformat()
    with PROGRESS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=float, default=480.0)
    parser.add_argument("--interval", type=float, default=1.1)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N new words this run (trial runs).")
    parser.add_argument("--retry-wait", type=float, default=5.0)
    args = parser.parse_args()

    words = json.loads(WORDLIST_FILE.read_text(encoding="utf-8"))
    done = load_done_words()
    pending = [w for w in words if w not in done]
    if args.limit is not None:
        pending = pending[: args.limit]

    print(f"total={len(words)} done={len(done)} pending_total={len(words) - len(done)} "
          f"this_run={len(pending)}")

    started = time.monotonic()
    ok_count = 0
    failed_count = 0
    consecutive_failures = 0
    next_word = None

    for index, word in enumerate(pending):
        if time.monotonic() - started > args.max_seconds:
            next_word = word
            print(f"TIME_BUDGET_REACHED next_word={word}")
            break

        record = None
        for attempt in range(3):  # initial + 2 retries
            try:
                result = lookup_word(word)
                if result["senses"]:
                    record = {"word": word, "status": "ok", **result}
                else:
                    # page fetched but no senses parsed: record honestly as
                    # no-data (word not found / empty parse), not as error
                    record = {
                        "word": word,
                        "status": "ok",
                        "noData": True,
                        "sourceUrl": result["sourceUrl"],
                        "ipaUk": result.get("ipaUk"),
                        "ipaUs": result.get("ipaUs"),
                        "audioUk": result.get("audioUk"),
                        "audioUs": result.get("audioUs"),
                        "senses": [],
                    }
                break
            except (HTTPError, URLError, OSError, TimeoutError, ValueError, http.client.HTTPException) as error:
                if attempt < 2:
                    time.sleep(args.retry_wait * (attempt + 1))
                    continue
                record = {
                    "word": word,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }

        if record is None:  # defensive; should not happen
            record = {"word": word, "status": "failed", "error": "unknown"}

        append_progress(record)
        if record["status"] == "ok":
            ok_count += 1
            consecutive_failures = 0
        else:
            failed_count += 1
            consecutive_failures += 1
            print(f"FAILED {word}: {record.get('error')}")
            if consecutive_failures >= 10:
                print("ABORT: 10 consecutive failures — network or blockage issue.")
                break

        if index + 1 < len(pending):
            time.sleep(args.interval + random.uniform(0.0, 0.4))

    remaining = len(pending) - ok_count - failed_count
    print(
        f"SUMMARY this_run_ok={ok_count} this_run_failed={failed_count} "
        f"skipped_remaining={remaining} "
        f"done_total={len(load_done_words())}/{len(words)}"
    )
    if next_word is not None:
        print(f"RESUME_FROM={next_word}")


if __name__ == "__main__":
    main()
