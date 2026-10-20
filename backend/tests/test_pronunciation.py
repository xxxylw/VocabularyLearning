from app.pronunciation import parse_us_pronunciation_wikitext
from app.main import create_app
from fastapi.testclient import TestClient


def test_parse_us_pronunciation_wikitext_extracts_us_ipa_and_audio():
    result = parse_us_pronunciation_wikitext(
        "example",
        """
==English==
===Pronunciation===
* {{a|US}} {{IPA|en|/ɪɡˈzæmpəl/}}
* {{audio|en|en-us-example.ogg|Audio (US)}}
===Noun===
""",
    )

    assert result.ipa == "/ɪɡˈzæmpəl/"
    assert result.audioFileName == "en-us-example.ogg"
    assert result.sourceUrl == "https://en.wiktionary.org/wiki/example#English"


def test_parse_us_pronunciation_wikitext_recognizes_us_template_attributes():
    result = parse_us_pronunciation_wikitext(
        "example",
        """
==English==
===Pronunciation===
* {{IPA|en|/ɪɡˈzæm.pəl/|a=US}}
* {{audio|en|en-us-example.ogg|a=US}}
===Noun===
""",
    )

    assert result.ipa == "/ɪɡˈzæm.pəl/"
    assert result.audioFileName == "en-us-example.ogg"


def test_pronunciation_route_returns_us_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.routes.lookup_wiktionary_pronunciation",
        lambda word: {
            "word": word,
            "ipa": "/ɪɡˈzæmpəl/",
            "audioUrl": "https://upload.wikimedia.org/example.ogg",
            "sourceUrl": "https://en.wiktionary.org/wiki/example#English",
            "audioSourceUrl": "https://commons.wikimedia.org/wiki/File:example.ogg",
            "attribution": "Wikimedia Commons contributor",
            "license": "CC BY-SA 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
            "status": "ready",
        },
    )

    response = TestClient(create_app()).get("/api/pronunciations/example")

    assert response.status_code == 200
    assert response.json()["ipa"] == "/ɪɡˈzæmpəl/"
    assert response.json()["audioUrl"] == "https://upload.wikimedia.org/example.ogg"

def test_pronunciation_route_returns_uk_and_us_ipa_from_oxford_cache(tmp_path, monkeypatch):
    import json

    from app.db import connect
    from app.pronunciation import lookup_wiktionary_pronunciation

    db_path = tmp_path / "vocabulary.sqlite"
    monkeypatch.setenv("VOCAB_DB_PATH", str(db_path))

    cached = {
        "word": "atmosphere",
        "pronunciationSource": "oxford",
        "ipaUk": "/ˈætməsfɪə(r)/",
        "ipaUs": "/ˈætməsfɪr/",
        "ipa": "/ˈætməsfɪr/",
        "audioUrl": None,
        "sourceUrl": (
            "https://www.oxfordlearnersdictionaries.com/definition/english/"
            "atmosphere?q=atmosphere"
        ),
        "status": "ready",
    }
    with connect() as connection:
        connection.execute(
            """
            insert into pronunciation_cache (
                normalized_word, response_json, status, retry_after, cached_at
            )
            values (?, ?, ?, ?, ?)
            """,
            ("atmosphere", json.dumps(cached), "ready", None, "2026-01-01T00:00:00+00:00"),
        )

    result = lookup_wiktionary_pronunciation("atmosphere")

    assert result["ipaUk"] == "/ˈætməsfɪə(r)/"
    assert result["ipaUs"] == "/ˈætməsfɪr/"

    response = TestClient(create_app()).get("/api/pronunciations/atmosphere")
    assert response.status_code == 200
    body = response.json()
    assert body["ipaUk"] == "/ˈætməsfɪə(r)/"
    assert body["ipaUs"] == "/ˈætməsfɪr/"
    assert body["sourceUrl"].startswith(
        "https://www.oxfordlearnersdictionaries.com/definition/english/"
    )
