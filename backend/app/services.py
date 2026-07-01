from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import uuid4

from app.db import connect
from app.enrichment import FallbackEnrichmentProvider
from app.models import PrepareJobRequest, PrepareJobResponse
from app.repositories import normalize_word


def prepare_book_words(request: PrepareJobRequest) -> PrepareJobResponse:
    if request.scope != "next":
        raise ValueError("Only scope='next' is supported")

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
