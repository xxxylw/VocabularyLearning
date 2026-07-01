from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import uuid4

from app.db import connect
from app.enrichment import FallbackEnrichmentProvider
from app.models import (
    DueReviewsResponse,
    ExportFullBookRequest,
    ExportFullBookResponse,
    ExportReadinessError,
    PrepareJobRequest,
    PrepareJobResponse,
    ReviewCardRequest,
    ReviewCardResponse,
    StudyCardResponse,
    StudyExampleResponse,
    TodaySessionResponse,
    TodayStartRequest,
)
from app.repositories import normalize_word
from app.scheduling import transition


class ReviewConflictError(ValueError):
    pass


class ExportNotReadyError(ValueError):
    def __init__(self, readiness: ExportReadinessError):
        super().__init__("Full book export is not ready")
        self.readiness = readiness


def prepare_book_words(request: PrepareJobRequest) -> PrepareJobResponse:
    if request.scope != "next":
        raise ValueError("Only scope='next' is supported")
    if request.overwriteExisting:
        raise ValueError("overwriteExisting=true is not supported yet")

    count = request.count if request.count is not None else 20
    max_senses = max(request.maxSensesPerWord, 1)
    now = _utc_now()
    today = date.today().isoformat()
    provider = FallbackEnrichmentProvider()

    with connect() as connection:
        book_words = connection.execute(
            """
            select id, word_text, normalized_text
            from book_words
            where import_status in ('pending', 'needs_review')
            order by sequence_index
            limit ?
            """,
            (count,),
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
                        "fallback",
                        sense.chinese_note,
                        now,
                        now,
                    ),
                )
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
                        "fallback",
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
                        last_reviewed_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), entry_id, "learning", 0, today, today, None),
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
    cards = _get_due_review_cards(study_date) + _get_due_new_cards(
        study_date,
        request.dailyNewWordTarget,
    )
    return TodaySessionResponse(totalCards=len(cards), cards=cards)


def get_due_reviews(due_date: date) -> DueReviewsResponse:
    cards = _get_due_study_cards(due_date, None)
    return DueReviewsResponse(date=due_date, total=len(cards), cards=cards)


def review_card(card_id: str, request: ReviewCardRequest) -> ReviewCardResponse:
    reviewed_on = request.reviewedDate or request.reviewedAt.date()
    reviewed_at = request.reviewedAt.isoformat()

    with connect() as connection:
        card = connection.execute(
            "select id, stage, due_at from cards where id = ?",
            (card_id,),
        ).fetchone()
        if card is None:
            raise LookupError("Card not found")
        if date.fromisoformat(card["due_at"]) > reviewed_on:
            raise ReviewConflictError("Card is not due on the reviewed date")
        if _review_exists_on_date(connection, card_id, reviewed_on):
            raise ReviewConflictError("Card was already reviewed on this date")

        previous_stage = card["stage"]
        next_stage, next_due_at, status = transition(
            previous_stage,
            request.rating,
            reviewed_on,
        )

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
                next_due_at.isoformat(),
            ),
        )
        connection.execute(
            """
            update cards
            set status = ?,
                stage = ?,
                due_at = ?,
                last_reviewed_at = ?
            where id = ?
            """,
            (
                status,
                next_stage,
                next_due_at.isoformat(),
                reviewed_at,
                card_id,
            ),
        )

    return ReviewCardResponse(
        cardId=card_id,
        rating=request.rating,
        previousStage=previous_stage,
        nextStage=next_stage,
        nextDueAt=next_due_at,
        status=status,
    )


def export_full_book_anki(
    request: ExportFullBookRequest,
) -> ExportFullBookResponse:
    with connect() as connection:
        readiness = _get_full_book_export_readiness(connection)
        if readiness.totalWords == 0 or readiness.missingWords > 0:
            raise ExportNotReadyError(readiness)

        card_count = _get_full_book_export_card_count(connection)

    return ExportFullBookResponse(
        downloadUrl=f"/api/export/anki/files/{_slugify_deck_name(request.deckName)}.apkg",
        cardCount=card_count,
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


def _get_full_book_export_readiness(connection) -> ExportReadinessError:
    total_row = connection.execute(
        "select count(*) as total from book_words"
    ).fetchone()
    prepared_row = connection.execute(
        """
        select count(distinct book_words.normalized_text) as total
        from book_words
        join words on words.normalized_text = book_words.normalized_text
        join entries on entries.word_id = words.id
        join cards on cards.entry_id = entries.id
        """
    ).fetchone()

    total_words = total_row["total"]
    prepared_words = prepared_row["total"]
    return ExportReadinessError(
        totalWords=total_words,
        preparedWords=prepared_words,
        missingWords=max(total_words - prepared_words, 0),
    )


def _get_full_book_export_card_count(connection) -> int:
    row = connection.execute(
        """
        select count(distinct cards.id) as total
        from book_words
        join words on words.normalized_text = book_words.normalized_text
        join entries on entries.word_id = words.id
        join cards on cards.entry_id = entries.id
        """
    ).fetchone()
    return row["total"]


def _slugify_deck_name(deck_name: str) -> str:
    slug = "".join(
        character.lower() if character.isalnum() else "-"
        for character in deck_name.strip()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "full-book"


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
    limit_clause = "" if limit is None else "limit ?"
    params: tuple[object, ...]
    params = (
        (due_date.isoformat(),)
        if limit is None
        else (due_date.isoformat(), limit)
    )

    with connect() as connection:
        card_rows = connection.execute(
            f"""
            select
                cards.id as card_id,
                cards.status,
                cards.stage,
                cards.due_at,
                cards.last_reviewed_at,
                words.text as word,
                entries.part_of_speech,
                entries.sense_label,
                entries.definition,
                entries.chinese_note
            from cards
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where cards.due_at <= ?
              and cards.status in ('new', 'learning', 'mastered')
              and {queue_condition}
            order by cards.due_at, cards.created_on, words.text, entries.sense_order
            {limit_clause}
            """,
            params,
        ).fetchall()

        if not card_rows:
            return []

        return _study_cards_from_rows(connection, card_rows)


def _study_cards_from_rows(connection, card_rows) -> list[StudyCardResponse]:
    card_ids = [row["card_id"] for row in card_rows]
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

    return [
        StudyCardResponse(
            cardId=row["card_id"],
            word=row["word"],
            partOfSpeech=row["part_of_speech"],
            senseLabel=row["sense_label"],
            definition=row["definition"],
            examples=examples_by_card[row["card_id"]],
            chineseNote=row["chinese_note"],
            status=row["status"],
            stage=row["stage"],
            dueAt=date.fromisoformat(row["due_at"]),
            queueType="new" if row["last_reviewed_at"] is None else "review",
        )
        for row in card_rows
    ]


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
