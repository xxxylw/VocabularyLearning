from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.db import connect


@dataclass(frozen=True)
class ParsedPronunciation:
    ipa: str | None
    audioFileName: str | None
    sourceUrl: str


def parse_us_pronunciation_wikitext(word: str, wikitext: str) -> ParsedPronunciation:
    pronunciation_section = _english_pronunciation_section(wikitext)
    us_lines = [line for line in pronunciation_section.splitlines() if _is_us_line(line)]

    ipa = _first_template_value(us_lines, "IPA")
    audio_file_name = _first_template_value(us_lines, "audio")

    return ParsedPronunciation(
        ipa=ipa,
        audioFileName=audio_file_name,
        sourceUrl=f"https://en.wiktionary.org/wiki/{quote(word.replace(' ', '_'))}#English",
    )


def _english_pronunciation_section(wikitext: str) -> str:
    english_match = re.search(r"^==English==\s*$", wikitext, re.MULTILINE)
    if not english_match:
        return ""

    english_text = wikitext[english_match.end():]
    next_language = re.search(r"^==[^=].*?==\s*$", english_text, re.MULTILINE)
    if next_language:
        english_text = english_text[:next_language.start()]

    pronunciation_match = re.search(r"^===Pronunciation===\s*$", english_text, re.MULTILINE)
    if not pronunciation_match:
        return ""

    section = english_text[pronunciation_match.end():]
    next_section = re.search(r"^===[^=].*?===\s*$", section, re.MULTILINE)
    return section[:next_section.start()] if next_section else section


def _is_us_line(line: str) -> bool:
    lowered = line.lower()
    return "{{a|us" in lowered or "|a=us" in lowered or "(us)" in lowered or "us pronunciation" in lowered


def _first_template_value(lines: list[str], template: str) -> str | None:
    pattern = re.compile(r"{{" + re.escape(template) + r"\|en\|([^|}]+)", re.IGNORECASE)
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(1).strip()
    return None


def lookup_wiktionary_pronunciation(word: str) -> dict[str, object]:
    normalized_word = _normalize_word(word)
    cached = _get_cached_pronunciation(normalized_word)
    if cached is not None:
        return cached

    parsed = parse_us_pronunciation_wikitext(normalized_word, _fetch_wikitext(normalized_word))
    result: dict[str, object] = {
        "word": normalized_word,
        "ipa": parsed.ipa,
        "audioUrl": None,
        "sourceUrl": parsed.sourceUrl,
        "audioSourceUrl": None,
        "attribution": None,
        "license": None,
        "licenseUrl": None,
        "status": "ready" if parsed.ipa or parsed.audioFileName else "unavailable",
    }

    if parsed.audioFileName:
        result.update(_fetch_audio_metadata(parsed.audioFileName))

    _save_cached_pronunciation(normalized_word, result)
    return result


def _normalize_word(word: str) -> str:
    normalized = " ".join(word.strip().lower().split())
    if not normalized or any(character not in "abcdefghijklmnopqrstuvwxyz-' " for character in normalized):
        raise ValueError("word must contain only English letters, spaces, apostrophes, or hyphens")
    return normalized


def _fetch_wikitext(word: str) -> str:
    data = _fetch_json(
        "https://en.wiktionary.org/w/api.php?"
        + urlencode({"action": "parse", "page": word, "prop": "wikitext", "format": "json", "formatversion": "2"})
    )
    return str(data.get("parse", {}).get("wikitext", ""))


def _fetch_audio_metadata(file_name: str) -> dict[str, object]:
    data = _fetch_json(
        "https://commons.wikimedia.org/w/api.php?"
        + urlencode({"action": "query", "titles": f"File:{file_name}", "prop": "imageinfo", "iiprop": "url|extmetadata", "format": "json", "formatversion": "2"})
    )
    pages = data.get("query", {}).get("pages", [])
    image_info = pages[0].get("imageinfo", [{}])[0] if pages else {}
    metadata = image_info.get("extmetadata", {})
    return {
        "audioUrl": image_info.get("url"),
        "audioSourceUrl": f"https://commons.wikimedia.org/wiki/File:{quote(file_name.replace(' ', '_'))}",
        "attribution": _metadata_value(metadata, "Artist"),
        "license": _metadata_value(metadata, "LicenseShortName"),
        "licenseUrl": _metadata_value(metadata, "LicenseUrl"),
    }


def _metadata_value(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key, {})
    if not isinstance(value, dict):
        return None
    raw_value = value.get("value")
    if not isinstance(raw_value, str):
        return None
    return re.sub(r"<[^>]+>", "", raw_value).strip() or None


def _fetch_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "VocabularyLearning/0.1 (educational pronunciation lookup)"})
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_cached_pronunciation(word: str) -> dict[str, object] | None:
    now = datetime.now(timezone.utc)
    with connect() as connection:
        cached = connection.execute(
            "select response_json, retry_after from pronunciation_cache where normalized_word = ?", (word,)
        ).fetchone()
    if cached is None:
        return None
    retry_after = cached["retry_after"]
    if retry_after and datetime.fromisoformat(retry_after) <= now:
        return None
    return json.loads(cached["response_json"])


def _save_cached_pronunciation(word: str, result: dict[str, object]) -> None:
    now = datetime.now(timezone.utc)
    retry_after = None
    if result["status"] == "unavailable":
        retry_after = (now + timedelta(hours=24)).isoformat()
    with connect() as connection:
        connection.execute(
            """
            insert into pronunciation_cache (normalized_word, response_json, status, retry_after, cached_at)
            values (?, ?, ?, ?, ?)
            on conflict(normalized_word) do update set
                response_json = excluded.response_json,
                status = excluded.status,
                retry_after = excluded.retry_after,
                cached_at = excluded.cached_at
            """,
            (word, json.dumps(result), result["status"], retry_after, now.isoformat()),
        )
