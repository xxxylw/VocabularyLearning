"""Scheduling for spaced repetition reviews.

P0-4 (PRD chapter 5, 2026-09-03): the fixed 7-step ladder
(INTERVALS = [0, 1, 2, 4, 7, 15, 30]) was replaced by a simplified
SM-2 algorithm. Each card carries two scheduling states:

- ease factor (EF): long-term difficulty, new cards start at 2.5,
  clamped to [1.3, 3.0];
- current interval I (days): interval established by the most recent
  rating, 0 for new cards.

Rating quality mapping: known -> q=5, uncertain -> q=3, unknown -> q=1.

EF update: EF' = EF + (0.1 - (5-q) x (0.08 + (5-q) x 0.02)), i.e.
known +0.10 / uncertain -0.14 / unknown -0.54, clamped to [1.3, 3.0].

Interval rules (I is the pre-review interval; interval math uses the
pre-review EF, per the PRD migration example round(15 x 2.5) = 38):

- known:     I <- min(180, max(1, round_half_up(I x EF))); next review
             after the new I days. A card becomes "mastered" when the
             untruncated theoretical interval round_half_up(I x EF)
             is >= 180; already-mastered cards stay mastered on known.
- uncertain: I unchanged; next review after min(I + 1, 180) days.
- unknown:   I <- 0; next review the next day. EF is never reset.

The legacy INTERVALS ladder is kept only so the migration can derive
each existing card's current interval from its legacy stage. The
legacy ``stage`` column no longer participates in scheduling.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import math

DEFAULT_EF = 2.5
EF_MIN = 1.3
EF_MAX = 3.0
INTERVAL_CAP_DAYS = 180

# Legacy fixed ladder (pre P0-4). Retained for the one-time migration
# that maps a card's legacy stage to its starting interval.
INTERVALS = [0, 1, 2, 4, 7, 15, 30]

RATING_QUALITY = {"known": 5, "uncertain": 3, "unknown": 1}


@dataclass(frozen=True)
class SchedulingOutcome:
    ef: float
    interval_days: int
    due_at: date
    status: str


def round_half_up(value: float) -> int:
    """Round half up (2.5 -> 3), unlike Python's banker's rounding."""
    return math.floor(value + 0.5)


def update_ef(ef: float, rating: str) -> float:
    """Return the EF after one review with the given rating."""
    try:
        quality = RATING_QUALITY[rating]
    except KeyError:
        raise ValueError(f"Unsupported review rating: {rating}") from None
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    return min(EF_MAX, max(EF_MIN, ef + delta))


def schedule_review(
    ef: float,
    interval_days: int,
    rating: str,
    reviewed_on: date,
    *,
    mastered: bool = False,
) -> SchedulingOutcome:
    """Compute the card state after one review.

    ``interval_days`` is the card's current interval before this review;
    interval math deliberately uses the pre-review EF (see module docs).
    """
    if rating not in RATING_QUALITY:
        raise ValueError(f"Unsupported review rating: {rating}")

    next_ef = update_ef(ef, rating)

    if rating == "known":
        theoretical = round_half_up(interval_days * ef)
        next_interval = min(INTERVAL_CAP_DAYS, max(1, theoretical))
        status = "mastered" if (mastered or theoretical >= INTERVAL_CAP_DAYS) else "learning"
        return SchedulingOutcome(
            ef=next_ef,
            interval_days=next_interval,
            due_at=reviewed_on + timedelta(days=next_interval),
            status=status,
        )

    if rating == "uncertain":
        due_in_days = min(interval_days + 1, INTERVAL_CAP_DAYS)
        return SchedulingOutcome(
            ef=next_ef,
            interval_days=interval_days,
            due_at=reviewed_on + timedelta(days=due_in_days),
            status="learning",
        )

    # unknown
    return SchedulingOutcome(
        ef=next_ef,
        interval_days=0,
        due_at=reviewed_on + timedelta(days=1),
        status="learning",
    )
