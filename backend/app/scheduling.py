from datetime import date, timedelta


INTERVALS = [0, 1, 2, 4, 7, 15, 30]


def transition(stage: int, rating: str, reviewed_on: date) -> tuple[int, date, str]:
    if rating == "known":
        next_stage = min(stage + 1, len(INTERVALS) - 1)
        status = "mastered" if next_stage == len(INTERVALS) - 1 else "learning"
        return (
            next_stage,
            reviewed_on + timedelta(days=INTERVALS[next_stage]),
            status,
        )
    if rating == "uncertain":
        return stage, reviewed_on + timedelta(days=1), "learning"
    if rating == "unknown":
        return 0, reviewed_on + timedelta(days=1), "learning"
    raise ValueError(f"Unsupported review rating: {rating}")
