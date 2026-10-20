"""SM-2 simplified scheduling algorithm tests (P0-4).

Spec: PRD chapter 5 (2026-09-03). The card carries two new states:
ease factor (EF, default 2.5) and current interval I (days, default 0).

Rating quality mapping: known -> q=5, uncertain -> q=3, unknown -> q=1.
EF' = EF + (0.1 - (5-q) x (0.08 + (5-q) x 0.02)), clamped to [1.3, 3.0].
Interval rules (I is the pre-review interval; interval math uses the
pre-review EF per the migration example round(15 x 2.5) = 38):
- known:     I <- min(180, max(1, round_half_up(I x EF))); due = new I.
             mastered when untruncated round_half_up(I x EF) >= 180.
- uncertain: I unchanged; due = min(I + 1, 180); EF -0.14.
- unknown:   I <- 0; due = next day; EF -0.54.
"""

from datetime import date, timedelta

import pytest

from app.scheduling import (
    DEFAULT_EF,
    EF_MAX,
    EF_MIN,
    INTERVAL_CAP_DAYS,
    INTERVALS,
    round_half_up,
    schedule_review,
)

REVIEWED_ON = date(2026, 9, 3)


class TestEfUpdate:
    def test_known_adds_point_one(self):
        outcome = schedule_review(2.5, 15, "known", REVIEWED_ON)
        assert outcome.ef == pytest.approx(2.6)

    def test_uncertain_subtracts_point_one_four(self):
        outcome = schedule_review(2.5, 15, "uncertain", REVIEWED_ON)
        assert outcome.ef == pytest.approx(2.36)

    def test_unknown_subtracts_point_five_four(self):
        outcome = schedule_review(2.5, 15, "unknown", REVIEWED_ON)
        assert outcome.ef == pytest.approx(1.96)

    def test_twenty_unknowns_clamp_ef_at_floor(self):
        ef = 2.5
        for _ in range(20):
            ef = schedule_review(ef, 5, "unknown", REVIEWED_ON).ef
        assert ef == pytest.approx(EF_MIN)
        # one more unknown keeps it at the floor
        ef = schedule_review(ef, 5, "unknown", REVIEWED_ON).ef
        assert ef == pytest.approx(EF_MIN)

    def test_twenty_knowns_clamp_ef_at_ceiling(self):
        ef = 2.5
        for _ in range(20):
            ef = schedule_review(ef, 5, "known", REVIEWED_ON).ef
        assert ef == pytest.approx(EF_MAX)
        ef = schedule_review(ef, 5, "known", REVIEWED_ON).ef
        assert ef == pytest.approx(EF_MAX)

    def test_unknown_never_clears_ef(self):
        # unknown resets the interval but not EF
        outcome = schedule_review(2.9, 30, "unknown", REVIEWED_ON)
        assert outcome.interval_days == 0
        assert outcome.ef == pytest.approx(2.36)


class TestRoundHalfUp:
    def test_half_rounds_up(self):
        assert round_half_up(2.5) == 3
        assert round_half_up(37.5) == 38
        assert round_half_up(2.4) == 2
        assert round_half_up(2.6) == 3

    def test_not_bankers_rounding(self):
        # Python round() would give 2 for 2.5 (banker's rounding)
        assert round_half_up(0.5) == 1
        assert round_half_up(1.5) == 2
        assert round_half_up(12.5) == 13


class TestKnownInterval:
    def test_new_card_known_gets_interval_one(self):
        outcome = schedule_review(2.5, 0, "known", REVIEWED_ON)
        assert outcome.interval_days == 1
        assert outcome.due_at == REVIEWED_ON + timedelta(days=1)
        assert outcome.status == "learning"

    def test_migration_example_stage_five_card(self):
        # PRD: stage 5 card (I=15, EF=2.5) next known -> round(15 x 2.5) = 38
        outcome = schedule_review(2.5, 15, "known", REVIEWED_ON)
        assert outcome.interval_days == 38
        assert outcome.due_at == REVIEWED_ON + timedelta(days=38)

    def test_interval_uses_pre_review_ef(self):
        # 15 x 2.5 = 37.5 -> 38 (not 15 x 2.6 = 39)
        outcome = schedule_review(2.5, 15, "known", REVIEWED_ON)
        assert outcome.interval_days == 38

    @pytest.mark.parametrize(
        "interval,ef,expected",
        [
            (1, 2.5, 3),  # 2.5 half-up
            (3, 2.5, 8),  # 7.5 half-up
            (1, 2.6, 3),  # 2.6
            (3, 2.7, 8),  # 8.1
            (7, 1.3, 9),  # 9.1
            (10, 1.3, 13),
            (5, 3.0, 15),
            (50, 2.5, 125),
            (71, 1.3, 92),  # 92.3
            (2, 2.4, 5),  # 4.8 -> 5
            (2, 2.2, 4),  # 4.4 -> 4
        ],
    )
    def test_known_interval_matrix(self, interval, ef, expected):
        outcome = schedule_review(ef, interval, "known", REVIEWED_ON)
        assert outcome.interval_days == expected
        assert outcome.due_at == REVIEWED_ON + timedelta(days=expected)

    def test_interval_monotonic_non_decreasing_for_consecutive_known(self):
        for ef in (1.3, 2.5, 3.0):
            interval = 1
            previous = interval
            for _ in range(30):
                outcome = schedule_review(ef, interval, "known", REVIEWED_ON)
                assert outcome.interval_days >= previous
                assert outcome.interval_days <= INTERVAL_CAP_DAYS
                previous = outcome.interval_days
                interval = outcome.interval_days

    def test_typical_sequence_at_constant_ef_2_5(self):
        # PRD illustration: 1 -> 3 -> 8 -> 20 -> 50 -> 125 -> 180 capped
        ef = 2.5
        interval = 1
        sequence = []
        for _ in range(6):
            outcome = schedule_review(ef, interval, "known", REVIEWED_ON)
            interval = outcome.interval_days
            sequence.append(interval)
        assert sequence == [3, 8, 20, 50, 125, 180]


class TestIntervalCapAndMastered:
    def test_interval_never_exceeds_cap(self):
        outcome = schedule_review(3.0, 180, "known", REVIEWED_ON)
        assert outcome.interval_days == INTERVAL_CAP_DAYS
        assert outcome.due_at == REVIEWED_ON + timedelta(days=180)

    def test_mastered_triggered_when_theoretical_interval_reaches_cap(self):
        # 50 x 2.5 = 125 < 180 -> not mastered
        outcome = schedule_review(2.5, 50, "known", REVIEWED_ON)
        assert outcome.status == "learning"
        # 125 x 2.5 = 312.5 >= 180 -> mastered, capped at 180
        outcome = schedule_review(2.5, 125, "known", REVIEWED_ON)
        assert outcome.status == "mastered"
        assert outcome.interval_days == 180

    def test_mastered_exactly_at_180(self):
        # 72 x 2.5 = 180 exactly -> mastered
        outcome = schedule_review(2.5, 72, "known", REVIEWED_ON)
        assert outcome.interval_days == 180
        assert outcome.status == "mastered"

    def test_just_below_180_not_mastered(self):
        # 71 x 2.5 = 177.5 -> 178 < 180
        outcome = schedule_review(2.5, 71, "known", REVIEWED_ON)
        assert outcome.interval_days == 178
        assert outcome.status == "learning"

    def test_mastered_card_known_stays_mastered_even_with_low_ef(self):
        # mastered card, EF dropped to 1.3, theoretical 60 x 1.3 = 78 < 180
        outcome = schedule_review(1.3, 60, "known", REVIEWED_ON, mastered=True)
        assert outcome.status == "mastered"
        assert outcome.interval_days == 78

    def test_mastered_card_unknown_unmasters(self):
        outcome = schedule_review(2.5, 60, "unknown", REVIEWED_ON, mastered=True)
        assert outcome.status == "learning"
        assert outcome.interval_days == 0
        assert outcome.due_at == REVIEWED_ON + timedelta(days=1)

    def test_mastered_card_uncertain_returns_to_learning(self):
        # matches current behavior: any non-known rating demotes mastery
        outcome = schedule_review(2.5, 60, "uncertain", REVIEWED_ON, mastered=True)
        assert outcome.status == "learning"


class TestUncertainSemantics:
    def test_interval_unchanged_due_one_day_beyond_interval(self):
        outcome = schedule_review(2.4, 15, "uncertain", REVIEWED_ON)
        assert outcome.interval_days == 15
        assert outcome.due_at == REVIEWED_ON + timedelta(days=16)
        assert outcome.status == "learning"

    def test_new_card_uncertain_due_tomorrow(self):
        outcome = schedule_review(2.5, 0, "uncertain", REVIEWED_ON)
        assert outcome.interval_days == 0
        assert outcome.due_at == REVIEWED_ON + timedelta(days=1)

    def test_due_capped_at_180_for_max_interval(self):
        outcome = schedule_review(2.5, 180, "uncertain", REVIEWED_ON)
        assert outcome.due_at == REVIEWED_ON + timedelta(days=180)

    def test_long_term_uncertain_never_mastered(self):
        ef = 2.5
        interval = 10
        for _ in range(50):
            outcome = schedule_review(ef, interval, "uncertain", REVIEWED_ON)
            assert outcome.status == "learning"
            ef = outcome.ef
            interval = outcome.interval_days


class TestUnknownSemantics:
    def test_unknown_resets_interval_and_schedules_next_day(self):
        outcome = schedule_review(2.2, 30, "unknown", REVIEWED_ON)
        assert outcome.interval_days == 0
        assert outcome.due_at == REVIEWED_ON + timedelta(days=1)
        assert outcome.status == "learning"


class TestLegacyLadder:
    def test_legacy_intervals_preserved_for_migration(self):
        assert INTERVALS == [0, 1, 2, 4, 7, 15, 30]

    def test_default_ef(self):
        assert DEFAULT_EF == 2.5
        assert EF_MIN == 1.3
        assert EF_MAX == 3.0


class TestUnsupportedRating:
    def test_unsupported_rating_raises(self):
        with pytest.raises(ValueError):
            schedule_review(2.5, 1, "easy", REVIEWED_ON)
