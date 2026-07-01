from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedSense:
    part_of_speech: str
    sense_label: str
    definition: str
    example: str
    chinese_note: str | None = None


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
            )
        ]
        return senses[:max_senses]
