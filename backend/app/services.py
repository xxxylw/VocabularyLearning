from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
import json
import os
from uuid import uuid4

from app.books import (
    book_exists,
    get_current_book_id,
    read_current_book_pointer,
    resolve_current_book,
    set_current_book_pointer,
)
from app.db import connect
from app.enrichment import FallbackEnrichmentProvider, OxfordEnrichmentProvider
from app.models import (
    BookListItemResponse,
    BookListResponse,
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


def _book_progress_aggregates(
    connection, book_id: str, user_id: str
) -> tuple[int, int, int]:
    """Per-book progress aggregates (PRD ch.9) for one user: total words,
    learned words (the word has at least one review by this user),
    mastered words (every card of the word is mastered and it has at
    least one card)."""
    total_row = connection.execute(
        "select count(*) as total from book_words where book_id = ?",
        (book_id,),
    ).fetchone()
    learned_row = connection.execute(
        """
        select count(distinct book_words.normalized_text) as total
        from book_words
        where book_words.book_id = ?
          and exists (
            select 1
            from reviews
            join cards on cards.id = reviews.card_id
            join entries on entries.id = cards.entry_id
            join words on words.id = entries.word_id
            where words.normalized_text = book_words.normalized_text
              and cards.user_id = ?
          )
        """,
        (book_id, user_id),
    ).fetchone()
    mastered_row = connection.execute(
        """
        select count(*) as total
        from (
            select distinct book_words.normalized_text
            from book_words
            where book_words.book_id = ?
              and exists (
                select 1
                from entries
                join words on words.id = entries.word_id
                join cards on cards.entry_id = entries.id
                where words.normalized_text = book_words.normalized_text
                  and cards.user_id = ?
              )
              and not exists (
                select 1
                from entries
                join words on words.id = entries.word_id
                join cards on cards.entry_id = entries.id
                where words.normalized_text = book_words.normalized_text
                  and cards.user_id = ?
                  and cards.status <> 'mastered'
              )
        )
        """,
        (book_id, user_id, user_id),
    ).fetchone()
    return total_row["total"], learned_row["total"], mastered_row["total"]


def _book_summary_response(
    connection, book_row, user_id: str, fallback_notice: str | None = None
) -> BookSummaryResponse:
    total, learned, mastered = _book_progress_aggregates(
        connection, book_row["id"], user_id
    )
    return BookSummaryResponse(
        id=book_row["id"],
        title=book_row["title"],
        description=book_row["description"],
        source=book_row["source"],
        createdAt=book_row["created_at"],
        updatedAt=book_row["updated_at"],
        totalWords=total,
        learnedWords=learned,
        masteredWords=mastered,
        fallbackNotice=fallback_notice,
    )


def get_current_book(user_id: str) -> BookSummaryResponse:
    with connect() as connection:
        book_row, fallback = resolve_current_book(connection, user_id)
        notice = (
            f"当前书不存在，已回退默认书「{book_row['title']}」"
            if fallback
            else None
        )
        return _book_summary_response(connection, book_row, user_id, notice)


def list_books(user_id: str) -> BookListResponse:
    with connect() as connection:
        current_book_row, _fallback = resolve_current_book(connection, user_id)
        current_book_id = str(current_book_row["id"])
        book_rows = connection.execute(
            "select * from vocabulary_books order by created_at, id"
        ).fetchall()
        books = [
            BookListItemResponse(
                **_book_summary_response(connection, row, user_id).model_dump(),
                isCurrent=str(row["id"]) == current_book_id,
            )
            for row in book_rows
        ]
    return BookListResponse(books=books)


def switch_current_book(user_id: str, book_id: str) -> BookSummaryResponse:
    """Switch the current book (PRD ch.9): only the caller's own
    current-book pointer is updated — no review / scheduling / progress /
    snapshot data is touched, and no other user's pointer moves.
    Switching to the already-current book is an idempotent no-op."""
    with connect() as connection:
        if not book_exists(connection, book_id):
            raise LookupError(f"Book not found: {book_id}")
        pointer = read_current_book_pointer(connection, user_id)
        if pointer != book_id:
            set_current_book_pointer(connection, user_id, book_id)
        book_row = connection.execute(
            "select * from vocabulary_books where id = ?",
            (book_id,),
        ).fetchone()
        return _book_summary_response(connection, book_row, user_id)


def prepare_book_words(
    user_id: str, request: PrepareJobRequest, *, is_super: bool = False
) -> PrepareJobResponse:
    """Prepare the next words of a book for one user (C-06).

    Enrichment is global and shared: entries / examples are created once
    per word and reused by every user, so a second user studying the
    same book never triggers another Oxford call. Cards are per-user:
    every user gets their own card row per entry (unique on
    (user_id, entry_id)).

    ``overwriteExisting`` re-enriches the *shared* word material and
    therefore deletes every user's cards for those words — it is a
    maintenance operation restricted to the super account (C-07 data
    boundary: a regular user must not be able to destroy another user's
    study data).
    """
    if request.scope != "next":
        raise ValueError("Only scope='next' is supported")
    if request.overwriteExisting and not is_super:
        raise PermissionError("overwriteExisting requires the super account")

    count = request.count if request.count is not None else 20
    max_senses = max(request.maxSensesPerWord, 1)
    now = _utc_now()
    today = date.today().isoformat()
    provider = _create_enrichment_provider()

    with connect() as connection:
        if request.bookId:
            # PRD ch.10: batch jobs target a specific book without touching
            # the current-book pointer (prepare ≠ switch).
            if not book_exists(connection, request.bookId):
                raise LookupError(f"Book not found: {request.bookId}")
            book_id = request.bookId
        else:
            book_id = get_current_book_id(connection, user_id)
        book_words = connection.execute(
            """
            select id, word_text, normalized_text
            from book_words
            where book_id = ?
              and (
                -- Baseline semantics (kept): pending / needs_review words
                -- are re-selectable so a flagged word can be re-processed
                -- (and marked back to ready) even by a user who already
                -- owns a card of it.
                import_status in ('pending', 'needs_review')
                or not exists (
                    -- Per-user selection (C-06): a word also counts as
                    -- "not yet prepared" for THIS user when they own no
                    -- card of it. import_status is a shared enrichment
                    -- flag: once one user prepared a word, everyone else
                    -- still gets their own cards from the shared entries.
                    select 1
                    from entries
                    join words on words.id = entries.word_id
                    join cards on cards.entry_id = entries.id
                    where words.normalized_text = book_words.normalized_text
                      and cards.user_id = ?
                )
              )
            order by sequence_index
            limit ?
            """,
            (book_id, user_id, count),
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

            if _word_card_count(connection, word_id, user_id) > 0:
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

            # Shared enrichment layer: only call the provider when the
            # word has no entries yet. A second user of the same word
            # reuses the existing entries and just gets their own cards.
            entry_rows = connection.execute(
                "select id from entries where word_id = ? order by sense_order",
                (word_id,),
            ).fetchall()

            if not entry_rows:
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
                entry_rows = connection.execute(
                    "select id from entries where word_id = ? order by sense_order",
                    (word_id,),
                ).fetchall()

            # Per-user cards: this user has none for the word yet
            # (checked above), so create one card per entry.
            for entry_row in entry_rows:
                connection.execute(
                    """
                    insert into cards (
                        id,
                        user_id,
                        entry_id,
                        status,
                        stage,
                        due_at,
                        created_on,
                        last_reviewed_at,
                        ef,
                        interval_days
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        user_id,
                        entry_row["id"],
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


def start_today_session(user_id: str, request: TodayStartRequest) -> TodaySessionResponse:
    study_date = request.date or date.today()

    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        snapshot_exists = _today_queue_snapshot_exists(
            connection, user_id, book_id, study_date
        )

    if not snapshot_exists:
        # 每日首次进入 Today：生成当天固定队列快照（复习卡在前 + 新卡在后）。
        _create_today_queue_snapshot(user_id, study_date, request.dailyNewWordTarget)
    else:
        # 快照已存在：当日不重算，只把额度内新 prepare 就绪的新卡追加到队尾。
        _merge_new_cards_into_today_queue(user_id, study_date, request.dailyNewWordTarget)

    return _read_today_queue_session(user_id, study_date)


def _today_queue_snapshot_exists(
    connection, user_id: str, book_id: str, study_date: date
) -> bool:
    row = connection.execute(
        "select 1 from today_queue_snapshots"
        " where user_id = ? and book_id = ? and study_date = ? limit 1",
        (user_id, book_id, study_date.isoformat()),
    ).fetchone()
    return row is not None


def _create_today_queue_snapshot(
    user_id: str, study_date: date, daily_new_word_target: int
) -> None:
    review_cards = sorted(
        # PRD ch.8 rule 2: review cards by due_at ascending (overdue
        # first); the stable sort keeps the book-sequence order as the
        # tie-breaker for cards sharing a due date.
        _get_due_review_cards(study_date, user_id),
        key=lambda card: card.dueAt,
    )

    new_word_target_remaining = max(
        daily_new_word_target - _count_new_words_studied_on(user_id, study_date),
        0,
    )
    new_cards = (
        _get_due_new_cards(study_date, new_word_target_remaining, user_id)
        if new_word_target_remaining > 0
        else []
    )
    if len(new_cards) < new_word_target_remaining:
        prepare_book_words(
            user_id,
            PrepareJobRequest(
                scope="next",
                count=new_word_target_remaining - len(new_cards),
                maxSensesPerWord=5,
                overwriteExisting=False,
            ),
        )
        new_cards = _get_due_new_cards(study_date, new_word_target_remaining, user_id)

    # A word whose senses span both the review and the new pool is queued
    # once, as a review card (its primary card id matches on both sides).
    review_card_ids = {card.cardId for card in review_cards}
    new_cards = [card for card in new_cards if card.cardId not in review_card_ids]

    _append_today_queue_rows(
        user_id,
        study_date,
        review_cards=review_cards,
        new_cards=new_cards,
        create_snapshot=True,
    )


def _merge_new_cards_into_today_queue(
    user_id: str, study_date: date, daily_new_word_target: int
) -> None:
    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        queued_new_card_ids = {
            row["card_id"]
            for row in connection.execute(
                "select card_id from today_queue"
                " where user_id = ? and book_id = ? and study_date = ?"
                " and queue_type = 'new'",
                (user_id, book_id, study_date.isoformat()),
            ).fetchall()
        }
        reviewed_queued_new = connection.execute(
            """
            select count(*) as total
            from today_queue
            where user_id = ? and book_id = ? and study_date = ?
              and queue_type = 'new'
              and exists (
                select 1 from reviews
                where reviews.card_id = today_queue.card_id
                  and substr(reviews.reviewed_at, 1, 10) = ?
              )
            """,
            (user_id, book_id, study_date.isoformat(), study_date.isoformat()),
        ).fetchone()["total"]

    # Quota consumed today = distinct new words studied (reviews) UNION
    # queued new entries; a queued new entry already reviewed today is in
    # both sets, hence the subtraction below (PRD ch.8 rule 7).
    studied_new = _count_new_words_studied_on(user_id, study_date)
    remaining = max(
        daily_new_word_target
        - studied_new
        - (len(queued_new_card_ids) - reviewed_queued_new),
        0,
    )
    if remaining <= 0:
        return

    candidates = _get_due_new_cards(
        study_date, remaining + len(queued_new_card_ids), user_id
    )
    fresh_cards = [
        card for card in candidates if card.cardId not in queued_new_card_ids
    ]
    if len(fresh_cards) < remaining:
        # The quota grew mid-day but the pool has no ready new cards left:
        # prepare the missing words, mirroring the snapshot-creation path.
        prepare_book_words(
            user_id,
            PrepareJobRequest(
                scope="next",
                count=remaining - len(fresh_cards),
                maxSensesPerWord=5,
                overwriteExisting=False,
            ),
        )
        candidates = _get_due_new_cards(
            study_date, remaining + len(queued_new_card_ids), user_id
        )
        fresh_cards = [
            card for card in candidates if card.cardId not in queued_new_card_ids
        ]
    fresh_cards = fresh_cards[:remaining]
    if not fresh_cards:
        return

    _append_today_queue_rows(
        user_id,
        study_date,
        review_cards=[],
        new_cards=fresh_cards,
        create_snapshot=False,
    )


def _append_today_queue_rows(
    user_id: str,
    study_date: date,
    review_cards: list[StudyCardResponse],
    new_cards: list[StudyCardResponse],
    create_snapshot: bool,
) -> None:
    entries = [(card, "review") for card in review_cards]
    entries += [(card, "new") for card in new_cards]
    now = _utc_now()

    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        if create_snapshot:
            connection.execute(
                "insert or ignore into today_queue_snapshots"
                " (user_id, book_id, study_date, created_at) values (?, ?, ?, ?)",
                (user_id, book_id, study_date.isoformat(), now),
            )
        row = connection.execute(
            "select coalesce(max(position), 0) as next_position"
            " from today_queue"
            " where user_id = ? and book_id = ? and study_date = ?",
            (user_id, book_id, study_date.isoformat()),
        ).fetchone()
        position = row["next_position"] + 1
        for card, queue_type in entries:
            connection.execute(
                """
                insert into today_queue (
                    id, user_id, book_id, study_date, position, card_id,
                    queue_type, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    user_id,
                    book_id,
                    study_date.isoformat(),
                    position,
                    card.cardId,
                    queue_type,
                    now,
                ),
            )
            position += 1


def _read_today_queue_session(user_id: str, study_date: date) -> TodaySessionResponse:
    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        queue_rows = connection.execute(
            "select card_id, position, queue_type from today_queue"
            " where user_id = ? and book_id = ? and study_date = ? order by position",
            (user_id, book_id, study_date.isoformat()),
        ).fetchall()
        if not queue_rows:
            return TodaySessionResponse(totalCards=0, cards=[], reviewedCards=0)

        queue_card_ids = [row["card_id"] for row in queue_rows]
        placeholders = ", ".join("?" for _ in queue_card_ids)
        existing_ids = {
            row["card_id"]
            for row in connection.execute(
                f"select id as card_id from cards where id in ({placeholders})",
                tuple(queue_card_ids),
            ).fetchall()
        }
        reviewed_ids = {
            row["card_id"]
            for row in connection.execute(
                f"""
                select distinct card_id
                from reviews
                where card_id in ({placeholders})
                  and substr(reviewed_at, 1, 10) = ?
                """,
                (*queue_card_ids, study_date.isoformat()),
            ).fetchall()
        }

        total_cards = sum(1 for card_id in queue_card_ids if card_id in existing_ids)
        reviewed_cards = sum(
            1
            for card_id in queue_card_ids
            if card_id in existing_ids and card_id in reviewed_ids
        )
        pending_rows = [
            row
            for row in queue_rows
            if row["card_id"] in existing_ids and row["card_id"] not in reviewed_ids
        ]

        cards: list[StudyCardResponse] = []
        if pending_rows:
            pending_card_ids = [row["card_id"] for row in pending_rows]
            pending_placeholders = ", ".join("?" for _ in pending_card_ids)
            pending_words = [
                row["normalized_text"]
                for row in connection.execute(
                    f"""
                    select distinct words.normalized_text
                    from cards
                    join entries on entries.id = cards.entry_id
                    join words on words.id = entries.word_id
                    where cards.id in ({pending_placeholders})
                    """,
                    tuple(pending_card_ids),
                ).fetchall()
            ]
            word_placeholders = ", ".join("?" for _ in pending_words)
            due_rows = connection.execute(
                f"""
                select
                    cards.id as card_id,
                    cards.last_reviewed_at,
                    words.normalized_text
                from cards
                join entries on entries.id = cards.entry_id
                join words on words.id = entries.word_id
                where cards.due_at <= ?
                  and cards.user_id = ?
                  and cards.status in ('new', 'learning', 'mastered')
                  and words.normalized_text in ({word_placeholders})
                """,
                (study_date.isoformat(), user_id, *pending_words),
            ).fetchall()
            study_cards = _study_cards_from_rows(connection, due_rows, user_id)
            cards_by_id = {card.cardId: card for card in study_cards}
            for row in pending_rows:
                card = cards_by_id.get(row["card_id"])
                if card is None:
                    continue
                card.queueType = row["queue_type"]
                card.queuePosition = row["position"]
                cards.append(card)

        return TodaySessionResponse(
            totalCards=total_cards,
            cards=cards,
            reviewedCards=reviewed_cards,
        )


def get_due_reviews(user_id: str, due_date: date) -> DueReviewsResponse:
    cards = _get_due_study_cards(due_date, None, user_id)
    return DueReviewsResponse(date=due_date, total=len(cards), cards=cards)


def review_card(
    user_id: str, card_id: str, request: ReviewCardRequest
) -> ReviewCardResponse:
    reviewed_on = request.reviewedDate or request.reviewedAt.date()
    reviewed_at = request.reviewedAt.isoformat()

    with connect() as connection:
        # BEGIN IMMEDIATE acquires the write lock up front so the
        # read-check-write sequence below cannot race a concurrent review
        # of the same card (QA F-01 finding, fixed in v2 batch 2).
        connection.execute("BEGIN IMMEDIATE")
        card = connection.execute(
            "select id, stage, status, due_at, ef, interval_days"
            " from cards where id = ? and user_id = ?",
            (card_id, user_id),
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
                user_id,
                card_id,
                rating,
                reviewed_at,
                previous_stage,
                next_stage,
                next_due_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                user_id,
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


def _word_card_count(connection, word_id: str, user_id: str) -> int:
    row = connection.execute(
        """
        select count(*) as total
        from entries
        join cards on cards.entry_id = entries.id
        where entries.word_id = ?
          and cards.user_id = ?
        """,
        (word_id, user_id),
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
    user_id: str,
) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="1 = 1",
        limit=limit,
        user_id=user_id,
    )


def _get_due_review_cards(due_date: date, user_id: str) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="cards.last_reviewed_at is not null",
        limit=None,
        user_id=user_id,
    )


def _get_due_new_cards(
    due_date: date,
    limit: int,
    user_id: str,
) -> list[StudyCardResponse]:
    return _get_due_study_cards_by_queue(
        due_date=due_date,
        queue_condition="cards.last_reviewed_at is null",
        limit=limit,
        user_id=user_id,
    )


def _get_due_study_cards_by_queue(
    due_date: date,
    queue_condition: str,
    limit: int | None,
    user_id: str,
) -> list[StudyCardResponse]:
    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
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
              and cards.user_id = ?
              and cards.status in ('new', 'learning', 'mastered')
              and exists (
                  -- PRD ch.9: after switching books the study pool only
                  -- contains cards of words that belong to the current
                  -- book, so the other book's cards never leak in.
                  select 1
                  from book_words
                  where book_words.normalized_text = words.normalized_text
                    and book_words.book_id = ?
              )
              and {queue_condition}
            order by
                case when book_sequence_index is null then 1 else 0 end,
                book_sequence_index,
                entries.sense_order,
                cards.due_at,
                cards.created_on,
                words.text
            """,
            (book_id, due_date.isoformat(), user_id, book_id),
        ).fetchall()

        if not card_rows:
            return []

        cards = _study_cards_from_rows(connection, card_rows, user_id)
        return cards if limit is None else cards[:limit]


def _count_new_words_studied_on(user_id: str, study_date: date) -> int:
    with connect() as connection:
        book_id = get_current_book_id(connection, user_id)
        row = connection.execute(
            """
            select count(*) as total
            from (
                select words.normalized_text
                from reviews
                join cards on cards.id = reviews.card_id
                join entries on entries.id = cards.entry_id
                join words on words.id = entries.word_id
                where reviews.user_id = ?
                  and substr(reviews.reviewed_at, 1, 10) = ?
                  and exists (
                      -- PRD ch.9: the daily new-word quota is tracked per
                      -- book — a word of another book never consumes the
                      -- current book's quota.
                      select 1
                      from book_words
                      where book_words.normalized_text = words.normalized_text
                        and book_words.book_id = ?
                  )
                  and not exists (
                    select 1
                    from reviews previous_reviews
                    join cards previous_cards on previous_cards.id = previous_reviews.card_id
                    join entries previous_entries on previous_entries.id = previous_cards.entry_id
                    where previous_reviews.user_id = ?
                      and previous_entries.word_id = entries.word_id
                      and substr(previous_reviews.reviewed_at, 1, 10) < ?
                  )
                group by words.normalized_text
            )
            """,
            (user_id, study_date.isoformat(), book_id, user_id, study_date.isoformat()),
        ).fetchone()

    return row["total"]


def _study_cards_from_rows(
    connection, card_rows, user_id: str
) -> list[StudyCardResponse]:
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
    book_id = get_current_book_id(connection, user_id)
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
          and cards.user_id = ?
          and cards.status in ('new', 'learning', 'mastered')
        order by
            case when book_sequence_index is null then 1 else 0 end,
            book_sequence_index,
            entries.sense_order,
            cards.due_at,
            cards.created_on,
            words.text
        """,
        (book_id, *normalized_words, user_id),
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
