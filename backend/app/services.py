from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
import json
import os
from uuid import uuid4

from app.books import get_current_book_id
from app.db import connect
from app.enrichment import FallbackEnrichmentProvider, OxfordEnrichmentProvider
from app.models import (
    BookSummaryResponse,
    DueReviewsResponse,
    PrepareJobRequest,
    PrepareJobResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    StudyCardResponse,
    StudyExampleResponse,
    StudySenseResponse,
    TodaySessionResponse,
    TodayStartRequest,
)
from app.repositories import normalize_word
from app.scheduling import DEFAULT_EF, schedule_review


class ReviewConflictError(ValueError):
    pass


def get_current_book() -> BookSummaryResponse:
    with connect() as connection:
        book_id = get_current_book_id(connection)
        book_row = connection.execute(
            "select id, title, description, source, created_at, updated_at"
            " from vocabulary_books where id = ?",
            (book_id,),
        ).fetchone()
        word_count_row = connection.execute(
            "select count(*) as total from book_words where book_id = ?",
            (book_id,),
        ).fetchone()

    return BookSummaryResponse(
        id=book_row["id"],
        title=book_row["title"],
        description=book_row["description"],
        source=book_row["source"],
        createdAt=book_row["created_at"],
        updatedAt=book_row["updated_at"],
        totalWords=word_count_row["total"],
    )


def prepare_book_words(request: PrepareJobRequest) -> PrepareJobResponse:
    if request.scope != "next":
        raise ValueError("Only scope='next' is supported")

    count = request.count if request.count is not None else 20
    max_senses = max(request.maxSensesPerWord, 1)
    now = _utc_now()
    today = date.today().isoformat()
    provider = _create_enrichment_provider()

    with connect() as connection:
        book_id = get_current_book_id(connection)
        book_words = connection.execute(
            """
            select id, word_text, normalized_text
            from book_words
            where import_status in ('pending', 'needs_review')
              and book_id = ?
            order by sequence_index
            limit ?
            """,
            (book_id, count),
        ).fetchall()

        job_id = str(uuid4())
        ready_cards = 0
        processed_words = 0

        for book_word in book_words:
            word_text = book_word["word_text"]
            normalized_text = book_word["normalized_text"] or normalize_word(word_text)
            word_id = _upsert_word(
                connection=connection,
                word_text=word_text,
                normalized_text=normalized_text,
                now=now,
            )

            if request.overwriteExisting:
                _delete_word_study_material(connection, word_id)

            if _word_card_count(connection, word_id) > 0:
                connection.execute(
                    """
                    update book_words
                    set import_status = 'ready', updated_at = ?
                    where id = ?
                    """,
                    (now, book_word["id"]),
                )
                processed_words += 1
                continue

            senses = provider.prepare(word_text, max_senses)
            for sense_order, sense in enumerate(senses, start=1):
                entry_id = str(uuid4())
                connection.execute(
                    """
                    insert into entries (
                        id,
                        word_id,
                        sense_order,
                        part_of_speech,
                        sense_label,
                        definition,
                        definition_source,
                        chinese_note,
                        created_at,
                        updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        word_id,
                        sense_order,
                        sense.part_of_speech,
                        sense.sense_label,
                        sense.definition,
                        sense.definition_source,
                        sense.chinese_note,
                        now,
                        now,
                    ),
                )
                if sense.example:
                    connection.execute(
                        """
                        insert into entry_examples (
                            id,
                            entry_id,
                            example_order,
                            sentence,
                            source,
                            is_primary,
                            created_at,
                            updated_at
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            entry_id,
                            1,
                            sense.example,
                            sense.example_source or "fallback",
                            1,
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    insert into cards (
                        id,
                        entry_id,
                        status,
                        stage,
                        due_at,
                        created_on,
                        last_reviewed_at,
                        ef,
                        interval_days
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        entry_id,
                        "learning",
                        0,
                        today,
                        today,
                        None,
                        DEFAULT_EF,
                        0,
                    ),
                )
                ready_cards += 1

            connection.execute(
                """
                update book_words
                set import_status = 'ready', updated_at = ?
                where id = ?
                """,
                (now, book_word["id"]),
            )
            processed_words += 1

        connection.execute(
            """
            insert into prepare_jobs (
                id,
                scope,
                status,
                total_words,
                processed_words,
                ready_cards,
                needs_review,
                failed_words_json,
                created_at,
                updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                request.scope,
                "completed",
                len(book_words),
                processed_words,
                ready_cards,
                0,
                json.dumps([]),
                now,
                now,
            ),
        )

    return PrepareJobResponse(
        jobId=job_id,
        status="completed",
        totalWords=len(book_words),
        processedWords=processed_words,
        readyCards=ready_cards,
        needsReview=0,
        failedWords=[],
    )


def start_today_session(request: TodayStartRequest) -> TodaySessionResponse:
    study_date = request.date or date.today()
    review_cards = _get_due_review_cards(study_date)
    new_word_target_remaining = max(
        request.dailyNewWordTarget - _count_new_words_studied_on(study_date),
        0,
    )
    new_cards = (
        _get_due_new_cards(study_date, new_word_target_remaining)
        if new_word_target_remaining > 0
        else []
    )
    if len(new_cards) < new_word_target_remaining:
        prepare_book_words(
            PrepareJobRequest(
                scope="next",
                count=new_word_target_remaining - len(new_cards),
                maxSensesPerWord=5,
                overwriteExisting=False,
            )
        )
        new_cards = _get_due_new_cards(study_date, new_word_target_remaining)

    cards = review_cards + new_cards
    return TodaySessionResponse(totalCards=len(cards), cards=cards)


def get_due_reviews(due_date: date) -> DueReviewsResponse:
    cards = _get_due_study_cards(due_date, None)
    return DueReviewsResponse(date=due_date, total=len(cards), cards=cards)


def review_card(card_id: str, request: ReviewCardRequest) -> ReviewCardResponse:
    reviewed_on = request.reviewedDate or request.reviewedAt.date()
    reviewed_at = request.reviewedAt.isoformat()

    with connect() as connection:
        card = connection.execute(
            "select id, stage, status, due_at, ef, interval_days"
            " from cards where id = ?",
            (card_id,),
        ).fetchone()
        if card is None:
            raise LookupError("Card not found")
        if date.fromisoformat(card["due_at"]) > reviewed_on:
            raise ReviewConflictError("Card is not due on the reviewed date")
        if _review_exists_on_date(connection, card_id, reviewed_on):
            raise ReviewConflictError("Card was already reviewed on this date")

        # SM-2 (P0-4): scheduling is driven by ef + interval_days. The
        # legacy stage is frozen at its migrated value (rollback anchor)
        # and is recorded unchanged on every new review row.
        previous_stage = card["stage"]
        outcome = schedule_review(
            card["ef"],
            card["interval_days"],
            request.rating,
            reviewed_on,
            mastered=card["status"] == "mastered",
        )
        next_stage = previous_stage

        connection.execute(
            """
            insert into reviews (
                id,
                card_id,
                rating,
                reviewed_at,
                previous_stage,
                next_stage,
                next_due_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                card_id,
                request.rating,
                reviewed_at,
                previous_stage,
                next_stage,
                outcome.due_at.isoformat(),
            ),
        )
        connection.execute(
            """
            update cards
            set status = ?,
                stage = ?,
                due_at = ?,
                last_reviewed_at = ?,
                ef = ?,
                interval_days = ?
            where id = ?
            """,
            (
                outcome.status,
                next_stage,
                outcome.due_at.isoformat(),
                reviewed_at,
                outcome.ef,
                outcome.interval_days,
                card_id,
            ),
        )

    return ReviewCardResponse(
        cardId=card_id,
        rating=request.rating,
        previousStage=previous_stage,
        nextStage=next_stage,
        nextDueAt=outcome.due_at,
        status=outcome.status,
    )


def _upsert_word(
    connection,
    word_text: str,
    normalized_text: str,
    now: str,
) -> str:
    existing = connection.execute(
        "select id from words where normalized_text = ?",
        (normalized_text,),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    word_id = str(uuid4())
    connection.execute(
        """
        insert into words (id, text, normalized_text, created_at, updated_at)
        values (?, ?, ?, ?, ?)
        """,
        (word_id, word_text, normalized_text, now, now),
    )
    return word_id


def _word_card_count(connection, word_id: str) -> int:
    row = connection.execute(
        """
        select count(*) as total
        from entries
        join cards on cards.entry_id = entries.id
        where entries.word_id = ?
        """,
        (word_id,),
    ).fetchone()
    return row["total"]


def _delete_word_study_material(connection, word_id: str) -> None:
    entry_rows = connection.execute(
        "select id from entries where word_id = ?",
        (word_id,),
    ).fetchall()
    if not entry_rows:
        return

    entry_ids = [row["id"] for row in entry_rows]
    placeholders = ", ".join("?" for _ in entry_ids)
    card_rows = connection.execute(
        f"select id from cards where entry_id in ({placeholders})",
        tuple(entry_ids),
    ).fetchall()
    card_ids = [row["id"] for row in card_rows]

    if card_ids:
        card_placeholders = ", ".join("?" for _ in card_ids)
        connection.execute(
            f"delete from reviews where card_id in ({card_placeholders})",
            tuple(card_ids),
        )
        connection.execute(
            f"delete from cards where id in ({card_placeholders})",
            tuple(card_ids),
        )

    connection.execute(
        f"delete from entry_examples where entry_id in ({placeholders})",
        tuple(entry_ids),
    )
    connection.execute(
        f"delete from entries where id in ({placeholders})",
        tuple(entry_ids),
    )


def _get_due_study_cards(
    due_date: date,
    limit: int | None,
) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="1 = 1",
        limit=limit,
    )


def _get_due_review_cards(due_date: date) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="cards.last_reviewed_at is not null",
        limit=None,
    )


def _get_due_new_cards(
    due_date: date,
    limit: int,
) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="cards.last_reviewed_at is null",
        limit=limit,
    )


def _get_due_study_cards_by_queue(
    due_date: date,
    queue_condition: str,
    limit: int | None,
) -> list[StudyCardResponse]:
    with connect() as connection:
        book_id = get_current_book_id(connection)
        card_rows = connection.execute(
            f"""
            select
                cards.id as card_id,
                cards.status,
                cards.stage,
                cards.due_at,
                cards.last_reviewed_at,
                words.text as word,
                words.normalized_text,
                entries.part_of_speech,
                entries.sense_label,
                entries.definition,
                entries.definition_source,
                entries.chinese_note,
                (
                    select min(book_words.sequence_index)
                    from book_words
                    where book_words.normalized_text = words.normalized_text
                      and book_words.book_id = ?
                ) as book_sequence_index
            from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where cards.due_at <= ?
              and cards.status in ('new', 'learning', 'mastered')
              and {queue_condition}
            order by
                case when book_sequence_index is null then 1 else 0 end,
                book_sequence_index,
                entries.sense_order,
                cards.due_at,
                cards.created_on,
                words.text
            """,
            (book_id, due_date.isoformat()),
        ).fetchall()

        if not card_rows:
            return []

        cards = _study_cards_from_rows(connection, card_rows)
        return cards if limit is None else cards[:limit]


def _count_new_words_studied_on(study_date: date) -> int:
    with connect() as connection:
        row = connection.execute(
            """
            select count(*) as total
            from (
                select words.normalized_text
                from reviews
                join cards on cards.id = reviews.card_id
                join entries on entries.id = cards.entry_id
                join words on words.id = entries.word_id
                where substr(reviews.reviewed_at, 1, 10) = ?
                  and not exists (
                    select 1
                    from reviews previous_reviews
                    join cards previous_cards on previous_cards.id = previous_reviews.card_id
                    join entries previous_entries on previous_entries.id = previous_cards.entry_id
                    where previous_entries.word_id = entries.word_id
                      and substr(previous_reviews.reviewed_at, 1, 10) < ?
                  )
                group by words.normalized_text
            )
            """,
            (study_date.isoformat(), study_date.isoformat()),
        ).fetchone()

    return row["total"]


def _study_cards_from_rows(connection, card_rows) -> list[StudyCardResponse]:
    due_card_ids_by_word: dict[str, list[str]] = {}
    queue_type_by_word: dict[str, Literal["new", "review"]] = {}
    for row in card_rows:
        normalized_text = row["normalized_text"]
        due_card_ids_by_word.setdefault(normalized_text, []).append(row["card_id"])
        queue_type_by_word.setdefault(
            normalized_text,
            "new" if row["last_reviewed_at"] is None else "review",
        )

    normalized_words = list(due_card_ids_by_word)
    normalized_placeholders = ", ".join("?" for _ in normalized_words)
    book_id = get_current_book_id(connection)
    all_sense_rows = connection.execute(
        f"""
        select
            cards.id as card_id,
            cards.status,
            cards.stage,
            cards.due_at,
            cards.last_reviewed_at,
            words.text as word,
            words.normalized_text,
            entries.part_of_speech,
            entries.sense_label,
            entries.definition,
            entries.definition_source,
            entries.chinese_note,
            (
                select min(book_words.sequence_index)
                from book_words
                where book_words.normalized_text = words.normalized_text
                  and book_words.book_id = ?
            ) as book_sequence_index
        from cards
        join entries on entries.id = cards.entry_id
        join words on words.id = entries.word_id
        where words.normalized_text in ({normalized_placeholders})
          and cards.status in ('new', 'learning', 'mastered')
        order by
            case when book_sequence_index is null then 1 else 0 end,
            book_sequence_index,
            entries.sense_order,
            cards.due_at,
            cards.created_on,
            words.text
        """,
        (book_id, *normalized_words),
    ).fetchall()

    card_ids = [row["card_id"] for row in all_sense_rows]
    placeholders = ", ".join("?" for _ in card_ids)
    example_rows = connection.execute(
        f"""
        select
            cards.id as card_id,
            entry_examples.id as example_id,
            entry_examples.sentence,
            entry_examples.is_primary
        from cards
        join entry_examples on entry_examples.entry_id = cards.entry_id
        where cards.id in ({placeholders})
        order by entry_examples.example_order
        """,
        tuple(card_ids),
    ).fetchall()

    examples_by_card: dict[str, list[StudyExampleResponse]] = {
        card_id: [] for card_id in card_ids
    }
    for row in example_rows:
        examples_by_card[row["card_id"]].append(
            StudyExampleResponse(
                exampleId=row["example_id"],
                sentence=row["sentence"],
                isPrimary=bool(row["is_primary"]),
            )
        )

    grouped_rows: dict[str, list] = {}
    for row in all_sense_rows:
        grouped_rows.setdefault(row["normalized_text"], []).append(row)

    study_cards: list[StudyCardResponse] = []
    for rows in grouped_rows.values():
        first = rows[0]
        senses = [
            StudySenseResponse(
                cardId=row["card_id"],
                partOfSpeech=row["part_of_speech"],
                senseLabel=row["sense_label"],
                definition=row["definition"],
                definitionSource=row["definition_source"],
                examples=examples_by_card[row["card_id"]],
                chineseNote=row["chinese_note"],
            )
            for row in rows
        ]
        # A card is "degraded" when any of its senses came from the fallback
        # enrichment provider. Frontend uses this to swap fake text for a
        # "Definition preparing" placeholder so the user is never shown
        # template content as if it were real Oxford data.
        degraded = any(
            sense.definitionSource == "fallback" for sense in senses
        )
        study_cards.append(
            StudyCardResponse(
                cardId=first["card_id"],
                cardIds=due_card_ids_by_word[first["normalized_text"]],
                word=first["word"],
                partOfSpeech=first["part_of_speech"],
                senseLabel=first["sense_label"],
                definition=first["definition"],
                definitionSource=first["definition_source"],
                examples=examples_by_card[first["card_id"]],
                chineseNote=first["chinese_note"],
                senses=senses,
                status=first["status"],
                stage=first["stage"],
                dueAt=date.fromisoformat(first["due_at"]),
                queueType=queue_type_by_word[first["normalized_text"]],
                degraded=degraded,
            )
        )

    return study_cards


def _review_exists_on_date(connection, card_id: str, reviewed_on: date) -> bool:
    row = connection.execute(
        """
        select 1
        from reviews
        where card_id = ?
          and substr(reviewed_at, 1, 10) = ?
        limit 1
        """,
        (card_id, reviewed_on.isoformat()),
    ).fetchone()
    return row is not None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_enrichment_provider():
    source = os.environ.get("VOCAB_ENRICHMENT_SOURCE", "oxford").lower()
    if source == "fallback":
        return FallbackEnrichmentProvider()
    return OxfordEnrichmentProvider()
