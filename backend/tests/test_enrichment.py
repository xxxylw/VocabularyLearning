from app.enrichment import OxfordEnrichmentProvider
from app.models import OxfordLookupResponse, OxfordLookupSenseResponse


def test_oxford_enrichment_provider_uses_oxford_examples(monkeypatch):
    def fake_lookup(word: str) -> OxfordLookupResponse:
        return OxfordLookupResponse(
            word=word,
            sourceUrl=f"https://example.test/{word}",
            senses=[
                OxfordLookupSenseResponse(
                    partOfSpeech="noun",
                    definition="the mixture of gases that surrounds the earth",
                    example="Wind power doesn't release carbon dioxide into the atmosphere.",
                )
            ],
        )

    monkeypatch.setattr("app.enrichment.lookup_oxford_word", fake_lookup)

    senses = OxfordEnrichmentProvider().prepare("atmosphere", 5)

    assert senses[0].definition == "the mixture of gases that surrounds the earth"
    assert senses[0].example == "Wind power doesn't release carbon dioxide into the atmosphere."


def test_oxford_enrichment_provider_writes_ielts_style_fallback_examples(monkeypatch):
    def fake_lookup(word: str) -> OxfordLookupResponse:
        return OxfordLookupResponse(
            word=word,
            sourceUrl=f"https://example.test/{word}",
            senses=[
                OxfordLookupSenseResponse(
                    partOfSpeech="noun",
                    definition="all of the water on or over the earth's surface",
                    example=None,
                )
            ],
        )

    monkeypatch.setattr("app.enrichment.lookup_oxford_word", fake_lookup)

    senses = OxfordEnrichmentProvider().prepare("hydrosphere", 5)

    assert senses[0].example == (
        "In an IELTS essay, hydrosphere can help explain environmental "
        "change, resource use, or long-term planning."
    )
    assert senses[0].example_source == "fallback"
