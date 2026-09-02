from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.models import OxfordLookupResponse, OxfordLookupSenseResponse

MAX_LOOKUP_SENSES = 5


def lookup_oxford_word(word: str) -> OxfordLookupResponse:
    normalized_word = normalize_lookup_word(word)
    last_result: OxfordLookupResponse | None = None
    last_error: OSError | None = None

    for candidate in lookup_word_candidates(normalized_word):
        try:
            result = fetch_oxford_word(candidate)
        except OSError as error:
            last_error = error
            continue

        if result.senses:
            return result
        last_result = result

    if last_result is not None:
        return last_result

    if last_error is not None:
        raise last_error

    return OxfordLookupResponse(
        word=normalized_word,
        sourceUrl=oxford_definition_url(normalized_word),
        senses=[],
    )


def fetch_oxford_word(word: str) -> OxfordLookupResponse:
    source_url = oxford_definition_url(word)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            )
        },
    )

    with urlopen(request, timeout=12) as response:
        html = response.read().decode("utf-8", errors="replace")

    return parse_oxford_lookup_html(word, html)


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
        raise ValueError("word must contain only English letters, spaces, apostrophes, or hyphens")

    return " ".join(normalized.split())


def oxford_definition_url(word: str) -> str:
    path_word = quote(word.replace(" ", "-"))
    query_word = quote(word)
    return f"https://www.oxfordlearnersdictionaries.com/definition/english/{path_word}?q={query_word}"


def parse_oxford_lookup_html(word: str, html: str) -> OxfordLookupResponse:
    parser = OxfordLookupHtmlParser()
    parser.feed(html)
    senses = [
        OxfordLookupSenseResponse(
            partOfSpeech=sense.part_of_speech or parser.part_of_speech or "word",
            definition=sense.definition,
            example=sense.example,
        )
        for sense in parser.senses
        if sense.definition
    ][:MAX_LOOKUP_SENSES]

    return OxfordLookupResponse(
        word=word,
        sourceUrl=oxford_definition_url(word),
        senses=senses,
    )


@dataclass
class ParsedSense:
    part_of_speech: str
    definition: str
    example: str | None


@dataclass
class DraftSense:
    part_of_speech: str = ""
    definition: str = ""
    example: str | None = None


class OxfordLookupHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.part_of_speech = ""
        self.senses: list[ParsedSense] = []
        self._current_sense: DraftSense | None = None
        self._is_in_idioms = False
        self._idioms_depth = 0
        self._active_field: str | None = None
        self._active_depth = 0
        self._field_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)

        if self._is_in_idioms:
            self._idioms_depth += 1
            return

        if tag == "div" and "idioms" in classes:
            self._is_in_idioms = True
            self._idioms_depth = 1
            return

        if self._active_field:
            self._active_depth += 1

        if tag == "li" and "sense" in classes:
            self._current_sense = DraftSense(part_of_speech=self.part_of_speech)
            return

        if tag == "span" and "pos" in classes and not self.part_of_speech:
            self._start_field("pos")
            return

        if self._current_sense is None:
            return

        if tag == "span" and "def" in classes and not self._current_sense.definition:
            self._start_field("definition")
            return

        if tag == "span" and "x" in classes and self._current_sense.example is None:
            self._start_field("example")

    def handle_endtag(self, tag: str) -> None:
        if self._is_in_idioms:
            self._idioms_depth -= 1
            if self._idioms_depth <= 0:
                self._is_in_idioms = False
            return

        if tag == "li" and self._current_sense is not None:
            if self._current_sense.definition:
                self.senses.append(
                    ParsedSense(
                        part_of_speech=self._current_sense.part_of_speech,
                        definition=self._current_sense.definition,
                        example=self._current_sense.example,
                    )
                )
            self._current_sense = None
            return

        if not self._active_field:
            return

        self._active_depth -= 1
        if self._active_depth > 0:
            return

        text = self._clean_text("".join(self._field_text))
        if self._active_field == "pos" and text:
            self.part_of_speech = text
        elif self._active_field == "definition" and self._current_sense is not None:
            self._current_sense.definition = text
        elif self._active_field == "example" and self._current_sense is not None:
            self._current_sense.example = text or None

        self._active_field = None
        self._field_text = []

    def handle_data(self, data: str) -> None:
        if self._active_field:
            self._field_text.append(data)

    def _start_field(self, field: str) -> None:
        self._active_field = field
        self._active_depth = 1
        self._field_text = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for name, value in attrs:
            if name == "class" and value:
                return set(value.split())
        return set()

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(text.split())
