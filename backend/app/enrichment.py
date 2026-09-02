from dataclasses import dataclass
from typing import Literal

from app.lookup import lookup_oxford_word

EnrichmentSource = Literal["oxford_api", "fallback"]


@dataclass(frozen=True)
class PreparedSense:
    part_of_speech: str
    sense_label: str
    definition: str
    example: str
    chinese_note: str | None = None
    definition_source: EnrichmentSource = "fallback"
    example_source: EnrichmentSource = "fallback"


class FallbackEnrichmentProvider:
    def prepare(self, word: str, max_senses: int) -> list[PreparedSense]:
        senses = [
            PreparedSense(
                part_of_speech="word",
                sense_label="general IELTS use",
                definition=f"A learner-friendly IELTS study meaning for '{word}'.",
                example=(
                    f"In IELTS writing, students should use '{word}' accurately "
                    "and naturally."
                ),
                chinese_note=None,
                definition_source="fallback",
                example_source="fallback",
            )
        ]
        return senses[:max_senses]


class OxfordEnrichmentProvider:
    def __init__(self, fallback: FallbackEnrichmentProvider | None = None):
        self.fallback = fallback or FallbackEnrichmentProvider()

    def prepare(self, word: str, max_senses: int) -> list[PreparedSense]:
        try:
            lookup = lookup_oxford_word(word)
        except (OSError, ValueError):
            return self.fallback.prepare(word, max_senses)

        senses = [
            PreparedSense(
                part_of_speech=sense.partOfSpeech,
                sense_label=sense.definition,
                definition=sense.definition,
                example=sense.example or self._fallback_example(word),
                chinese_note=None,
                definition_source="oxford_api",
                example_source="oxford_api" if sense.example else "fallback",
            )
            for sense in lookup.senses[:max_senses]
        ]

        return senses or self.fallback.prepare(word, max_senses)

    @staticmethod
    def _fallback_example(word: str) -> str:
        return (
            f"In an IELTS essay, {word} can help explain environmental "
            "change, resource use, or long-term planning."
        )
